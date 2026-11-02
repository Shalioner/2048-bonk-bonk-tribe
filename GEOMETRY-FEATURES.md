# Структурные признаки для pure Bonk expectimax

Реализация ограничена `vendor/geometry_features.h`, генератором
`experiments/geometry/features.py` и его тестами. Product core, ABI2, правила,
cache, progressive/legacy и прежние игровые данные не изменены. ML backend,
обучаемых весов, загрузчиков и новых зависимостей нет. Полные игры не запускались;
эффект на процент побед и фактическую стоимость проверяет отдельный runner.

## Контракт генератора

```python
variant_source(base_bytes, {"kind": "baseline", "strength": 0})
variant_source(base_bytes, {"kind": "segments", "strength": 0.5})  # или1
variant_source(base_bytes, {"kind": "geometry", "strength": 2000}) # или4000
variant_source(base_bytes, {"kind": "risk", "strength": 0.5})       # или1
```

`baseline` возвращает исходный bytes-объект без единой вставки. Каждый другой
kind добавляет include после2048.h и только собственную формулу. Нет сочетаний
семейств/динамических параметров. Неизвестные strength, нечисловые/неfinite
значения, повторная инъекция и несовпавший source anchor отвергаются. Сборка
использует `-I vendor`; происхождение исходного core фиксирует вызывающий runner.
Test-only exports добавляются лишь к временным исходникам внутри тестов.

## Три независимых семейства

**Segments0.5/1.** Строка делится на отрезки рангами>=11. В штрафе монотонности
используется `(1-s)*min(left,right) + s*sum_segments(min(left,right))`. Пустоты
внутри отрезков остаются обычными рангами0, CAP не превращаются в0, остальные
слагаемые и move tables не меняются. Без CAP возвращается прежний penalty
непосредственно, поэтому все14641 строк без рангов>=11 сохраняются побайтно в
float-таблице. Гипотеза: гибкость порядка вокруг уже готовых CAP. Риск: ослабление
полезного глобального порядка. Работа выполняется при init_tables, не в листьях.

**Geometry2000/4000.** Для каждого16-битного mask CAP>=11 предвычислены число
ортогональных рёбер между двумя non-CAP клетками (максимум24) и максимум при
таком числе CAP. К raw добавляется `strength*(freeEdges(mask)-maxEdges[nCAP])`.
Это<=0 и равно0 для лучшей геометрии данного числа CAP, D4-инвариантно. Не-CAP
означает как пустую, так и обычную занятую клетку. CAP остаются подвижными:
нет walls, pruning, нового terminal/loss или изменения допустимых ходов.
Гипотеза: избегание геометрии с меньшим пространством контактов. Риск: adjacency
является лишь proxy; движение CAP может быстро изменить конфигурацию. LUT:
65536+17 байт; инициализируется до потоков поиска, как таблицы исходного core.
cap-mask вычисляется по `v>=11 iff v3&&(v2||(v1&&v0))`; при0 CAP нет16-клеточного
цикла, при редких CAP перечисляются установленные nibble bits.

**Risk0.5/1.** При двух точных11 и<=3 пустотах R — вероятность отсутствия любого
легального хода непосредственно после следующего появления, с равномерной
пустой клеткой и текущей строкой stage spawn_table. Goal после spawn не loss.
Для raw>=0 итог `raw*(1-strength*R)`, отрицательный raw сохраняется побайтно.
Signed-max поведение исходного поиска не исправляется этим семейством.
Гипотеза: уменьшить немедленные тупики при двух CAP. Риск: одноступенчатая
оценка не отражает более дальние угрозы и добавляет работу в листьях.

R вычисляется точной закрытой формулой, согласованной до игровых данных:

1. При0 пустот нет события spawn, R=0; отдельный game/node код сохраняет свои
   проверки loss. При>=2 пустотах после одного spawn остаётся пустота. В связной
   сетке есть граница zero/nonzero, и плитка может сдвинуться в пустоту; CAP тоже
   подвижны. Значит R=0, включая2/3 пустоты.
2. При одной пустоте child полон. Полный board может измениться только слиянием
   соседней одинаковой пары ранга<11. Если такая пара уже есть среди занятых
   клеток, любое появление оставляет легальный ход, R=0.
3. Иначе легальный merge создаёт только spawn ранга<11, равного одному из
   ортогональных соседей пустоты. Появление11 создаёт третью11 и также безопасно.
   R — сумма вероятностей остальных рангов1..15. Clamp[0,1] устраняет только
   округление нормализованной float-строки вероятностей.

Это та же R, что полное перечисление spawn×четырёх execute_move. Наивный native
oracle с function pointer на execute_move сохранён в test-only экспорте, отдельно
есть независимый Python move/oracle. Production helper выполняет до24 adjacency
проверок и15 probability terms вместо до180 execute_move в изначальном прямом
перечислении3empty×15rank×4directions. Реальную цену всё равно измеряет runner.

CAP-barrier/mask использует>=11, как core запрещает merge для>=11. Goal и risk
gating используют ровно count(rank11), как исходный goal. Ранги12..15 проверены
как generic API boundary и не считаются готовой11. Зафиксированная default spawn
модель имеет ненулевые ранги1..5; эти generic случаи не объявляются измеренными
правилами телефона. При трёх11 leaf всегда возвращает1e9 до новых вычислений.

## Проверки

```sh
python3 -m unittest test_geometry_features -v
python3 -m py_compile experiments/geometry/features.py test_geometry_features.py
git diff --check
```

Тест строит baseline и6 вариантов в удаляемом временном каталоге своего worktree:

- Все65536 строки: native segmented penalty против Python, точная обратимость;
  обе strength; native row-table bit identity без CAP; move-left/right совпадают
  с baseline для каждой строки, включая ранги12..15.
- Все65536 masks: LUT против прямого edge reference, max по каждому CAP-count,
  отсутствие положительного penalty и все8 D4 transforms. Быстрый mask проверен
  на16ranks×16cells и4096 фиксированных random generic boards.
- Native/Python moves и R: boundaries0/1/2/3empty, default/custom spawn включая
  rank11/12/15, разные stage11/15 distributions, goal-no-loss. Закрытая формула
  сравнивается с native callback enumeration и Python oracle; отдельно проверены
 300 random1empty/2exact11 и200 random2/3empty generic boards.
- Positive raw, negative production raw и обе risk strengths; точная мягкая
  geometry поправка; все7 вариантов сохраняют Python move parity на snapshots.
- D4 leaf evaluations и goal bypass для всех вариантов. Физические LUT и
  segmented penalty сравниваются точно. Полная эвристика наследует float32
  сложение row tables и rank**3.5: изменение порядка суммирования уже в baseline
  не даёт bitwise symmetry. Для sampled leaf scores допуск max(2points,2e-6×abs)
  покрывает несколько float32 ULP; независимо R сравнивается с oracle до1e-7.
  В финальном прогоне измеренный максимум D4 spread0.25, row reverse0.125.
- Для всех6 новых вариантов:192 cached/reference score comparisons, два плотных
  board, depth2/3, threshold0/1e-4 и4 направления. Параметры header фиксированы
  сборкой, spawn фиксирован внутри поиска, cache(board,depth,prob) сохраняется.

MIT notice/origin производного row-вычисления сохранён в header. Источник —
nneonneo/2048-ai и локальная Bonk-адаптация; веса/политики других игр не переносились.
Правильность формул не доказывает увеличение win-rate или глобальную оптимальность.

Финальная проверка:11 тестов прошли за8.328s вместе со сборкой baseline+6 вариантов;
py_compile и diff-check прошли. Worktree product-core diff пуст. Временные native
артефакты автоматически удалены; actual screen/control не запускались.

Предданная оптимизация: exact_cap_count использует ту же nibble equality и
popcount, что count_cap core, вместо16 сравнений. Это по-прежнему точный rank11,
не маска>=11. Проверены16ranks×16cells, все counts0..16 и4096 random64-bit boards
против простого Python count. После изменения header все11 gates прошли за8.027s
со сборкой7 библиотек; py_compile/diff-check прошли, D4/row errors не изменились.
Скорость на реальных партиях этим тестом не измерялась.
