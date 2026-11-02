# Цикл проверки математической геометрии

Новый автономный runner находится в `experiments/geometry/run.py`.
Старые `experiments/math/*`, их результаты и контроль не изменяются.
Здесь нет ML-моделей, fit, обучения или установки нового default.
Runner использует reviewed math-протокол из baseline
`08b8b9536070ef493f755b5c130ff89ff4e05512`, включая точный обязательный
набор freeze-хешей и защиту raw telemetry от усечённого freeze.

## Заранее объявленные варианты

Во всех вариантах pinned production core — `e28aad01f8c8e3aa725e41640625e5fda7a02701`,
SHA256 `257050c392bf3a743f894b0f674ff8d7da5c8f2165e2dff003f0e3b4192cb24d`.
Baseline возвращается побайтно без изменения. Формулы кандидатов поставляет
отдельный модуль `experiments/geometry/features.py` через
`variant_source(base_bytes, arm) -> bytes`; arm содержит name/round/kind/strength.
Нативная сборка использует `-I vendor` и `geometry_features.h`.

| Round | Arm | Strength | Гипотеза и цена |
|---|---|---|---|
| 0 | baseline | 0 | Сохранённая ручная эвристика, точная исходная линия сравнения. |
| 1/2 | segments_r1/segments_r2 | 0.5 / 1 | Монотонность по участкам между CAP может лучше описывать доступные цепочки; добавляет арифметику и может ослабить полезный общий порядок. |
| 1/2 | geometry_r1/geometry_r2 | 2000 / 4000 | Мягкий штраф стеснённой CAP-геометрии может сохранять мобильность; добавляет анализ поля и может штрафовать полезные позиции CAP. Это не запрет легальных перемещений. |
| 1/2 | risk_r1/risk_r2 | 0.5 / 1 | Точная вероятность немедленного no-legal-move loss после следующего spawn при двух CAP и не более трёх пустых клеток; добавляет execute_move-проверки вне старого positions counter. |

Это семь заранее объявленных arms и два фиксированных раунда, без адаптации
силы по контролю. Правила CAP11/GOAL3 и стадийная модель spawn сохраняются как
проверяемые допущения симулятора. Сами формулы и их нативная проверка принадлежат
исполнителю features и координатору; mock-тесты runner не доказывают их пользу.

## Seed audit

До игровых данных выполнено:
`rtk proxy python3 experiments/geometry/run.py inventory`.
Сохранён `seed-inventory.json`: 11 history roots, 1822 JSON, 135954 ранее
наблюдавшихся/зарезервированных seed, **errors=[]**, оба overlap пусты.
Явно проверены HEAD старого math08b8b953 и TD6e421fee и присутствие JSON
из обоих архивов. Наличие прежних math screen1600000000..1600000007 и
control1700000000..1700000059 обязательно, поэтому прежний контроль не
переименован в новый. Остальные известные 2048 JSON включены общим обходом;
неизвестные внешние seed остаются за границей доказательства.

Новые screen1800000000..1800000011 и control1900000000..1900000059
не пересекаются с наблюдавшимися и ранее зарезервированными диапазонами.
Из обхода исключён только весь текущий общий geometry output-root и каталоги
зависимостей, чтобы собственный новый plan не считался старым экспериментом.
Повторная запись inventory запрещена. Абсолютные пути нужны только как
указатели на архивные доказательства; source/freeze fingerprint использует
пути относительно текущего worktree.

## Отбор и контроль

Все игры: progressive search, requested depth8, think0.01, max_moves6000.
Screen: 12 новых seed × 7 arms = 84 игры, строго последовательно; порядок arms
циклически сдвигается по seed. Отбор: больше goal; при равенстве baseline,
затем меньше измеренный choose-wall/completed-move, затем порядок plan.

Freeze вычисляет итог обоих раундов. `local_plateau` допустим только если
ни round1, ни round2 не поднял предыдущий лучший goal count. Если хотя бы один
раунд улучшил его, остановка называется `bounded_neighborhood_exhausted`.
Оба вывода относятся только к этому конечному набору формул и strengths.

Control: 60 новых seed × baseline/finalist = 120 игр, последовательно,
первый label чередуется по seed. Даже если finalist=baseline, оба labels
сохраняются отдельно. Принятие требует nonbaseline finalist, больше побед,
exact paired one-sided p<=0.05 и отсутствие limit. При другом исходе baseline
сохраняется. Цена считается отдельно: choose-wall ratio и флаг попадания
в диапазон0.9..1.1. Номинальные10ms не доказывают равную реальную цену.
Raw и итог сохраняют wall time и достигнутые depth counts. positions —
legacy counter: дополнительные immediate-risk execute_move в него не входят.
Вывод о выигрыше на телефоне или при10s из этого протокола не следует.

## Команды после интеграции features

Команды выполняются из собственного worktree. Пользовательский `--out`
не поддерживается; каждый этап записывает только свой фиксированный файл.

```sh
rtk proxy python3 experiments/geometry/run.py build
rtk proxy python3 experiments/geometry/run.py screen
rtk proxy python3 experiments/geometry/run.py screen --resume
rtk proxy python3 experiments/geometry/run.py freeze
rtk proxy python3 experiments/geometry/run.py control
rtk proxy python3 experiments/geometry/run.py control --resume
rtk proxy python3 experiments/geometry/run.py finalize
```

Каждый этап берёт flock; JSON checkpoint заменяется атомарно после каждой
партии. Resume принимает только точный префикс расписания с совпадающими
config, source, native и freeze SHA. Завершённые игры не повторяются.
Limit сохраняется отдельным исходом и блокирует дальнейшее продолжение,
не превращаясь в loss. Freeze/finalize только валидируют завершённые этапы
и никогда не запускают недостающие игры неявно.

`build` явный. Он фиксирует bot/benchmark/spawn/pinned-core и все прежние
vendor headers, добавляя features.py и geometry_features.h, с относительными
ключами. Producer исполняется лениво из текущих bytes features.py без
повторного использования import-cache/pyc. Manifest проверяет фактические
бинарники и сгенерированные engine.cpp; изменение источников во время
сборки запрещает публикацию manifest. Freeze требует **точного полного**
набора source, screen, build manifest, всех семи native и engine.cpp hashes;
лишние, отсутствующие или усечённые наборы отвергаются до control.

## Проверено в этой ветке

`rtk proxy python3 -m unittest -v test_geometry_cycle.py`:
**21 тест OK, 0.765 s**. Использованы временные mock binaries и stub feature
producer; реальный bot.py AST загружает только Python-правила с отдельным
stub backend вместо eager native load. Проверены baseline byte equality,
контракт семи arms, seed inventory, свежие source bytes и header fingerprint,
отказ manifest при source change, exact resume, порядок serial scheduling,
limit, goal без лишнего spawn, telemetry, корректный exact test, оба stop
reason и неизменённые freeze/P2 gates.

Нативные варианты, полные партии и контроль в этой ветке **не запускались**.
Координатор после интеграции проверит общий native build и обязательные gates,
затем выполнит заранее объявленные 84+120 игр и сохранит фактический вывод.
