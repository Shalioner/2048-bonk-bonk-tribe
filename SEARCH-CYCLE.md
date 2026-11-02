# Протокол альтернатив поиска без ML

Новый runner: `experiments/search_alternatives/run.py`. Исходная ветка протокола:
`01b659fa734d1aa018223377a9fa77602958c046`. Старые geometry/math/TD файлы и
данные не изменяются. Здесь нет fit, ML или установки нового default.
Сохранены reviewed atomic/flock/resume, relative fingerprint, end-build
fingerprint и точный обязательный набор freeze-хешей, включая raw screen.

## Пять заранее объявленных arms

Pinned native core: `e28aad01f8c8e3aa725e41640625e5fda7a02701`, SHA256
`257050c392bf3a743f894b0f674ff8d7da5c8f2165e2dff003f0e3b4192cb24d`.
Генератор `variants.py` имеет контракт `variant_source(base_bytes, arm) -> bytes`.
Он читается лениво из текущих bytes, без старого import-cache/pyc; его файл,
`search_variants.h` и все прежние vendor headers входят в source fingerprint.
Baseline, precision и banked_endgame возвращают исходные bytes core дословно.
Фактическую реализацию canonical/serial_shallow проверяет исполнитель native
вариантов; mock-тесты runner не доказывают полезность этих изменений.

| Round | Arm | Thresholds | Budget profile | Эффект и цена |
|---|---|---|---|---|
| 0 | baseline | 1e-4, 1e-5, 1e-6 | fixed | Сохранённая линия сравнения. |
| 1 | canonical | 1e-4, 1e-5, 1e-6 | fixed | D4-канонизация chance-cache после существующего leaf cutoff, включая reference path; больше повторного использования ценой преобразования ключа. |
| 2 | serial_shallow | 1e-4, 1e-5, 1e-6 | fixed | Root passes depth<=2 последовательно, более глубокие параллельно; меньше launch/synchronization, но теряется мелкий параллелизм. |
| 3 | precision | 1e-5, 1e-6 | fixed | Тот же native core, более строгий первый pruning pass; возможны более точная оценка и меньшая завершённая глубина. |
| 4 | banked_endgame | 1e-4, 1e-5, 1e-6 | banked_endgame | Тот же native core/эвристика/физика, перенос накопленного времени в эндшпиль. Это отдельный ресурсный режим с cap до 40 ms, его эффект нельзя приписывать одной точности поиска при прежнем per-call 10 ms cap. |

Перед **каждой** партией configure явно задаёт `bot.THRESHOLDS` из source_arm.
Raw thresholds сохраняют фактический Python schedule и проверяются против него,
включая случай control label=finalist при native source, совпадающем с baseline.
DEFAULT_THINK=10s, телефон/runtime и production core не меняются.

## Бюджет с переносом остатка

Чистые функции `allocate_budget(profile, credit, caps)` и
`charge_budget(credit, elapsed)` не читают plan и пригодны для отдельного
опционального entrypoint без правки замороженного runner/core.
Fixed profile всегда передаёт 0.01 s, credit=None. Bank начинает каждую партию
с credit=0. Перед каждым choose: credit+=0.01; cap=0.01 при 0/1 CAP и 0.04
при **ровно двух** CAP; budget=min(cap,max(0,credit)). После choose списывается
измеренный внешний wall. Отрицательный остаток сохраняется и погашается
будущими allowances; unused credit не переносится в следующую партию.

0-budget может вызвать fallback и иметь положительную фактическую стоимость.
Поэтому нет обещания строгого prefix-wall cap: OS/fallback overshoot виден
как долг. Native hard deadline применяется к **переданному** budget без
ослабления; старые budget+50ms unit gates остаются прежними. Профиль bank
не обозначается как тот же per-call 10 ms cap. Даже при ожидаемой высокой доле
вызовов дольше nominal 0.01 s все наблюдения сохраняются.

## Телеметрия и её проверка

Checkpoint/summary содержат measurement_schema=1. Каждая raw партия хранит:
thresholds и budget_profile; wall_samples, budget_samples, decision_caps;
credit_balance и search_cpu_seconds. Три массива имеют len=search_calls,
включая завершающий choose=None. sum(wall_samples) точно равна
search_wall_seconds. CPU измеряется process_time вокруг choose и включает
нативные threads; это сумма CPU, которая может превышать wall.

Budget replay использует сохранённые wall и cap counts, абсолютный допуск 1e-10.
Before-call decision_caps — целые 0..2, монотонны; финальный cap count совпадает
с board и не уменьшается относительно последнего call. Это **не** полный
trajectory replay: промежуточные moves/boards целиком не записываются.
Goal>=3 сохраняется, один атомарный ход способен создать 4 CAP сразу; такое
завершение допустимо. После goal не выполняется лишний spawn.

`threshold_counts` относятся только к завершённым moves: положительный
info.threshold форматируется `.0e`, при info.fallback или threshold=None
используется `fallback`. Ключи ограничены schedule source_arm плюс fallback;
sum=completed moves. None-call не добавляет threshold bucket.

Summary flatten-ит все wall samples каждого arm: обычная median,
p95 nearest rank ceil(95*n/100), max и above_nominal_rate относительно 0.01
**для всех**, включая bank. Отдельно: choose-wall/completed-move,
search_cpu_seconds и choose_cpu_per_move; depth_counts и threshold_counts;
фактический max_supplied_budget_seconds, объявленный maximum_profile_call_cap
и min_final_credit_balance. Схема сохраняет bank policy отдельно даже если
его native bytes совпали с baseline. Positions — счётчик узлов, не цена CPU/wall.
Нет adaptive censoring или исключения медленных/неудобных наблюдений.

## Данные и заранее фиксированный отбор

Progressive requested depth 8, nominal 0.01, max_moves 6000. Screen:
16 новых seed 2400000000..2400000015 × 5 arms = **80 игр**, строго serial,
циклический сдвиг порядка по seed. Это Python Random integer seeds выше 2^31;
нативный seed API не используется. Winner: больше goals, затем baseline,
затем меньше измеренный choose-wall/completed-move, затем объявленный порядок.

Rounds 1..4 обрабатываются в порядке номера с обновлением best goal count.
Local_plateau — только если **последние два** раунда не подняли предыдущий
best; иначе bounded_neighborhood_exhausted. Улучшение раунда 1 с последующими
двумя/тремя stalls не мешает локальному плато. Это не глобальная оптимальность.

Один finalist замораживается до control. Control: **100 новых пар**, seed
2500000000..2500000099 × baseline/finalist = **200 игр**, serial и чередование
первого label. N=100 выбран до любых новых игровых данных. Основание:
предыдущий geometry control на одном source_arm=baseline дал 34/60 и 24/60
побед у двух labels; larger N заранее уменьшает чувствительность к такой
вариативности, не гарантируя значимости. N не продлевается по p/outcomes.

Принятие: nonbaseline finalist, больше goals, exact paired one-sided p<=0.05,
ноль limits. Actual choose-wall ratio 0.9..1.1 сообщается отдельно; номинальный
budget не доказывает равную стоимость. Ресурсный выигрыш bank явно отделяется
от четырёх fixed profiles. Limit сохраняется отдельным исходом и прекращает
этап; его нельзя превратить в loss или продолжить без изменения протокола.

## Свежий seed inventory

До данных выполнен стандартный inventory полного набора 2048 history roots:
**12 roots, 2136 JSON, 136026 seen/reserved**, errors=[] и overlap=[] для обоих
новых диапазонов. Явно проверены HEAD geometry 01b659fa, math 08b8b953,
TD 6e421fee и JSON из их архивов. Старые 1.8/1.9 и 1.6/1.7 диапазоны обязательны
в seen; прежний контроль не переименован в новый. Неизвестные внешние seeds
не покрыты этим доказательством. Исключены dependencies и только текущий
общий search-alternatives output-root, где находится собственный новый plan.

Первый, ещё не использованный inventory для N=60 сохранён вне Git:
`../inventory-pre100-lab-q237ihcy/inventory-pre100.json`, SHA256
`5b85200c226dede0324afeb4b259fea854fa0e8bbcfda50f2bf1f6784efcbd45`.
После увеличения N до 100 **до данных** старый файл перемещён в эту лабораторию,
а новый inventory создан полным стандартным audit; диапазон control..99
заново проверен без пересечений. Повторная штатная запись inventory запрещена.

## Команды после интеграции native вариантов

```sh
rtk proxy python3 experiments/search_alternatives/run.py build
rtk proxy python3 experiments/search_alternatives/run.py screen
rtk proxy python3 experiments/search_alternatives/run.py screen --resume
rtk proxy python3 experiments/search_alternatives/run.py freeze
rtk proxy python3 experiments/search_alternatives/run.py control
rtk proxy python3 experiments/search_alternatives/run.py control --resume
rtk proxy python3 experiments/search_alternatives/run.py finalize
```

Нет произвольного --out/implicit rebuild. Каждый этап берёт flock. JSON
атомарно заменяется после каждой игры; resume требует точный префикс
расписания, прежние config/source/native/freeze SHA и валидную телеметрию.
Freeze/finalize только валидируют завершённые этапы и не продолжают их неявно.
Freeze требует точного полного набора source, raw screen, manifest, всех
пяти engine.cpp/native hashes. Усечённые/лишние наборы отвергаются до control.
Изменение источников во время build запрещает выпуск manifest.

## Проверено

`rtk proxy python3 -m unittest -v test_search_cycle.py`: **31 OK, 1.470 s**.
Только temporary mock binaries и stub variants; нет native builds/полных игр.
Проверены reset thresholds, exact 100-pair schedule, samples/CPU/nearest-rank
p95/None-call, threshold buckets, bank cap/available/debt/repayment/reset,
control bank identity, goal 4 CAP, limits, обе stop reasons, strict fingerprint,
exact resume/freeze/P2. В runner 32 module-level functions (31 helpers + main);
новые относительно geometry: arm_config, allocate_budget, charge_budget,
wall_statistics. Старые 84/116 gates без изменений не повторялись.

Общие native gates и реальные 80+200 игр выполняет координатор после интеграции.
Улучшение win rate или калибровки здесь не измерялось. Все выводы будущих игр
ограничены офлайн-моделью spawn/rules и выбранным ресурсным профилем.
