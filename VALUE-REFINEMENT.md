# Два ограниченных уточнения MC-value

Задача относится к плану `experiments/learning/model-round2-plan.json`,
зафиксированному координатором до screen/holdout в `b52fee7`.
Исходный пилот не прошёл Brier admission: его модель, метрики и teacher datasets
остаются неизменными. Эта поставка добавляет только `fit_round2.py`,
`test_value_refinement.py` и данный документ. `value_model.py`, header,
`cycle.py`, исходный plan и игровые данные не менялись.

## Запуск после интеграции

```sh
python3 experiments/learning/fit_round2.py
python3 experiments/learning/fit_round2.py --resume
```

Runner лениво импортирует текущий `cycle` и использует его `load_teacher`
для train и validation. Сбор партий никогда не продолжается неявно. До fit
проверяются:

- SHA обоих datasets из отдельного round2-плана;
- SHA `pilot-model-v1.bin` и `pilot-training-metrics-v1.json` из того же плана;
- исходные source hashes cycle/plan/value_model и соответствие архивной пары;
- complete, отсутствие limit, наличие records и непересечение seed партий.

Допускаются ровно две конфигурации из записанного плана: LR2 и LR10,
epochs64, patience12, L2=1e-4, min_delta=1e-5. Batch size и seed берутся
из конфигурации исходного пилота. `min_delta` — лишь критерий сохранения epoch;
**внешний допуск улучшения Brier остаётся 0.001**.

## Артефакты и выбор

Все варианты сохраняются независимо:

```text
experiments/learning/refinement/lr2.bin
experiments/learning/refinement/lr2-metrics.json
experiments/learning/refinement/lr10.bin
experiments/learning/refinement/lr10-metrics.json
experiments/learning/refinement/selection.json
```

В metadata binary включены source/model/data/plan provenance и header
`refinement_checkpoint`. Metrics имеют совместимую с `cycle.model_gate` схему:
`metrics`, `config`, `datasets`, `source_hashes`, `model_sha256`,
`validation_gate_passed` и `gate` с `required_improvement=0.001`.

`source_hashes` содержит неизменные cycle/plan/value_model и дополнительные
`refinement_runner`/`refinement_plan`. Конфигурация, datasets, архивы v1 и сами
файлы происхождения проверяются повторно перед сохранением решения и публикацией.
Изменение источника не разрешает молчаливое дообучение или перезапись checkpoint.

Выбирается минимальный validation Brier; при точной ничьей сохраняется порядок
плана, то есть LR2. Для публикации нужны одновременно:

1. `metrics.validation_gate_passed is True` самого модуля;
2. улучшение относительно того же stage-prior baseline не меньше0.001;
3. Brier строго меньше Brier архивной v1.

Изменение stage-prior baseline отвергается. Если лучший кандидат не удовлетворяет
этим условиям, `selection.json` получает `status=negative`; активные `model.bin`
и `training-metrics.json` вообще не записываются. Даже худшие варианты и архивы
v1 сохраняются. Решение с `status=selected` содержит `selected_variant`, SHA
обоих кандидатов, обоих архивов v1, источников и ожидаемой активной пары.

## Прерывания и согласованность

Variant binary сохраняется до variant metrics. Если процесс прервался между
ними, `--resume` восстановит metrics из проверенных training/metadata самого
binary без нового fit. Уже существующие несовместимые артефакты не переписываются.
Отсутствие binary при существующих metrics или зафиксированном selection
считается ошибкой, а не разрешением на повторное обучение.

`selection.json` фиксируется **до** изменения активных файлов. Для положительного
решения сначала атомарно заменяются активные metrics с SHA выбранной модели,
затем атомарно заменяется binary. Поэтому между двумя заменами положительный
`cycle.model_gate` видит несовпадающий SHA и обязан остановить screen.
Порядок «сначала новая модель, потом старые отрицательные metrics» не применяется:
он позволял бы тихо исключить learned arm вместо отказа.

`--resume` заново проверяет provenance и решение, затем допубликовывает уже
сохранённый выбранный вариант; новые fit не вызываются. Каждый active-файл может
иметь только известный SHA v1 или выбранного кандидата. Неизвестная активная
версия отвергается. Если screen/holdout/finalist уже существует, начинать round2
или менять активную пару запрещено; проверка полностью завершённого решения
без изменений остаётся разрешённой.

Исходный `cycle.py fit --resume` предназначен для первоначального пилота.
После выбора refinement следует использовать `fit_round2.py --resume`,
поскольку конфигурация активного fit уже будет другой. Модель здесь по-прежнему
настраивается на validation, не получает нового независимого контроля и не
обещает улучшения процента побед.

## Проверки этой поставки

```sh
python3 -m unittest -v test_value_refinement.py
```

Восемь коротких synthetic/mock тестов прошли за0.959с. Использован настоящий
формат сериализации `ValueModel`, но `fit` заменён mock-функцией; новых реальных
обучений и партий не запускалось. Проверены положительный выбор, отрицательный
gate с byte-identical v1, правило ничьей, необходимость module gate и преимущества
над v1, строгий resume, прерывание active-публикации и восстановление metrics
завершённого кандидата без повторного fit. Также проверены отказ при изменённых
source/dataset/archive, incomplete/limit и попытке начать обучение после screen.

Фактическую совместимость с текущими teacher checkpoints и реальный результат
двух конфигураций проверяет координатор после cherry-pick. Существующие данные
в тестах не читались как обучающие примеры и не менялись.
