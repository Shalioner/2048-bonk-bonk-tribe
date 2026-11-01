# Финальное ревью протокола и bounded refinement

Проверен точный диапазон `c22fe8fe99fa5e551ef12e5b6d96f4b1740ce434..2c61ed0ed3bf360dcdcf7202204756a231b0123a`, detached review-worktree. Код, исходные данные и предыдущие отчёты не изменялись. Дополнительно read-only сверены фактические teacher/pilot/refinement/active артефакты основного worktree. Обучение, игры, native rebuild и делегирование не выполнялись.

## Подтверждённый P2: pre-screen guard обходится альтернативным --out

**Места:** `experiments/learning/fit_round2.py:69–70,245–249`; существующий контракт `cycle.py:415–424,450–451`.

`_evaluated()` ищет только стандартные `screen.json`, `holdout.json`, `finalist.json` в ROOT. При этом официальный CLI/API `cycle` поддерживает произвольный `--out` для screen/holdout. После завершённого screen в другом файле round2 не обнаруживает просмотренные результаты и разрешает начать два новых fit. Это нарушает заявленную границу «refinement только до screen» на данном маршруте.

**Воспроизведение без игр/обучения:** использованы настоящий `cycle.evaluate("screen", out=...)`, реальное расписание12 seed×3 arm и настоящая проверка schema/prefix; episodes/native явно заменены mock. Затем запущен настоящий refinement runner с FakeModel из тестов:

```text
actual cycle.evaluate alternate --out schema: screen True 36
refinement detects this completed screen: False
subsequent mocked fits: [2.0, 10.0] status: selected
```

**Граница finding:** стандартный маршрут с ROOT/screen.json корректно блокируется, что подтверждено новым тестом. Координатор сообщил, что фактический screen запущен именно через default path после завершения round2/фиксации LR10; активная пара модели/метрик read-only согласована. Эта находка не доказывает нарушения фактической последовательности и не требует повторного обучения или игр. Сырые screen outcomes не читались.

**Минимальная мера для поддержки альтернативного пути:** регистрировать начало официального screen/holdout в общем phase-marker независимо от `--out` и проверять его в refinement либо запретить альтернативный `--out` у этих официальных стадий. Добавить mock-регрессию screen в альтернативном файле → отказ round2 до fit. В текущем эксперименте достаточно сохранять уже используемый default-маршрут и не запускать новых fit; существующие source/data/selection SHA не изменять ради этой проверки.

Других подтверждённых дефектов в проверенном диапазоне не найдено.

## Закрытие предыдущих P2

- `evaluate.py:63–70` включает evaluator и position study в frozen hashes, а также существующие round2 runner/plan/selection. Изменение evaluate после freeze теперь отвергается до holdout. Новые freeze-тесты воспроизведены.
- `position_study.py:46–50` проверяет форму board и cap-count и исключает goal/поля без легальных ходов до стратификации. Терминальный first-cap2 свидетель из предыдущего отчёта и смешанный корпус покрыты проходящими регрессиями. Выбор по результату исследуемого arm не добавлен.
- `evaluate.finalize` учитывает полные игры позиционного корпуса отдельно и в общей сумме; complete/limit проверяются. Модельные refinement-fit повторно не считаются новыми игровыми эпизодами.

## Механизм refinement

- Ровно два варианта закреплены одновременно в коде и отдельном плане: LR2/LR10, epochs64, L2=1e-4, patience12, min_delta1e-5. Несовпадающий список вариантов отвергается. Минимум Brier выбирается с ничьей по объявленному порядку; новые параметры по screen/holdout не выбираются.
- Внешний admission не ослаблен: module gate=True, улучшение относительно того же stage prior **>=0.001**, и Brier строго лучше архивного v1. min_delta1e-5 применяется к выбору epoch внутри обучения, не заменяет внешний admission0.001. Метрики должны быть конечными в[0,1], изменённый prior отвергается.
- Проверяются SHA обоих datasets, архивов v1, cycle/plan/value_model/refinement runner/round2 plan, соответствие training в архивном binary его metrics, complete/records/отсутствие limit и разделение seed. `cycle.load_teacher` продолжает обеспечивать исходный строгий fingerprint и полноту records. Inputs повторно сверяются перед сохранением решения/публикацией.
- Варианты хранятся раздельно с параметрами и provenance в binary metadata. Resume читает сохранённый binary, сверяет header/metrics/источники и восстанавливает утраченные variant metrics без нового fit. Missing binary при metrics или decision не разрешает повторное обучение. Негативный результат сохраняет active v1 byte-identical.
- Selection фиксируется до active-пары. Сначала атомарно заменяются положительные metrics с SHA выбранной модели, затем binary. Между заменами существующий `cycle.model_gate` видит несовпадение и отказывает. Resume допубликовывает только уже известную выбранную пару; неизвестный active SHA отвергается. После стандартного screen/holdout/finalist разрешена только проверка полностью завершённого решения без изменения active-файлов.

## Read-only сверка фактических артефактов

- Train:128 полных партий,12204 записи,121 loss/7 goal. Validation:32 партии,3031 запись,28 loss/4 goal. Limit отсутствуют; число records каждого seed совпало с sampled_positions, labels — с полным исходом, SHA datasets — с round2 plan.
- Архивные pilot binary/metrics совпали с записанными SHA; training binary равен metrics. Source SHA cycle/plan/value_model совпали. Pilot gate=False.
- LR2: validation Brier0.10452683953202536, улучшение0.0002981993959434992, gate=False.
- LR10: validation Brier0.1035503509146113, улучшение0.001274688013357561, gate=True. Это validation для настройки, не независимое доказательство роста побед.
- SHA обоих variant binary/metrics совпали с selection, training внутри binary — с JSON metrics, source/data/runner/plan SHA согласованы. Active model.bin/training-metrics.json совпали с desired_active выбранного LR10.
- На момент сверки default screen.json уже существовал; holdout.json/finalist.json отсутствовали. Содержимое результатов screen не использовалось в ревью.

## Проверки

```sh
rtk proxy env TMPDIR="$REVIEW_TMP" python3 -m unittest -v test_learning_study.py test_value_refinement.py
rtk proxy git diff --check c22fe8f..2c61ed0
```

`REVIEW_TMP` — существующая `.review-temp` внутри review-worktree. Все13 новых mock-тестов прошли за1.258с. Дополнительно выполнен короткий mock-repro альтернативного output выше и независимые read-only JSON/SHA/strict-model-load сверки реальных артефактов. Новых реальных fit/игр, native rebuild, сети и изменений основного worktree не было. Решение о дальнейшей экспериментальной приёмке остаётся за координатором.
