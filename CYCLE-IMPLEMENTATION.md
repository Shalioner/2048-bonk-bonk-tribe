# Переносимый runner нового цикла

Сданные новые файлы: `experiments/learning/cycle.py`, `test_learning_cycle.py`
и этот отчёт. Протокол взят из `edc1f54`; его отдельный cherry-pick в рабочую
ветку исполнителя нужен только для чтения. В интеграционную ветку следует брать
новый cycle-коммит отдельно, без повторного переноса protocol-коммита.

Runner читает `experiments/learning/plan.json`; seed/метрики/модель по умолчанию
не подменяются аргументами CLI. Полных партий и настоящего обучения исполнитель
не запускал: тесты используют явные заглушки эпизодов и модели.

## Команды после интеграции общих компонентов

```sh
rtk proxy python3 experiments/learning/cycle.py build-baseline
rtk proxy python3 experiments/learning/cycle.py collect --split train
rtk proxy python3 experiments/learning/cycle.py collect --split validation
rtk proxy python3 experiments/learning/cycle.py fit
rtk proxy python3 experiments/learning/cycle.py screen
# Далее координатор выбирает и фиксирует finalist.json.
rtk proxy python3 experiments/learning/cycle.py holdout
```

`--resume` есть у collect/fit/screen/holdout. По умолчанию существующие данные
не перезаписываются. У collect/screen/holdout есть необязательный `--out` для
отдельного артефакта; fit читает стандартные teacher-train/teacher-validation.

`build-baseline` сохраняет C++ именно из baseline-коммита в `build/baseline`,
собирает его с существующими vendor-заголовками и пишет manifest исходника,
бинарника, заголовков и compiler flags. Повторный вызов при совпавших хешах
ничего не пересобирает; при несовпадении отказывается менять исходный пакет.

## Сохранённые форматы

Teacher JSON: `checkpoint_version`, `kind`, `config`, `source_hashes`,
`solver_table`, `environment_table`, `games`, `records`, `complete`.

Records содержат `seed`, `board` (afterstate до spawn), `label`, `move`,
`stage`, `episode_weight=1/n`. Отбор: каждые32 хода, первый cap-transition,
каждые8 ходов при <=3 пустых afterstate-клетках; goal-afterstates исключены,
board уникален внутри seed. Целевой label относится к исходу всего эпизода.
Полнота проверяется для каждого seed: число records равно sampled_positions
для goal/loss и нулю для limit. Утрата целой группы seed обнаруживается.

Обучение вызывается на instance `ValueModel` с параметрами плана:
epochs16, learning_rate0.25, l2=1e-4, patience4, явно min_delta0.001.
Fit только читает законченные datasets, не продолжает сбор неявно.
Модель сохраняется в `model.bin`; `training-metrics.json` хранит хеши dataset,
исходников/модели, все параметры, исходные метрики и внешний gate.
Learned-arm включается только при model.validation_gate_passed и улучшении
validation Brier >=0.001. Модуль value дополнительно применяет свой strict
критерий; точный пограничный случай0.001 поэтому исключается консервативно.

Screen/holdout JSON имеют тот же общий header и `games`, `summaries`, `complete`.
Каждый game содержит seed/arm/result, конечное поле/caps/max/moves,
полное время и `search_wall_seconds`, количество search-вызовов,
фактически возвращённые `depth_counts`, `requested_depth_counts`,
fallbacks/cuts/positions, первые cap-снимки и номера ходов.
Для fixed teacher positions=None: недоступный счётчик не выдаётся за ноль.

Список screen-arm читается из плана; порядок поворачивается на каждом seed.
Holdout содержит только baseline и единственный замороженный вариант;
порядок двух армов чередуется по seed. Все полные игры выполняются последовательно.
Из-за limit опыт немедленно останавливается после атомарного сохранения этого
исхода; limit не превращается в loss и не получает обучающих меток.

## Fingerprint и общие контракты

Каждый checkpoint проверяет точное совпадение параметров/таблиц, source SHA,
SHA и пути реально загруженных библиотек, а для learned-arm — model.bin и
исходника ValueModel. Teacher не включает model.bin: позднее создание модели
не ломает возобновление сбора. Завершённые игры должны образовывать точный
префикс порядка seed/arm; повторы, перестановки и неподходящие исходы отвергаются.
Атомарный checkpoint сохраняется до первой игры и после каждой завершённой.
Прерванная игра запускается заново, законченные не повторяются.

Holdout требует `finalist.json` вида:

```json
{"finalist":"progressive","hashes":{"bot.py":"<sha256>","experiments/learning/plan.json":"<sha256>"}}
```

`hashes` — непустой плоский словарь путей относительно worktree. Все пути
должны оставаться внутри worktree, все хеши сверяются до игры. Координатор
добавляет полный нужный набор источников/модели/данных. Сам finalist.json
включён в fingerprint holdout целиком. Runner ничего не выбирает по контролю.

Требуются интегрированные `bot.configure_value_model(None|path)` и
`bot.choose(..., policy="legacy"|"progressive", info=...)`. Старый baseline
API2 загружается через bot.load_engine; configure(None) на нём должен быть
допустим. Таблицы solver/environment передаются play раздельно, проверяются
на размер/конечность/неотрицательность, внутри партии не обучаются.

## Проверка

`rtk proxy python3 -m unittest -v test_learning_cycle.py` — 13 тестов прошли
за6.996с. Это mocked episodes на реальных конфигурациях128/32 train/validation,
12 screen seed и60 holdout-парах; настоящие игры/fit не выполнялись.

Проверены interruption/resume/skip, отказ перезаписи, смена binary/environment/
model, нарушение порядка, полнота записей каждого эпизода, late creation модели
после teacher, параметры fit/min_delta/Brier gate, отсутствие скрытого сбора
в fit, последовательный порядок120 holdout-игр и заморозка единственного
варианта, сохранение limit без labels, afterstate до spawn, неизменность
baseline-снимка и идемпотентность сборки на явной заглушке компилятора.

После интеграции координатор выполняет короткий настоящий smoke и общие gates,
затем уже разрешённые полные сбор/обучение/screen/control. Никакого вывода о
проценте побед эти unit-проверки не дают.
