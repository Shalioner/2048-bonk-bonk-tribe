# Порт n-tuple TD для Bonk

Реализованы `TDModel`, отдельный inference-header и нативный trainer по
[согласованному API](TD-PORT-API.md). Default бота и старый MC-модуль не меняются.
Нейтральная необученная модель возвращает 0.5 для обычных состояний; это не
утверждение о её силе или калибровке вероятности.

## Что перенесено

Донор: moporgic/TDL2048, commit
`a99f620aec0d30a75943a4c9646743f1f53b0197`; оригиналы находятся в
`vendor/tdl2048-original`. SHA256 исходного `2048.cpp`:
`936e045167e2d168ad45095194ebd12ffc7d7aa79d4fe335dafe119d9a1bdaff`.
Из `method::isomorphic::estimate/optimize` (строки 1510–1538) перенесено
суммирование активных lookup и обновление **каждого** D4-вхождения, включая
повторяющиеся индексы. Из `state::instruct` (1771–1776) перенесена коррекция
`alpha * (target - estimate)`; из `run optimize:forward` (2037–2057) —
последовательное обновление предыдущего afterstate по оценке выбранного
следующего afterstate и финальному исходу. Это перенос кода алгоритма с
изменением задачи, а не запуск upstream игры или её готовых весов.

В трёх адаптированных файлах сохранён полный MIT copyright/permission/warranty
текст: Copyright (c) 2021 Hung Guei. Исходные vendor-файлы не изменены.
Изменения перечислены в заголовках файлов. Bonk сохраняет CAP11/GOAL3;
награда на обычном переходе 0, discount 1, terminal goal 1, loss 0.
Четыре 4-клеточных паттерна × восемь D4 используют те же клетки и индексы,
что и `value_model.features`. Весовые таблицы разделены по 0/1/2 плиткам11.

## Оценка и обновление

В неоконченном состоянии `raw = bias[stage] + sum(32 weights)/8`.
Сумма float64 затем округляется в float32 и ограничивается
`[float32(1e-6), float32(1)-float32(1e-6)]`. Goal/loss всегда точно 1/0.
При обучении `error = float32(alpha * float32(target - prediction))`;
каждое активное вхождение получает `float32(error/4)`, последовательно.
Bias не обновляется. Терминальные поля не обучаются. Повторные индексы
увеличиваются повторно, что существенно на симметричных и разреженных полях.

`bonk_td::Model` предоставляет `loaded/load/clear/evaluate`, а для trainer:
`reset`, `parameter_count`, `set_parameters`, `get_parameters`, `update`.
Незагруженный evaluate возвращает NaN. Константная оценка не пишет память;
load/clear/update разрешены только вне параллельного inference.

## Файл BONKTD01

Детерминированный little-endian header `<8s10I`, 48 байт:
magic `BONKTD01`, version1, header_size48, metadata_size, stages3,
patterns4, table_size65536, cap11, goal_count3, target_type2, CRC32.
CRC32 покрывает metadata+payload. Далее канонический UTF-8 JSON
(sort_keys, compact, без NaN/Inf), затем 786435 float32: bias3, после него
weights[3,4,65536] в C-order. Payload: 3145740 байт.
JSON содержит фиксированную архитектуру, правила и целевую семантику,
точный donor, SHA256 payload, training и дополнительные metadata.
Python проверяет все эти поля, CRC, SHA, размер и конечность параметров.
Старые BONKVAL1/target1 взаимно отвергаются.

C++ независимо проверяет фиксированный header/target2/правила/размер,
CRC32 и конечность всех параметров; metadata остаётся opaque. При загрузке
через бота/эксперимент Python-валидация должна предшествовать native load.
C++ load транзакционный: сбой оставляет прежние веса и loaded-state.
Python save пишет временный файл рядом с целью и заменяет его атомарно.

## Trainer, RNG и транзакции

Сборка только явно: `build_trainer(path)` возвращает Path к библиотеке.
Import и `NativeTrainer(library_path)` ничего не пересобирают. Trainer включает
локальный `engine_adapted.cpp`; движение, цель, teacher search и таблица
появлений берутся именно из него, а не из donor board.h.
`init_tables` исполняется один раз на библиотеку. Отдельные handles хранят
собственные параметры и spawn table. Доступ к общим engine globals
сериализован process-global mutex; при каждом episode восстанавливается
таблица данного handle. Это не многопоточное ускорение обучения.

Каждый episode получает свой SplitMix64 с исходным uint64 seed и стандартными
константами 9e3779b97f4a7c15, bf58476d1ce4e5b9, 94d049bb133111eb.
Пустая клетка выбирается unbiased rejection/modulo sampling; вероятность rank
и epsilon — по старшим 53 битам `next()/2^53`. Rank выбирается по таблице
для **максимального уровня после движения**. Без start_board выполняются две
spawn, с ним дополнительных initial spawn нет. Teacher всегда depth1,
ручная эвристика и model-off; selfplay — greedy learned afterstate с epsilon.
Tie order U,D,L,R (нативные индексы 0,1,2,3).

TD0 обновляет previous afterstate по ещё не обновлённой оценке next;
последний previous получает точный терминальный исход. MC хранит afterstates
и обновляет их только после goal/loss. Observe не копирует модель и не
обновляет веса. Goal после движения завершает партию без spawn; goal,
достигнутый самим spawn по кастомной таблице, тоже завершает партию.

При первой попытке обновления сохраняется резервная копия модели.
Limit полностью откатывает episode; updates=0, attempted_updates показывает
число отменённых TD updates. MC limit не получает ложную метку loss.
Исключение также откатывает параметры. Невалидные аргументы и terminal
start_board отклоняются до изменения параметров; буферы/параметры/таблица
проверяются как в Python, так и в C API.

`first_caps['1'/'2']` — первое состояние с **точным** cap count после spawn
(либо само явно заданное начальное поле), до следующего решения игрока;
только nonterminal с legal move. Терминальные snapshots исключены.
Trajectory digest — FNV-1a64 (offset14695981039346656037, prime1099511628211)
по записям `(tag byte, uint64 little-endian value)`: tag0 initial board,
tags1..4 afterstate с direction0..3, tag5 spawned board, tag6 outcome
(0 loss/1 goal/2 limit). Он не содержит model/algorithm/weights, поэтому
фиксированный teacher имеет общий digest у MC/TD0/observe.

`set_model/model` передают float32 параметры побитно и копируют metadata и
training. Счётчики/историю обучения runner дописывает явно: trainer не
выдаёт изменившиеся веса за уже проверенную или калиброванную модель.

## Проверки и границы

Команда: `python3 -m unittest -v test_td_model.py test_td_train.py`.
11 коротких тестов, последний прогон: **OK, 3.815 s**. Native библиотеки
собирались только во временных лабораториях и удаляются с TemporaryDirectory.
Проверены все стадии/D4, побитовое Python/C++ равенство оценок и весов после
нескольких updates, формула повторных вхождений, clipping, terminal overrides,
детерминированный file roundtrip, CRC/metadata/finite/target rejection,
transactional load, независимые handles и перенос checkpoint, одинаковые
teacher trajectories при разных весах, goal/no-spawn, stochastic goal,
реальный no-legal loss, observe без записи и TD rollback при limit.
Первый прогон выявил ошибку ожидания в тесте first_caps: начальный legal
snapshot корректно сохранялся; ожидание исправлено, продукт не менялся.

Реальные большие партии, обучение, validation/holdout и сравнение win rate
не запускались. Скорость и польза TD для побед пока не измерены. Полная
интеграция optional inference и общие gates принадлежат координатору.
