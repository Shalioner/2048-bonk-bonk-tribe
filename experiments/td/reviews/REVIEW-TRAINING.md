# Независимая приёмка сохранённых TD/MC training данных

**Блокеров для фиксации результатов обучения не найдено.** Проверены сохранённые артефакты, а не повторное исполнение партий. Обучение состоялось и меняло веса; выбранные checkpoint у всех трёх arms — count0, исходная нейтральная оценка V=0.5. Положительного результата обучения по заданной validation здесь нет.

Область: root `experiments/td/` — pool/pilot-prefix, оба завершённых training states, journals, referenced models и validation. Runner соответствует `88063e1`, pool-amendment/plan — `e05ed8e`; данные на момент завершения сверки сохранены в `fa6f59a3a6195ee234490842015ba650cfd494bc`. Записан только этот отчёт. Не вызывались runner, native trainer, обучение, партии, rebuild или suite. После окончания raw-сверки координатор начал screen; его результаты этим отчётом не читались.

## Pool и pre-training amendment

- Финальный pool содержит ровно4096 последовательных seed1400000000–1400004095, complete=True,4096 loss/0 goal/0 limit; observe updates/attempted_updates0. Финальные boards и legal/non-goal first_caps независимо проверены.
- Первые512 стабильных записей совпали с `pilot-pool-512.json` во всех полях, кроме seconds. SHA обоих файлов совпали с `pool-prefix-check.json`. Pilot сохранился отдельно, а не был выдан за новый независимый набор.
- Повторный детерминированный отбор первых unique snapshots в порядке seed дал те же pool starts: cap1 **64**, cap2 **59**. У pilot было64/8. Capacity64 — верхняя граница, фактических cap2-позиций59; полной ёмкости64 документировать нельзя.
- Amendment расширил train-only источник до4096 до training/validation/screen; source metadata и commit-содержимое плана согласованы. Отбор start states использует cap-snapshots, не исход goal/loss. Новый prefix не является независимым win-control.

## Training journals: независимый пересчёт

Каждая группа содержит128 последовательных blocks×128 records=**16384** индекса0–16383, seed1000000000–1000016383. Проверены run_id, block/index/seed-prefix, отсутствие invalid/limit, точные curriculum starts и итоговая SHA-цепь records. Обе группы complete, без pending_validation и неопубликованного хвоста.

| Arm | Full-start | Cap1-start | Cap2-start | Goal из full | Goal из cap1 | Goal из cap2 |
|---|---:|---:|---:|---:|---:|---:|
| mc_teacher | 9216 | 3584 | 3584 | 0 | 0 | 17 |
| td_teacher | 9216 | 3584 | 3584 | 0 | 0 | 17 |
| td_selfplay | 9216 | 3584 | 3584 | 0 | 0 | 0 |

Остальные исходы — loss. В matched cap2:3567 loss и17 goal у каждого arm; в selfplay cap2:3584 loss. Goal/loss пересчитаны по конечным board: точные три rank11 либо отсутствие legal move по правилам Bonk. Все boards/caps, snapshots, finite seconds и счётчики updates согласованы.

| Arm | Завершённые ходы | Updates |
|---|---:|---:|
| mc_teacher | 9981988 | 9981971 |
| td_teacher | 9981988 | 9981971 |
| td_selfplay | 5536328 | 5536328 |

У всех **16384 matched пар** совпали trajectory_digest, board, moves, result и first_caps. Первые2048 warmup-эпизодов selfplay также совпали с td_teacher по этим полям и updates. Это соответствует детерминированному teacher/model-off; отдельного проигрывания траекторий аудитор не выполнял.

Curriculum соответствует фиксированной формуле:2048 full warmup, затем phase0 cap1/phase1 cap2/phase2–3 full. Фактически использовано32 из64 cap1 snapshots и59 из59 cap2 snapshots; это следствие заранее заданного seed/index mapping, не нарушение записанного алгоритма. Покрытие cap1 не следует описывать как использование всех64 starts.

Общее число training **arm-episodes49152**. Уникальных training seed16384; повторение между matched arms и selfplay намеренное. Curriculum continuations не являются полными играми с двух начальных плиток, а matched результаты не являются независимыми наблюдениями друг от друга.

## Validation, остановка и выбранные веса

У каждой группы фактические checks — **0,2048,8192,16384**. Каждый arm/check имеет192 observe validation rows:64 full,64 cap1,64 cap2, в точном порядке заданных seed/start. Всего **2304** rows. Проверены complete, zero updates, отсутствие limit, header source/model/pool SHA, исходы по boards и заново вычисленные metric tuples.

**Все12 arm/check validation tuples равны[0,0,0].** Совместная matched metric и selfplay metric также нулевые. Отбор с сохранением earliest tie закономерно оставил count0. Stalls3, trained checks3: правило минимум3 checks и минимум2 последовательных stall выполнено на16384. Stop reason `plateau` соответствует объявленному локальному критерию; cap131072 не достигнут.

Проверены15 referenced binary:12 milestone-моделей и3 active current-модели. Header BONKTD01/target2/CAP11/GOAL3, CRC, canonical metadata, payload SHA, finite float32, run/arm/episode metadata и parameter SHA согласованы. Current16384 совпадает по параметрам с последним milestone соответствующего arm. Bias у моделей остаётся0.5.

Количество ненулевых весов на16384: MC141034, TD-teacher141020, TD-selfplay143605. Следовательно обучение не было пропущено; оно не дало выбранного validation улучшения. Выбранные count0 имеют нулевые веса и общий parameter SHA:

```text
76858f880e6c5408fcae57e7b707ca77dd553ba522f1edaf1865fae70a0f9379
```

Таким образом все три выбранных evaluator побитно одинаковы по параметрам и дают исходный clipped-linear prior V=0.5 на nonterminal. Предстоящий screen этих selected файлов проверяет этот исходный evaluator, а не веса последнего обученного checkpoint. Разные file SHA отражают arm/run metadata и не означают разных выбранных весов.

## Provenance и сводка

Фактические используемые pool/train/validation ranges попарно не пересекаются (кроме намеренного общего training prefix arms) и не пересекаются с426 seed сохранённого исторического inventory. Cap-validation начинает из того же train-only pool; это tuning, не независимый full-game контроль.

Все producer file SHA совпали с текущими файлами; run.py дополнительно сверен с blob88063e1, plan — с blobe05ed8e. Оба train state и все validation headers используют тот же source fingerprint/pool SHA. Реальный trainer8abc4fc7… совпал с build manifest и сохранённой копией; inference binary и его backup также совпали с provenance. В native ничего не загружалось/исполнялось для этой проверки.

Независимо рассчитанные counts, outcomes, moves, updates, checks, selected, stalls и matched identity совпали с `training-summary.json`. Поле producer `complete_resume_validated` само по себе не использовалось как доказательство запуска resume; команды координатора аудитором не повторялись.

```text
pilot-pool-512.json  b0c568b9edbf16735bcd6312e6041a9c6c3c072bb0bb7d19859c36545e041953
pool.json            95f1f3a1900e1e648f7372fa50b484db1ce9f2bf3a508a6b91fbeb15c917a90b
train-matched.json   b49d03477801c87b3ea6367d196865ca0eb569f9a761914f7cfb2407205787e7
train-selfplay.json  7075aa2dcee3fae5c277630d812502ea9e02c7e908b903cbed53e529f6f5571e
training-summary.json 6ee7f7ffe879d2aa8a8b8fffb417269a6bba6cb1d02dd9a5baae7a58dc362d25
```

## Граница вывода

Ни training goal17 из cap2 continuations, ни нулевой validation не являются независимым сравнением search-policy из полного старта. Нулевые tuples не устанавливают причину отсутствия улучшения: этот аудит не приписывает результат только размеру validation, teacher, архитектуре или TD в целом. Зафиксирован конец конкретного ограниченного цикла по его собственному правилу, не глобальная оптимальность или непригодность TD/ML.

Сохранённые отрицательные результаты и selected0 следует оставить неизменными. Сверка подтверждает готовность зафиксировать именно эти результаты обучения; вывод о независимом контроле и его статистике будет отдельным после timed этапов.
