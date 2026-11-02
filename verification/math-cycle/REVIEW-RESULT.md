# Независимая приёмка математического контроля

Проверен результат `a0a101bc27cd24642a8027fc587940e3a4178b9c`, pre-control freeze `78657bd37dfaac064387824ef25ec158e58d1739`, producer sources `7b561fbcee3c34fb4ab83811473066946469ae35`. Анализ только файлов: продукт и данные не менялись, native код, игры, fit, build и testsuite не запускались. Текущие изменения README/MATHEMATICAL.md принадлежат координатору и не входят в frozen producer.

**Дефектов, блокирующих сохранение этого опыта как отрицательного независимого контроля, не найдено. Прирост побед не подтверждён; оснований принимать новые веса нет.**

## Независимый пересчёт raw

Использован собственный pure-Python reference движения: сжатие каждой линии, однократное слияние одинаковых уровней строго ниже11, сравнение нормализованных tuple boards. Цель — минимум три плитки11; loss — отсутствие всех четырёх изменяющих поле ходов. Во всех176 raw записях outcome/caps согласуются с final board. Все победы заканчиваются ровно тремя11; limits отсутствуют. Проверены диапазоны moves, telemetry/depth sums и нетерминальные cap snapshots. Source branch до spawn на winning afterstate сохранён без изменений; полные траектории raw не содержат и повторно не исполнялись.

Screen: ровно56 записей, восемь seed1600000000..1600000007, все семь arms на каждом seed в предписанном вращающемся порядке.

| Arm | Goal / loss | Choose wall / move, ms |
|---|---:|---:|
| baseline | 3 / 5 | 4.331670 |
| empty_r1 | 6 / 2 | 4.264437 |
| mono_r1 | 3 / 5 | 4.347303 |
| merges_r1 | 5 / 3 | 4.372031 |
| empty_r2 | 3 / 5 | 4.275159 |
| mono_r2 | 6 / 2 | 4.318066 |
| merges_r2 | 5 / 3 | 4.369017 |

Выбран `empty_r1`: он и mono_r2 имеют по6 побед, у empty_r1 ниже заранее объявленная фактическая стоимость хода. Результат раундов пересчитан: best3→6 в round1 и6→6 в round2. Поэтому `bounded_neighborhood_exhausted` корректен; условие `local_plateau` не выполнено. Это завершение фиксированного списка вариантов, не достижение плато или глобального оптимума.

Control: ровно120 записей на60 seed1700000000..1700000059, по одной baseline/finalist на seed, порядок первого label чередуется. Все source_arm соответствуют baseline/empty_r1; повторов и пропусков нет.

| Label | Goal / loss / limit | Win rate | Moves | Сумма choose wall, s | Choose wall / move, ms |
|---|---:|---:|---:|---:|---:|
| baseline | 31 / 29 / 0 | 51.6667% | 114418 | 489.173835073 | 4.275322371 |
| finalist | 32 / 28 / 0 | 53.3333% | 118032 | 494.858424946 | 4.192578495 |

Парная таблица: обе победили16 раз, обе проиграли13 раз, finalist выиграл16 discordant pairs и проиграл15. Exact one-sided binomial tail по31 discordant pairs даёт **1/2 =0.5**. Разность+1 победа не проходит p<=0.05. Самостоятельно полученные gained/lost seed списки, исходы, агрегаты и false `confirmed_improvement` совпали с summary; summary прочитан для сравнения после пересчёта.

Стоимость рассчитана из summed measured choose wall / completed moves, а не из nominal10ms и не из полного времени партии. Отношение finalist/baseline **0.9806461668023577**, разница−1.9354%, внутри заранее объявленных±10%. Общий wall партий502.899874412s и508.186019207s соответственно. Candidate суммарно выполняет больше ходов, поэтому большая суммарная search wall не означает более дорогой ход. Из этого измерения не следует ускорение на телефоне или win-rate при10s/ход.

## Seed и происхождение

- Независимо проверены пересечения с135886 `seen_seeds` зафиксированного inventory: screen и control не пересекаются ни с ним, ни друг с другом. Все raw seed точно входят в объявленные диапазоны. Inventory покрывает перечисленные исторические JSON; неизвестные внешние seed этим утверждением не охватываются. Старые26/60 остаются историей: новый парный контроль сравнивает31/60 с32/60.
- Все10 source fingerprints screen/control/actual build manifest равны друг другу, SHA фактических файлов и bytes из `7b561fb`. План, inventory, spawn JSON, producer, bot/benchmark, core и headers не менялись после producer freeze.
- Проверен **точный набор26** обязательных freeze paths и каждый фактический SHA: источники, raw screen, actual build manifest, семь generated C++ sources и семь native libraries. Header control и summary содержат фактический SHA finalist `59da6590d66a5e308ff10518b554358a1d397ac1f5aebe9cda7d7e4649107d8b`; SHA control в summary также совпадает.
- Actual manifest и семь generated sources побайтно совпадают с receipts, сохранёнными ещё в pre-game commit `fd7d1cc40f30ea76991398a72e3f8a52723554a2`. Screen и finalist побайтно равны версиям из `78657bd`; control/summary в том commit отсутствуют. Result commit `a0a101b` — непосредственный потомок этого pre-control freeze. Это согласованная цепь producer → pre-game engines → screen/finalist → control.
- Все семь generated sources заново сопоставлены с pinned pure e28 и заранее объявленной заменой ровно одной константы. Выбранный empty_r1 содержит EMPTY310.5; его native SHA `be310b9ec4e041f3fb7e8a5973f329b8157742514b67c8c57f39d5ae11783147` совпадает с frozen manifest. Generated baseline и текущий production core побайтно равны e28, EMPTY270; core SHA `257050c392bf3a743f894b0f674ff8d7da5c8f2165e2dff003f0e3b4192cb24d`. Остальные веса/правила, cache/progressive и spawn модель сохранены. ML includes, loaders и learned-evaluation branches отсутствуют во всех generated sources.

Приёмка подтверждает внутреннюю согласованность сохранённых исходников, binaries по SHA, raw результатов и preregistered статистического решения. Библиотеки не исполнялись и игры не воспроизводились. Результат поддерживает сохранение pure baseline с EMPTY270; он не доказывает невозможность улучшения ручной эвристики и не устанавливает причину прежних ML-результатов.
