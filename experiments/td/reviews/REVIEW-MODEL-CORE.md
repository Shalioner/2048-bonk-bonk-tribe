# Независимое ревью TD model/trainer/core

**Подтверждённых блокирующих дефектов P0–P3 в проверенной области не найдено.**

Точный диапазон `45fd360ce3fbb080823ef6544cdd4d5d54f329f1..35eb431c7fbc95da32bfca99475e8faeb90d1866`, отдельный чистый review-worktree. Изучены pinned donor, `td_model.py`, `vendor/td_model.h`, `vendor/td_train.cpp`, core/Python seam и новые тесты. Runner ещё не входит в эту приёмку. Продукт не менялся; дополнительные полные игры/fit не запускались, production engine не пересобирался.

## Проверенные механизмы

- **Перенос донора фактический и прослеживаемый.** Суммирование/обновление D4-вхождений соответствует `TDL2048/2048.cpp:1497–1536`; `alpha*(target-predict)` — `state::instruct:1771–1775`; последовательный previous-afterstate → selected-next-afterstate → terminal target — `optimize:forward:2038–2057`. Новые Python/header/trainer содержат точный commit/source mapping, список адаптаций и полный MIT copyright/permission/warranty. Новый код реализует эту схему, а не только хранит архив. Изменения цели и нормировки явны: Bonk3×rank11, reward0/discount1, terminal1/0, четыре4-tuples, стадии, clipped-linear и error/4 на каждое из32 вхождений.
- **Модель/формат.** Python/C++ используют одинаковые клетки D4, float64-сумму с float32-округлением, фиксированный bias и повторные обновления alias-индексов. Терминалы обходят обучение. BONKTD01/target2 отделён от старого MC; проверяются размер/header/rules/target, CRC, finite, а в Python также canonical metadata, donor и payload SHA. Native load заменяет параметры только после полной проверки. Старый MC serializer не изменён.
- **TD transition phase корректна.** `td_train.cpp:189–205` выбирает next afterstate до обновления previous и берёт его ещё не обновлённую оценку. После этого previous потребляется один раз. Goal после собственного хода завершает эпизод без spawn; goal после custom spawn и loss без legal moves дают точные1/0 последнему previous. Goal-afterstate не обучается и terminal target не применяется дважды.
- **MC learner control сохраняет траекторию.** MC запоминает те же nonterminal afterstates и обновляет их после полного goal/loss. Teacher использует локальный `solve_levels(...,1)` при очищенных inference-моделях; не читает обучаемый handle. Поэтому изменение MC/TD-весов не меняет teacher behavior. Digest включает initial board, направления/afterstates, spawned boards и outcome, без model/algorithm. Синтетические тесты подтвердили одинаковый digest при MC/TD0/observe и разных исходных весах.
- **Replay и handles.** SplitMix64 принадлежит отдельному эпизоду; RNG не наследуется от прошлых запусков. Initial full start имеет ровно два spawn, curriculum start уже является player state и не получает лишний initial spawn. Параметры и spawn table принадлежат handle; каждый episode восстанавливает его таблицу в общих engine globals под mutex. get/set/save/load сохраняют float32-биты; два handles после checkpoint дают одинаковый результат и веса. В trainer библиотеке inference-модели очищаются перед поиском; отдельная тренировочная модель не очищается.
- **Invalid/limit rollback.** Python и C проверяют аргументы до изменения модели; set_parameters/set_spawn_table валидируют целиком перед заменой. Резерв модели создаётся перед первым update. Limit и исключение откатывают все изменения; limit возвращает updates0, attempted_updates остаётся диагностикой. MC при limit не получает loss-label. Observe не обновляет параметры. Проверки покрыли invalid native bypass, malformed board/model/table, terminal start и limit после попыток TD-update.
- **Интеграция.** Обе ранние goal-точки используют шкалу1 при MC/TD, manual heuristic сохраняет прежнюю шкалу. Успешная загрузка одного формата очищает другой; ошибочная не очищает активный. Общий clear возвращает heuristic. Новые exports optional для API2; старый MC/default legacy/model-off сохраняются. Прежние scheduler/cache/moves/spawn в данном diff не менялись.

## Уточнение epsilon до данных

В проверенном native-коде epsilon применяется только при `behavior=selfplay` (`td_train.cpp:178–186`). Teacher детерминирован и не расходует RNG на exploration. Текущий API описывает именно это, тогда как общий `plan.model.epsilon=.05` можно было прочитать шире.

Координатор принял явное решение до pool/train: фиксировать `plan.training.teacher_epsilon=0.0`, записывать/передавать0 для teacher, а0.05 применять только к selfplay после warmup. Core не меняется. Это уточняет фактическую политику, не меняет matched MC/TD teacher trajectories и не требует повторения успешных core gates. Реализацию этой записи в будущем runner следует сверить в его уже запланированном отдельном ревью; текущий отчёт не выдаёт ещё не интегрированный runner за проверенный.

## Независимая проверка

```sh
rtk proxy env PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v test_td_model.py test_td_train.py test_td_integration.py
rtk proxy git diff --check 45fd360..35eb431
```

Все**15** существующих synthetic/model/integration тестов прошли за**7.726с**. Два тестовых native адаптера собирались только во временных папках; скопированная production engine.dylib не пересобиралась. `diff --check` успешен, tracked review-worktree чистый.

Общий121 suite и16 observe-партий technical-probe, сообщённые координатором, здесь не повторялись и не подменяют независимые результаты выше. Вывод относится к корректности model/trainer/core и не утверждает роста побед, скорости больших обучений или готовности ещё не проверенного experiment runner. Нового глобального поиска методов не проводилось.
