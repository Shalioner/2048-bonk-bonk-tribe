# Закрытие P2 freeze

Проверен точный commit `7b561fbcee3c34fb4ab83811473066946469ae35` (worker `d8a08b4`) поверх рассмотренного `989477a`: только `experiments/math/run.py`, две регрессии в `test_math_cycle.py` и описание исправления. План, inventory и native core не менялись.

**Ранее подтверждённый P2 закрыт. Новых дефектов в этом diff не обнаружено.**

Writer и verifier используют общий `required_freeze_paths()`: sources, полный raw screen, manifest и все семь generated source/native pairs. Набор файлов соответствует прежнему полному writer-набору. Verifier до SHA-проверки требует dict и точное равенство keyset; сокращение или расширение manifest теперь отклоняется. SHA вычисляются по обязательным путям, а не по произвольно перечисленным путям из входного JSON. Проверки `check_build`, validate-only screen и повторного расчёта selection сохранены.

Самостоятельно выполнены только две новые targeted mocked регрессии:

```text
python3 -m unittest \
  test_math_cycle.MathCycleTests.test_freeze_requires_exact_hash_key_set \
  test_math_cycle.MathCycleTests.test_raw_telemetry_cannot_hide_behind_trimmed_freeze -v
Ran 2 tests in 0.107s — OK
```

Они подтверждают отказ для trim и extra keys, обнаружение `positions += 1` при полном SHA-наборе и отказ до mocked play/создания control при сокращённом наборе. Таким образом, исходный конкретный обход raw-screen-печати больше не воспроизводится.

Полные suite, сборки, поиск ходов, партии и обучение здесь не запускались; продукт и данные не менялись. Предыдущее ревью остальных контрактов остаётся действующим. Дальнейшая приёмка фактических библиотек и результатов относится к последующим этапам; это closure исходников до данных, не доказательство прироста побед.
