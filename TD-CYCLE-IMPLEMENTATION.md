# TD/MC experiment runner

Реализован `experiments/td/run.py`, отдельный от старого MC-протокола.
Он выполняет зафиксированный `experiments/td/plan.json`: native pool,
matched MC/TD-teacher, TD-selfplay, validation, screen и независимый контроль.
В этой реализации реальные партии, обучение и native-сборки не запускались.
Увеличение процента побед пока не измерено.

## Запуск после интеграции

Из корня выделенного 2048 worktree с готовым совместимым основным движком:

```sh
python3 experiments/td/run.py build
python3 experiments/td/run.py pool
python3 experiments/td/run.py train --group matched
python3 experiments/td/run.py train --group selfplay
python3 experiments/td/run.py screen
python3 experiments/td/run.py freeze
python3 experiments/td/run.py holdout
python3 experiments/td/run.py finalize
```

`build` явно собирает только trainer через `td_model.build_trainer`; основной
движок должен быть собран координатором заранее. Импорт и `--help` не загружают
bot/engine/trainer и ничего не собирают. Повторный `build` только проверяет
завершённый manifest; изменённый бинарник/исходники не перезаписываются.
Для продолжения pool/train/screen/holdout нужен `--resume` с теми же параметрами,
исходниками и файлами моделей. Отсутствующий checkpoint при `--resume` — ошибка.

Один неблокирующий `flock` защищает весь CLI-цикл. Группы обучения запускаются
последовательно. Timed-партии всегда последовательны; порядок arms циклически
сдвигается по seed. Запуск функций из стороннего Python-кода требует того же
`lease()`; штатный CLI уже делает это.

`--out` позволяет сохранить отдельный checkpoint, но downstream-команды читают
стандартные pool/train/screen/finalist/holdout пути. Для основного воспроизводимого
цикла используются команды выше. Произвольный `--out` evaluation также ставит
общий запрет нового обучения; новый путь не позволяет обойти его.

## Файлы и восстановление

- `build/trainer-manifest.json`: собственный fingerprint исходников, SHA реального
  trainer. `build-intent.json` отделяет прерванную разрешённую сборку от чужого
  бинарника.
- `pool.json`: config, sources, spawn_table, games, starts, complete. Сохраняются
  все512 исходов. В curriculum идут первые64 уникальных legal/non-goal массивов
  каждой стадии в порядке seed; число побед источника не участвует в отборе.
- `train-{matched,selfplay}.json`: config, sources, pool SHA, run_id, current,
  checkpoint_episodes, records_sha256, checks, selected, stalls, complete,
  stop_reason. `pending_validation` фиксируется перед проверкой milestone.
- `<train-file>.artifacts/blocks/00000000.json`: журнал эпизодов, максимум128
  записей на файл. Каждый исход атомарно записывается; модель сохраняется каждые128
  эпизодов и перед milestone. Цепочка SHA связывает checkpoint с точным журналом.
- `current/{0,1}-<arm>.bin`: два чередующихся слота. State публикуется после всех
  моделей пары. Прерванная запись нового слота не повреждает предыдущий state.
- `milestones/<count>/<arm>.bin` и `validation/<count>/<arm>.json`: неизменяемые
  модели и192 greedy observe-партии (64 full,64 cap1,64 cap2). Поддерживаются
  атомарные префиксы validation без повторения законченных партий.
- `screen.json`/`holdout.json`: собственные config/sources/spawn_table, games,
  complete; каждая партия содержит точное choose wall time, достигнутые/запрошенные
  глубины, fallback/cut/positions, итоговое поле и cap-состояния.
- `finalist.json`: один TD-finalist, screen gate, все модели и SHA producer/stat
  scripts, плана/инвентаризации, реальных бинарников, pool, blocks, validation,
  current/selected моделей и screen. `summary.json` сохраняет также отрицательный
  итог, оба exact p-value и отдельную стоимость.

Возобновление восстанавливает модель из current и детерминированно проигрывает
уже записанный хвост (не более одного блока), сверяя весь стабильный исход,
trajectory digest и seed/start. Эпизод не применяется дважды к опубликованному
checkpoint. После прерывания на границе milestone его validation завершается
до следующего обучающего эпизода. Старые model/check/record SHA проверяются.
Журнал хранится блоками, а его история загружается в память при resume/обучении;
максимальный131072-партийный прогон может потребовать заметной памяти и диска.

## Отбор и ограничения

Для teacher и warmup явно передаётся `training.teacher_epsilon=0.0`;
после warmup selfplay получает `model.epsilon=0.05`. Pool/validation используют0.
Matched MC и TD-teacher получают одинаковые seed, start_board, фиксированный
teacher и число эпизодов; различие trajectory_digest/исхода останавливает группу.
Их общий milestone выбирается по сумме validation tuple `(full, cap1, cap2)`.
Равенство сохраняет самый ранний checkpoint, включая начальный count0.
Начальный validation не считается одним из минимальных трёх обученных checks.
После двух stall и минимум трёх checks фиксируется локальное plateau;
достижение максимума без этого условия имеет статус `budget_limited`.
Selfplay имеет собственный выбор и stop; это отдельная политика поведения.

Observe проверяется по числу updates и неизменности параметров. `first_caps`
принимаются только как точные count1/count2 board16 после spawn, пригодные к
следующему выбору хода. Terminal snapshots отвергаются. Timed evaluation также
заканчивается до spawn после goal. На ограничении moves результат сохраняется
как `limit`, группа останавливается; обученные веса этого эпизода не публикуются.
Mismatch обеих matched-траекторий также сохраняется как invalid диагностическая
запись, без принятия частичных моделей.

`evaluation-started.json` ставится глобально до первой screen/holdout партии.
После него новые/частичные pool, train и build запрещены для любого `--out`.
Полностью законченные checkpoint разрешено только строго проверить через resume.
`freeze` и `finalize` никогда не продолжают неполные этапы неявно. Финалист не
перевыбирается по holdout. Повторная штатная заморозка запрещена.

На screen выбирается только TD-teacher или TD-selfplay: победы, затем choose
wall/completed move, затем порядок плана. `promoted` требует >heuristic,
>=matched MC и >=исторический MC. Holdout — ровно60 seed×3 arms; joint claim
требует больше побед TD против обеих reference и два односторонних точных парных
теста с p<=0.025 каждый, без limits и с пройденным screen gate. Дубликаты seed
и неполные пары отвергаются. Равный10ms nominal budget не называется равной
фактической стоимостью: отдельно показаны total wall, choose wall/move и
отношение стоимости; флаг сопоставимости требует диапазон[0.9,1.1].

Старый `experiments/learning/model.bin` только читается как отдельный screen arm.
Данные старого learning-cycle не становятся новой обучающей выборкой; его
`source_hashes()` не используется. Фиксированные диапазоны новых seeds и аудит
доступных старых архивов принадлежат `seed-inventory.json`; неизвестные внешние
источники этим аудитом не покрываются. Cap-validation использует train-only pool,
поэтому не является независимым полным контролем. Вывод относится только к
офлайн-правилам и10ms budget; глобальный оптимум/телефон/10s полный win-rate
не проверяются и default не переключается.

## Проверки

```sh
python3 -m unittest test_td_cycle -v
python3 -m py_compile experiments/td/run.py test_td_cycle.py
git diff --check
```

Mocked-проверки используют реальные файлы протокола и SHA скрипта с маленькими
временными моделями/планом. Покрывают полный цикл и строгий resume, две ошибки
прерывания, tail replay, teacher/selfplay epsilon, общие matched-trajectories,
минимум checks/earliest ties, после-spawn snapshots, неизменный observe, limit,
выбор с учётом old MC, глобальный marker при custom output, запрет неявных партий
из freeze/finalize, модель/producer/engine tampering, поворот arms, exact p-value,
дубликаты seed и отсутствие spawn после goal. Native/core integration и реальные
экспериментальные результаты проверяются координатором отдельно.

Результат перед сдачей:17 тестов прошли за3.318s; py_compile и staged diff-check
прошли. Изменены только этот отчёт, новый runner и его тесты.
