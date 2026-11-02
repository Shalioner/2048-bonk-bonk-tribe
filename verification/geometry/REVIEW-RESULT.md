# Независимая приёмка geometry-результатов

Проверен точный data commit `17a7cbbbeb38e6ac2b60b0c040c767a797cacf4d`, pre-control freeze `3bc8e09185f95c0f9a5df5cda2aa370550930525`, pre-game provenance `11b0ea7462c655444f4d77d260d96806c9528220`, producer `8d1dfb2c55cc449e74b07eca12cd26f2885ca518`. Анализ только файлов и чистой генерации source bytes: native, игры, build, fit и testsuite не запускались; продукт и данные не менялись.

**Блокирующих несоответствий raw, протоколу и происхождению не найдено. Подтверждённого улучшения стратегии нет.** Выбран baseline; оба control label используют одну и ту же baseline-сборку. Их разные результаты не являются эффектом features или сравнением двух разных стратегий.

## Raw и отбор

Все204 записи проверены собственным pure-Python reference движения, без импорта native bot: сжатие линий, однократное слияние одинаковых рангов строго ниже11, сравнение tuple boards. Goal — минимум три11; loss — отсутствие всех четырёх изменяющих поле ходов. Outcomes/caps согласуются с final board во всех записях. Проверены move bounds, depth/telemetry sums и нетерминальные cap snapshots. **Limits0**; максимальное observed moves2308 ниже6000. Полных траекторий raw не содержит; правило отсутствия spawn после winning afterstate подтверждено неизменённым reviewed source, а не повторной игрой.

Screen содержит ровно84 записи:12 seed1800000000..1800000011×7 arms, без пропусков и повторов, с точной объявленной ротацией порядка.

| Arm | Goal / loss | Choose wall / move, ms |
|---|---:|---:|
| baseline | 9 / 3 | 4.086323 |
| segments_r1 | 4 / 8 | 4.247137 |
| geometry_r1 | 8 / 4 | 4.092426 |
| risk_r1 | 6 / 6 | 4.095209 |
| segments_r2 | 0 / 12 | 4.171556 |
| geometry_r2 | 5 / 7 | 4.121975 |
| risk_r2 | 3 / 9 | 4.279515 |

Отбор baseline корректен: он единственный имеет9 goals. Пересчитанные rounds:9→9 (improved=false), затем9→9 (false). Поэтому `local_plateau` соответствует заранее объявленному операционному критерию **для этих шести формул/strength и этого screen**. Это не доказательство глобального оптимума, статистического превосходства baseline над всеми альтернативами или невозможности улучшения другими ручными формулами.

## Control: два label одной baseline

Ровно120 записей,60 seed1900000000..1900000059, по одному baseline/finalist на seed; первый label чередуется. У **всех120** `source_arm="baseline"`. Выбранный finalist в frozen record — также baseline; фактическая baseline library одна, SHA256 `ec74e617df06bf0e71936aaa4974f34a59061198bb804eb8bbc2470acc4a3cf2`.

| Label | Goal / loss / limit | Win rate | Moves | Сумма choose wall, s | Choose wall / move, ms |
|---|---:|---:|---:|---:|---:|
| baseline | 34 / 26 / 0 | 56.6667% | 117317 | 484.984655302 | 4.133967416 |
| finalist | 24 / 36 / 0 | 40.0000% | 115245 | 488.568311206 | 4.239388357 |

Пересчитаны все60 пар: обе победили13 раз, обе проиграли15; gained11, lost21. Exact one-sided binomial tail по32 discordant pairs равен **4187373083/4294967296 =0.9749487701337785**. Это техническая статистика label-пар, а не свидетельство различия стратегий. `paired.improved=false` и `confirmed_improvement=false` корректны; дополнительно nonbaseline admission невозможен уже по выбору baseline.

Сумма measured choose wall / completed moves даёт ratio **1.0255011544068613**, то есть+2.5501%, внутри объявленных±10%. Total wall партий508.766717169s и514.052339169s. Номинальные10ms не подменяют эту фактическую стоимость. Это также не стоимость новых features в независимом контроле: оба label здесь baseline. Причина разброса исходов одной сборки этим опытом не изолирована; эффект features, нейропорта или установленная причина не заявляются.

Summary использован для сравнения только после независимого raw-пересчёта. Совпали выбор, обе rounds, stop_reason, полные gained/lost seed списки, exact p, outcomes, moves, wall, depth counts, positions, cost ratio и оба итоговых boolean flags.

## Независимость и freeze

- Screen/control seed множества точны и не пересекаются друг с другом, с135954 `seen_seeds` inventory или со старыми math ranges1600000000..1600000007 и1700000000..1700000059. Старые ranges присутствуют в inventory. Граница доказательства — перечисленные исторические JSON; неизвестные внешние seed не объявлены покрытыми.
- Все12 producer fingerprints screen/control/actual manifest совпали с фактическими файлами и bytes из `8d1dfb2`: runner, features, plan, inventory, bot/benchmark, spawn JSON, core и headers. Проверен **точный набор28** mandatory freeze paths и каждый SHA, включая raw screen, actual manifest, все семь generated sources и все семь libraries.
- Actual build manifest и семь C++ sources побайтно совпадают с receipt-файлами из pre-game commit `11b0ea7`. Generated source каждого arm совпадает с заново выполненной pure-Python генерацией из pinned e28 и зафиксированного plan; реальные library SHA соответствуют manifest/freeze.
- Raw screen и finalist побайтно совпадают с `3bc8e091`; control/summary в этом commit отсутствуют. Result `17a7cbbb` — его непосредственный потомок. SHA finalist `a00581a3407d1f857a1a716df791cf92f8794acd608467b880cde3cd51c394a9` совпадает с control/summary headers; SHA control `2496a7ebd1d26ddb75b9290ef1380e1f9838277b67c9021d30c093af60cc8286` совпадает с summary.
- Generated baseline и production core побайтно равны pure e28, core SHA `257050c392bf3a743f894b0f674ff8d7da5c8f2165e2dff003f0e3b4192cb24d`. ML includes/loaders/learned evaluation отсутствуют во всех generated sources. Правила, spawn модель, cache/progressive и прежнее signed-max поведение не менялись. Новые формулы не установлены как default.

Приёмка подтверждает согласованную сохранённую цепь producer/provenance → screen → baseline freeze → отдельные control seed → отрицательное решение. Библиотеки проверены по файлам/SHA без исполнения; игры не воспроизводились. Дополнительные партии для этого решения не требуются. Допустимый вывод — сохранить pure baseline и завершить этот заранее ограниченный geometry-цикл по его локальному критерию, без заявления прироста win-rate или переноса цифр на телефон/10s.
