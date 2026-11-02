# Реализация математического цикла

Первый этап: активные bot/core восстановлены из pure `e28aad01f8c8e3aa725e41640625e5fda7a02701`.
Core побайтно совпадает (SHA256 `257050c392bf3a743f894b0f674ff8d7da5c8f2165e2dff003f0e3b4192cb24d`).
В bot изменён только default CLI `--search-policy progressive`; Python API сохраняет
`policy="legacy"`, глубина8 и бюджет10с сохранены. Старые budget-тесты вызывают
legacy явно. Cache/progressive алгоритмы и их gates не ослаблены.

ML backend, inference headers, trainer, donor copy, тесты и experiments/learning,
experiments/td удалены из новой ветки. Полные code/data/license архивы сохранены
без изменений в `6e421fe`/`local/strategy-td-port-20261102` и
`45fd360`/`local/strategy-learning-20261101`. README/SOURCE/RESEARCH-NOTES указывают
архивы и текущий MATHEMATICAL.md. Модель частот появлений и её обновления сохранены.

Проверка первого этапа:

```sh
sh vendor/build.sh
python3 -m unittest test_bot test_benchmark test_progressive_search test_search_cache test_pure_math -v
```

65 тестов прошли за6.484s. Четыре прежних pure-модуля содержат61 тест
(32+15+9+5), новый модуль добавляет4 gates отсутствия ML, CLI default,
legacy CLI и неизменных depth/think/spawn updates. Указанные ранее74 не являются
числом тестов этих исходных файлов; тесты не удалялись и не пропускались.
Телефон/runtime/main/старые worktree не менялись. Реальные партии не запускались.

Benchmark CLI при `--think` также использует `--search-policy progressive`
по умолчанию, явный legacy доступен; API `benchmark.play(...,policy="legacy")`
сохраняет прежнюю семантику. Без `--think` fixed-depth путь не меняется. Политика
включена в checkpoint config; неподдерживаемый progressive отвергается до
checkpoint/симуляции.17 benchmark-тестов прошли за0.038s, включая два новых.

## Новый фиксированный цикл

`experiments/math/plan.json` заранее задаёт baseline и два раунда трёх независимых
изменений: EMPTY×1.15/1.3, MONOTONICITY×0.75/0.5, MERGES×1.25/1.5. SUM остаётся11;
он является отрицательным штрафом, поэтому увеличение SUM не выдается за награду
крупным плиткам. Никакие параметры не сочетаются. Веса активного core пока исходные.

Seed audit выполнен до evaluation:1620 исторических JSON из перечисленных2048
worktree/архивов, включая сохранённый6e421fe TD-worktree;135886 прежних/объявленных
seeds консервативно зарезервированы. Ошибок и пересечений с новым screen
1600000000..1600000007 и control1700000000..1700000059 нет. Полные пути/SHA JSON
сохранены в `seed-inventory.json`; неизвестные внешние источники не покрываются.
Зависимости `.venv`/`node_modules` исключены из инвентаризации.

Команды из корня отдельного worktree:

```sh
# inventory уже зафиксирован; повторная перезапись запрещена
python3 experiments/math/run.py build
python3 experiments/math/run.py screen
python3 experiments/math/run.py freeze
python3 experiments/math/run.py control
python3 experiments/math/run.py finalize
```

После прерывания screen/control повторяются с `--resume`. Runner использует только
стандартные пути и один неблокирующий `flock`, без parallel timed-партий.
`--help`/import не загружают native и ничего не собирают. Build явный: семь отдельных
библиотек из pinned pure e28aad0, фиксированные compiler flags, compiler version,
generated source SHA и реальный binary SHA. Каждая вариация отличается ровно одной
строкой веса, baseline source побайтно исходный. Завершённый build можно только
строго проверить; несовместимый/чужой build не перезаписывается. Новая сборка после
начала screen запрещена. Исходники/план/инвентаризация/spawn table входят в fingerprints.

Screen —56 последовательных игр, порядок всех7 arms поворачивается по seed.
Сохраняются seed/label/source_arm, final board/outcome/moves, точное choose wall,
total wall, достигнутые/запрошенные глубины, positions/fallback/cut и нетерминальные
cap1/cap2 snapshots. После каждой партии JSON атомарно заменяется. Resume проверяет
точный префикс, config, исходники, actual source/native SHA и freeze SHA. Limit
сохраняется отдельно и останавливает этап; он не превращается в loss.

Выбор: максимум побед; при равенстве baseline имеет приоритет, затем меньшее
choose-wall/completed-move и объявленный порядок. `finalist.json` до контроля
хеширует raw screen, plan/inventory, producer/stat script и все фактические
generated sources/native binaries. Read-side заново проверяет выбор по готовому
screen; freeze/finalize не продолжают неполный этап неявно. По каждому раунду
сохраняются previous_best/best_goals/improved. `local_plateau` разрешён только при
двух раундах без роста лучшего числа побед; иначе `bounded_neighborhood_exhausted`.
Оба названия относятся только к заранее заданному ограниченному набору.

Control всегда120 игр:60 новых seed×labels baseline/finalist, с чередованием
первого label. Даже выбранный baseline играет под обоими отдельными labels.
Улучшение требует больше побед, exact paired one-sided p<=0.05 и0 limits;
повторный выбор/подбор по контролю отсутствует. Если finalist=baseline, новая
стратегия улучшенной не объявляется. Summary сохраняет положительный или
отрицательный результат, точные gained/lost seed, win-rates и раздельную стоимость.
Одинаковый10ms upper budget не называется одинаковой фактической ценой: отношение
choose-wall/move и диапазон[0.9,1.1] показаны явно. Про10с/ход, телефон и глобальный
оптимум выводов нет; исторические26/60 не считаются новым контролем.

Проверки runner:15 synthetic/mocked тестов прошли; py_compile/diff-check
прошли. Покрыты полноценный мини-цикл, resume после interruption, контроль
изменения native/source/config/порядка, сохранение limits, malformed freeze,
read-only finalize/freeze, exact statistics/неполные пары, baseline tie,
разные исходы двух раундов, no-spawn после synthetic goal и telemetry/snapshots.
Все семь native-вариантов собраны; финальный source/manifest проверен после
последнего изменения протокола, raw core SHA совпал с e28aad0. Нормализация пути
module filename также покрыта регрессией. Build/исторические временные сборки
не включаются в Git. screen/control/finalist/summary не создавались: реальные
полные игры или длинные измерения не запускались.

Предданный follow-up закрывает неполный freeze: writer и verifier используют один
`required_freeze_paths()`, verifier требует точное совпадение ключей hashes до
проверки SHA. Raw screen целиком входит в обязательный набор, включая telemetry,
которая не влияет на агрегаты выбора. Red regression подтвердил принятие trim/
extra hashes и обход через positions+=1; после исправления все17 synthetic tests
прошли за0.694s, py_compile/diff-check прошли. План и inventory не изменялись.
Прежние ignored builds не пересобирались: их producer fingerprint после этого
source-fix устарел; координатор выполняет первую свежую сборку после интеграции.
Реальные screen/control по-прежнему не запускались.
