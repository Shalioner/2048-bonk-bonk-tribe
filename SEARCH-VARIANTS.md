# Isolated pure-math search alternatives

Реализация ограничена header, генератором и native/unit harness. Product core,
правила, цель, эвристика, growth, margin и ABI2 не изменены. Ни новых ML-компонентов,
ни зависимостей нет. Actual screen/control и полные партии не запускались.

## Контракт

```python
variant_source(base_bytes, {"kind": "baseline"})
variant_source(base_bytes, {"kind": "canonical"})
variant_source(base_bytes, {"kind": "serial_shallow", "serial_max_depth": 2})
variant_source(base_bytes, {"kind": "precision"})
variant_source(base_bytes, {"kind": "banked_endgame"})
```

Baseline, precision и banked_endgame возвращают тот же исходный bytes-объект.
Precision thresholds и banked time allocation принадлежат отдельному frontend,
не native-генератору. Обычные thresholds —1e-4/1e-5/1e-6, precision —1e-5/1e-6.
Metadata не участвуют в native source. Serial допускает только фиксированный
max_depth2. Неизвестный kind, повторная инъекция или несовпадение source anchors
отвергаются. Сочетаний canonical/serial нет. Сборке canonical нужен `-I vendor`.
Header содержит MIT attribution исходного nneonneo/2048-ai и локальной адаптации.

## Canonical D4

После goal/deadline/leaf-cutoff, непосредственно перед созданием cache key,
`board` заменяется минимальным packed uint64 среди восьми D4 transforms. Header
реализует собственные bitboard transpose/horizontal/vertical в namespace;
обновляются и key, и фактически раскрываемый board. Это выполняется независимо
от cache_enabled, поэтому reference использует тот же порядок раскрытия.
Leaf cutoff возвращает прежнюю оценку без канонизации, goal сразу возвращает1e9.

Ключ по-прежнему `(board, remaining_depth, floatbits(cprob))`, при threshold0
probabilitybits0. Cache остаётся локальным eval_state. Параметры варианта фиксированы
сборкой, spawn distribution не меняется внутри поиска. Uniform cell choice,
max-rank-indexed spawn и полный максимум по четырём направлениям D4-инвариантны;
при иной модели зависимости от клетки это обоснование потребует пересмотра.

Математическое expectimax значение сохраняется. Float32 row sums и накопление
ожидания могут идти в другом порядке: битового равенства с baseline не обещаем,
на очень близких root values может измениться tie/argmax. Сравнение с baseline
и независимым Python high-precision value reference использует допуск
`max(1point, abs(value)*1e-6)` — несколько float32 ULP, а не допуск изменения
правил. Для canonical cached/reference требуется **точное** равенство, поскольку
оба обхода раскрывают один и тот же canonical board. На проверенных случаях
root D4 values и множества argmax также совпадают точно при depth>=2.

Цена: восемь bitboard transforms на раскрываемый chance node, включая cache hits;
возможный выигрыш — больше совпадений состояния внутри того же direction cache.
Нет общего cache между направлениями и нет обещания прироста win-rate.

## Serial shallow

Изменён только progressive pass: общий lambda body оценивает направление с новым
eval_state и прежними pre/post deadline checks, stop flag, threshold и depth.
На pass_depth<=2 lambda вызывается последовательно для всех allowed directions;
на depth>=3 используется прежняя схема потоков, до четырёх. Fallback depth1,
growth prediction, reserve margin, discarded incomplete passes, выбор последнего
полного результата и info[0..9] сохраняются. solve_budget, fixed-depth scoring,
goals и spawn functions остаются прежними. Их fixed-depth values побитово
совпадают с baseline в native gates.

Гипотеза: убрать создание/освобождение потоков там, где поиск дешевле scheduler
overhead. Риск: serial depth2 может проиграть по wall time при дорогих boards;
фактическую цену и завершённые глубины должен измерить отдельный игровой runner.

## Проверки и воспроизведение

```sh
python3 -m unittest test_search_variants -v
python3 -m py_compile experiments/search_alternatives/variants.py test_search_variants.py
git diff --check
```

Тест собирает три разные native реализации в удаляемом каталоге своего worktree.
Precision/banked backend identity проверяется по исходным bytes; их frontend
состояния проверяет другой модуль. Test-only diagnostic exports дописываются
лишь к временным generated sources и не меняют production ABI.

- Native D4 против Python permutations: все64 single-bit boards, distinct ranks
  и128 fixed-random64-bit boards, все8 transforms, direction/move equivariance.
  В первом прогоне этот gate выявил два ошибочно перенесённых transpose mask;
  masks исправлены точно по core до сдачи, tolerances не ослаблялись.
- D4 root values и полные argmax sets, включая симметричный tie board.
- Canonical cached/reference exact; serial/base fixed values exact. Witness,
  cap1, cap2 и generic rank15, depths2/3/4, thresholds0/1e-4/1e-5.
  Для unpruned sparse witness задана явная двухранговая stage11 distribution,
  чтобы unit не превращался в длинный benchmark. Stage15 имеет другую строку.
- Независимый Python move/heuristic/expectimax reference с double accumulation,
  float32 probability context и тем же leaf cutoff/signed-max convention;
  плотные cap2/generic15, depths2/3, thresholds0/1e-4.
- Прежний leaf cutoff/goal bypass, fallback при0budget, precision first threshold,
  успешные depth1/depth3 refinement passes, abort с сохранением полного уровня.
- 45 hard-budget calls: три backend×три boards×1/5/10/20/40ms. Для каждого
  `elapsed <= requested_budget+50ms` без ослабления прежних четырёх budget gates.
  Returned move дополнительно сверяется с fixed depth/threshold последнего
  завершённого прохода. Target30/threshold1e-12/growth0 принудительно проверяет abort.

Небольшой synthetic корпус и успешные дедлайны не гарантируют тот же wall time
на всех boards/нагрузках системы. Default/game policy этим коммитом не выбирается;
положительный или отрицательный результат и actual-cost claim требуют отдельного
зафиксированного screen/control. Исторические данные и телефон не изменялись.

Финальный native/unit log (включая три isolated builds):

```text
Ran 9 tests in 11.056s
OK
max_baseline_value_delta = 0.625
max_python_reference_delta = 0.19276220398023725
max_positive_budget_excess_s = 0.0
```

py_compile/diff-check прошли. Product-core diff пуст; временные native артефакты
удалены, полные игры/screen/control не запускались. Статистика выше относится
к unit-корпусу и не является оценкой игрового преимущества.
