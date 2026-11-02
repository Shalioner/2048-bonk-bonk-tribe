# Независимая приёмка результатов альтернатив поиска

Проверен data commit `035e2140e9563b183f8ebde513f7e50b58da4571`: pre-game freeze `bf53e44a38cb1d36a059a7063c582f322a323cbb`, screen/finalist freeze `e8ea1c823c2cc108ed1242318042023c56db7d0b`, producer `537936af3d05b4f8a69a4764cadcf2d54c8467ed`. Дополнительно прочитаны docs/restoration из `eda79b5`/`489fa64` и актуальный README diff.

**Блокирующих несоответствий данных, протоколу или происхождению не обнаружено. Заданный статистический критерий роста выполнен:49/100→63/100, exact paired p≈0.02704. Это результат другого ресурсного режима с wall/ход×1.5948 и CPU/ход×1.5620; преимущество при равной цене или прежнем per-call cap10ms не доказано.**

Native, поиск ходов, игры, build, fit и testsuite здесь не запускались. Собственная проверка — чтение файлов, pure-Python reference/recount и чистая генерация source bytes. Код и данные не менялись.

## Независимый raw-пересчёт

Все280 final boards проверены собственным Python reference движения, без импорта native bot: сжатие линий, однократные слияния одинаковых рангов строго ниже11, сравнение tuple boards. Goal — count11>=3; loss — отсутствие любого изменяющего поле направления. Outcome/caps согласованы во всех записях, limits0, observed moves460..2323. Сверены depth/threshold buckets, samples, cap snapshots и numeric telemetry. Никакие медленные наблюдения не исключались.

Screen:16 seed2400000000..2400000015×5 arms=80 игр, точный объявленный rotating order без пропусков/дубликатов.

| Arm | Goal / loss | Choose wall / move, ms |
|---|---:|---:|
| baseline | 7 / 9 | 4.394972 |
| canonical | 8 / 8 | 4.283958 |
| serial_shallow | 6 / 10 | 4.299293 |
| precision | 5 / 11 | 5.647133 |
| banked_endgame | 11 / 5 | 8.004102 |

Выбор banked_endgame корректен: максимум11 goals. Best по четырём раундам:7→8 (рост),8→8,8→8,8→11 (рост). `bounded_neighborhood_exhausted` соответствует plan; `local_plateau` нового набора не достигнут. Четыре screen arms не выдаются за четыре независимых подтверждения.

Control: ровно100 новых seed2500000000..2500000099 и200 игр, по baseline/finalist на seed, первый label чередуется. У100 baseline rows source_arm=baseline; у100 finalist rows source_arm=banked_endgame. План N100 сохранён до screen/control; расширения выборки по результату не обнаружено.

| Label | Goal / loss / limit | Moves | Choose wall sum, s | Choose CPU sum, s |
|---|---:|---:|---:|---:|
| baseline | 49 / 51 / 0 | 196444 | 845.933750638 | 2272.774039000 |
| banked finalist | 63 / 37 / 0 | 205340 | 1410.165531760 | 3710.756484000 |

Парная таблица: обе победили33 раза, обе проиграли21; gained30, lost16. Exact one-sided tail по46 discordant pairs равен **951315613005/35184372088832 =0.027038015929434778**. Небазовый finalist, больше goals, p<=0.05 и zero limits выполняют заранее заданный gate. Полные gained/lost seed lists и `confirmed_improvement=true` совпали с summary, который сравнивался только после собственного raw-пересчёта.

## Фактическая цена и ledger

| Control measurement | baseline | banked |
|---|---:|---:|
| Choose wall / completed move, ms | 4.306233586 | 6.867466308 |
| Choose CPU / completed move, ms | 11.569577279 | 18.071279264 |
| Wall median, ms | 3.551209 | 5.832542 |
| Wall p95 nearest rank, ms | 8.036208 | 20.193000 |
| Wall maximum, ms | 118.017042 | 168.107917 |
| Calls strictly above10ms | 0.0972035% | 17.2838244% |
| Максимальный supplied budget, ms | 10 | 40 |

Wall ratio **1.5947732910092545**, CPU/move ratio **1.5619653880033155**; actual-cost-within±10%=false. Это соответственно+59.48% wall и+56.20% CPU на completed move. Разница суммарных затрат дополнительно включает разное число ходов. Total game wall867.601348998s/1433.221301752s также пересчитан.

Для каждого choose call, включая terminal None, независимо replay начисления10ms, phase cap10/40ms, списания measured wall и final credit с tolerance1e-10. Проверены длины wall/budget/decision_caps, монотонность caps0..2 и их связь с final caps. Во всех bank rows replay согласован; в этих записанных последовательностях отрицательного prefix credit/zero supplied budget не возникло, минимальный final credit контроля2.914797095s. Отрицательный долг сохраняется кодом и ранее проверенными mocks, но его ветвь не была вызвана этим реальным sample. Полных board trajectories нет: ledger replay доказывает внутреннюю согласованность телеметрии, не каждое фактическое историческое движение.

Отдельная граница задержек: в контроле **4 baseline и6 bank calls** превысили именно supplied budget+50ms; максимальный excess108.017042/128.107917ms. Это совпало с `actual-overruns.json`. Unit-гейт на малом корпусе не является общей гарантией wall bound реальной серии. В screen также были выбросы: bank maximum204.032333ms, precision231.265708ms; контрольные118/168ms нельзя выдавать за максимум всех280 игр. Эти наблюдения сохранены и вошли во все суммы/quantiles.

Собственный recount всех агрегатов дал точное совпадение с frozen screen table/summary: wall/CPU, median/p95/max, above-nominal rate, depth/threshold counts, positions, profiles, budgets и balances. Сравнения с той же actual cost и с равномерным увеличением бюджета отсутствуют; эффект распределения времени отдельно от дополнительной цены не изолирован.

## Независимость и provenance

- Новые screen/control seed множества точны, взаимно disjoint и не пересекаются с136026 seen inventory или прежними math1.6/1.7 и geometry1.8/1.9 ranges. Старые ranges присутствуют в inventory. Seed — Python ints, включая значения>int32; native RNG truncation в producer отсутствует. Неизвестные внешние seed вне покрытия inventory.
- Все13 source fingerprints равны в screen/control/actual manifest, фактическим файлам и bytes producer `537936a`. Проверен **точный mandatory keyset25** и каждый фактический freeze SHA: source/plan/inventory/spawn/headers, raw screen, manifest и все пять source/native pairs. SHA finalist `4bbbe0a61693e07dbc8a9d8a0e16af8417a76017e611975ad0ef50976df94df9` и control `feeac75a6db73382ab10d64439b6a60a98f6e69947c7ac5fe4de36b37afcd14c` совпали с headers/summary.
- В pre-game `bf53e44` уже сохранены plan N100 и build receipts; игровых файлов тогда нет. Все пять generated sources побайтно совпадают с receipts из этого commit и собственной текущей генерацией. Manifest JSON совпадает со snapshot; receipt имеет лишь дополнительный завершающий newline, а точные raw bytes рабочего manifest отдельно закреплены freeze SHA.
- Screen/finalist побайтно равны `e8ea1c8`, control/summary в нём отсутствуют. Data `035e214` — непосредственный потомок этого freeze. Источник выбран до контрольного commit и не пересобран/перевыбран по контролю.
- Baseline и banked native sources побайтно равны pure e28; production core SHA `257050c392bf3a743f894b0f674ff8d7da5c8f2165e2dff003f0e3b4192cb24d`. Два library files имеют свои зафиксированные SHA, но одну формулу/правила; различается frontend budget profile. ML включает/загрузчики/learned evaluation отсутствуют во всех generated sources. Эвристика, CAP/goal/spawn и signed-max не исправлялись этим опытом.

## Дополнительная сверка handoff/claims

Прочитаны restore_build.py и restoration logs. Скрипт восстанавливает проверенные архивные bytes и exact manifest без пересборки; существующий build только проверяет. Самостоятельно сверены все пять sidecar libraries с frozen manifest. В `restoration-lab` подтверждены HEAD=eda79b5, чистый Git, те же пять library SHA, manifest bytes и complete screen/finalist/control/summary. Повторно restore/recount/finalize не запускались. Сохранённые логи заявляют280/25SHA и finalize без новых игр; source complete/validate-only путь этому соответствует.

Docs/README корректно отделяют+14pp при другом resource profile от равной цены, uniform bigger-budget baseline, телефона/default10s и локального плато. `/usr/bin/time -l` сведения920.20s/2490.64s и maximum RSS105136128/155123712 bytes совпадают с raw execution logs; это память всего runner/checkpoints, не бота/телефона.

Итоговая приёмка подтверждает статистический успех **banked resource profile относительно зафиксированной базы в этом офлайн-контроле**, вместе с большей ценой и реальными latency overruns. Глобальная оптимальность, новое локальное плато, выигрыш при равной работе и готовность будущего launcher этим отчётом не подтверждаются; отдельный entrypoint diff ещё не предоставлялся.
