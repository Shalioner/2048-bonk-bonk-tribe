# Структурная математическая эвристика без ML

Проверены три новых механизма, по два варианта каждого. **Ни один не превзошёл
базу в отборе; рабочая формула оставлена прежней.** По заранее заданному правилу
два раунда без роста завершили локальное плато этой ограниченной окрестности.
Это не глобальная оптимальность и не доказательство бесполезности других идей.

Ветка `local/strategy-geometry-20261102` от `08b8b95`, отдельный worktree.
Предыдущая pure-math версия и опыт31/60 против32/60,p=0.5 сохранены в старой
ветке/worktree. ML-оценок, обучения и загрузчиков весов в решателе нет.
Модель появлений и обновление наблюдаемых частот сохранены. Телефон, ADB,
runtime merge-game-bot, чужие изменения main, память Codex/Choirboy не менялись.
Публикаций, установок и платных API не было.

## Формулы

| Направление | Варианты | Механизм и цена |
|---|---|---|
| Сегментная монотонность | смешение0.5/1 | Считать penalty отдельно на отрезках между рангами≥11; меняется только предрасчёт row tables |
| Геометрия CAP | штраф2000/4000 | Число ортогональных рёбер между non-CAP клетками относительно максимума при том же числе CAP; быстрый mask/LUT на листе |
| Риск немедленной блокировки | strength0.5/1 | При двух exact11 неотрицательный raw умножается на1−strength×P(loss после одного spawn); закрытая формула на листе |

Каждый вариант включает один механизм, без комбинаций. CAP остаются подвижными:
геометрия — мягкий score, а не непроходимые стены/запрет ходов/новый loss.
Сегментация не обнуляет CAP и сохраняет обычные ранги внутри каждого отрезка.
Без CAP row formula побитно прежняя. Goal3×11 остаётся1e9, переходы, cache,
progressive, deadlines и search ABI не меняются. Generic ранги12..15 также
не сливаются и являются barriers; goal/risk gating используют точный rank11.

Формула риска использует текущую stage-specific spawn-модель. Если после spawn
остаётся пустая клетка, на непустом связном поле есть граница empty/occupied,
а значит легальный сдвиг. Поэтому при двух и более пустотах до spawn R=0.
При одной пустоте child полон: его может изменить только соседняя одинаковая
пара ранга<11. Если такая пара уже существует, R=0; иначе безопасны только
ранги подходящих соседей пустоты и11, дающая третью CAP/goal. Вероятности
остальных рангов суммируются. При0 пустотах нет spawn-события, helper возвращает0.
Это не новый terminal rule. Для отрицательного raw сохраняется прежняя шкала:
старое поведение best=0 не исправлялось этим опытом.

Production helper не перебирает execute_move: его closed form сверена с прямым
oracle. Корректность R условна относительно принятых правил и spawn-вероятностей,
она не означает калиброванной оценки вероятности полной победы.

## Проверки до партий

Исходники: [geometry_features.h](vendor/geometry_features.h),
[features.py](experiments/geometry/features.py),
[GEOMETRY-FEATURES.md](GEOMETRY-FEATURES.md).

32 новых теста (11 native/features и21 runner) прошли на root за10.768с:
семь isolated builds, exhaustive65536 rows,65536 masks/max-by-count/D4,
all16ranks×16cells и4096 random board mask/count, risk oracle/Python,
192 cache/reference comparisons, goal/no-spawn, limits, resume/freeze/statistics.
Прежние84 pure-math gates не ослаблялись; неизменённые проверки без причины
не повторялись. Всего116 уникальных пройденных тестов в цепочке.

Независимая проверка формулы риска:7000 generic boards,150000 spawn children
по рангам1..15, совпадение с прямым Python движением/goal. Это синтетические
состояния, не полные партии или новый win-rate контроль. Скрипт и результат:
[lemma_check.py](verification/geometry/lemma_check.py),
[lemma-check.json](verification/geometry/lemma-check.json).

Baseline producer возвращает исходные bytes e28 без include/инъекций. Его новая
DLL на31 прежнем поле/124 depth-threshold случаях дала побитно те же четыре
оценки и переходы, что прежний no-ML движок. Весь float32 score сохраняет
rounding: maximum D4 spread0.25,rowreverse0.125; сами дискретные формулы
симметричны. Эти допуски не выдаются за bitwise D4 всего score.

[Предданное независимое ревью](verification/geometry/REVIEW-PROTOCOL.md) проверило
формулы, wide-count identity, producer/cache/source и протокол до данных.
Фактические семь root DLL проверены API2/CAP11/GOAL3/progressive/noML и SHA.
Source snapshots, compiler/flags, manifest, provenance и логи находятся в
verification/geometry; binary backups — в соседнем ../native-engines.
Pre-game commit: `11b0ea7`. Production core остаётся побайтным e28.

## Отбор и локальное плато

[План](experiments/geometry/plan.json) зафиксирован до партий. Inventory:
1822 исторических JSON,135954 наблюдавшихся/зарезервированных seed,errors0.
Это не число сыгранных партий. Новые диапазоны не пересекаются с доступным
inventory, друг с другом или старыми math1.6/1.7 диапазонами; неизвестные
внешние прогоны не объявляются покрытыми.

Screen:12 новых seed `1800000000..1800000011`,7 arms,84 полные партии.
Строго serial, ротация arms по seed; progressive,depth8,10мс/ход,max_moves6000.
Тяжёлых фоновых задач во время timed игр не было.

| Вариант | Победы/12 |
|---|---:|
| База | 9 |
| Segments0.5 | 4 |
| Segments1 | 0 |
| Geometry2000 | 8 |
| Geometry4000 | 5 |
| Risk0.5 | 6 |
| Risk1 | 3 |

Правило: максимум goals, при ничьей сначала baseline, затем actual wall/ход
и объявленный порядок. Выбран **baseline**. В round1 и round2 best9→9:
`stop_reason=local_plateau` соответствует объявленному критерию. Размер
выборки и ограниченные семейства не позволяют объявить глобальный потолок.
Почему признаки уступили, этим сравнением не изолировано: изменены оценки,
а для geometry/risk добавлена стоимость leaf arithmetic. Корректная формула
не гарантирует более сильную игру при ограниченном времени.

Все28 обязательных SHA заморожены коммитом `3bc8e09` до control:
источники, raw screen, actual manifest, семь source/native pairs. После
заморозки другой признак или strength по control не выбирался.

## Контроль: два прогона одной базы

По фиксированному протоколу control всегда содержит120 игр/60 новых seed
`1900000000..1900000059`, даже при выборе baseline. Поэтому обе метки
`baseline` и `finalist` имеют **source_arm=baseline и одну DLL**.
Это не сравнение новой стратегии с базой и не контроль шести отклонённых
альтернатив. Разницу исходов нельзя приписать признакам или удалению ML.

| Метка одной конфигурации | Победы | Loss | Limit | Search wall/ход |
|---|---:|---:|---:|---:|
| baseline | 34/60 | 26 | 0 | 4.134мс |
| finalist→baseline | 24/60 | 36 | 0 | 4.239мс |

Для второй метки gained11/lost21, формальный односторонний p=0.97494877.
`confirmed_improvement=false` также требует небазовый выбранный вариант:
никакого нового алгоритмического эффекта здесь нет. Actual wall ratio1.02550
в пределах±10%; это отдельное измерение, а не вывод из nominal10ms.
Глубины/positions и их scope сохранены в summary. Positions считает search
nodes, а не всю feature arithmetic; production risk не вызывает execute_move.

Timed поиск может завершать разные глубины/пороги при колебаниях выполнения;
фиксированный seed не гарантирует одинаковые ходы/финал при wall budget.
Конкретная причина разницы34/60 и24/60 отдельным A/B не установлена.
Результаты не усредняются как независимые разные стратегии и не сравниваются
с историческими26/60 либо31/60 ради заявления прироста.

Все204 партии завершены без limits. [screen.json](experiments/geometry/screen.json),
[finalist.json](experiments/geometry/finalist.json),
[control.json](experiments/geometry/control.json),
[summary.json](experiments/geometry/summary.json). Raw контроль сохранён в `17a7cbb`.
Координатор отдельно пересчитал outcomes из tuple boards, парные seed,
gained/lost, биномиальный хвост и SHA; сводка совпала.
[Независимый финальный обзор](verification/geometry/REVIEW-RESULT.md) подтвердил
все204 исхода, выбор/локальный stop-критерий, пары seed и28 frozen SHA.
Он отдельно подтвердил, что обе control-метки относятся к одной baseline.

## Решение и воспроизведение

Рабочая стратегия сохранена: ручная baseline-формула, CLI progressive,
API legacy совместим, без ML. Ни один новый признак не включён по умолчанию.
Нет измеренного нового win-rate для полных игр10с/ход или телефона; stage/rules
остаются допущениями модели мира. Телефон и runtime не переключались.

Сохранённый worktree:
`/Users/shalioner/orca/projects/Всякое разное/output/2048-geometry-20261102/worktree`.
Проверка завершённых этапов без новых игр:

```sh
rtk proxy python3 experiments/geometry/run.py build
rtk proxy python3 experiments/geometry/run.py screen --resume
rtk proxy python3 experiments/geometry/run.py control --resume
rtk proxy python3 experiments/geometry/run.py finalize
```

Не изменять producers/header/plan и не пересобирать экспериментальные DLL,
выдавая новую сборку за resume. Для нового цикла нужны новый протокол и новый
ещё не использованный контроль. Эти screen/control seed уже раскрыты.
