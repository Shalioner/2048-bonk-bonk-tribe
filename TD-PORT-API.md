# Контракт адаптации TD

Исходная точка45fd360. Донор moporgic/TDL2048 a99f620, MIT, оригиналы и SHA
сохранены в vendor/tdl2048-original. Переносится логика n-tuple estimate/update
и последовательного TD(0), а не чужая цель merge-score или готовые веса.

## Модель

`td_model.TDModel(initial_value=0.5)`: `bias` float32(3), `weights`
float32(3,4,65536), `metadata`/`training` dict; `predict(board)`,
`update(board,target,alpha)`, `save(path,metadata=...)`, classmethod `load(path)`.
Паттерны/D4/нормировка совпадают с value_model.features. Nonterminal link —
clipped linear, raw=bias+sum(32 weights)/8, clipped в[1e-6,1-1e-6]. Goal1,
loss0; actual goal/loss не обучаются. TD-update: error=alpha*(target-predict),
каждое из32 активных вхождений получает error/4; bias неизменен.
Повторные индексы обновляются по числу вхождений, как у donor isomorphic update.
Формат отдельный BONKTD01 с правилами11/3 и target_type2; старый MC не менять.
Строгие metadata/SHA/CRC/finite и transactional load; незагруженный C++ evaluateNaN.

C++ `bonk_td::Model`: `loaded`, `load(path,error*)`, `clear`, `evaluate(packed)`.
Дополнительные методы обучения принадлежат исполнителю модели.

## Нативное обучение

`td_model.build_trainer(path)` — явная сборка vendor/td_train.cpp в указанный
путь, без implicit rebuild при import/resume. Trainer включает локальный
engine_adapted.cpp, использует его CAP/moves/spawn; upstream целиком не исполняется.

`td_model.NativeTrainer(library_path)`:

- `reset(initial_value=0.5)`; `set_model(model)`; `model()` возвращает TDModel;
- `set_spawn_table(table16x15)`;
- `episode(seed,algorithm='td0'|'mc'|'observe',behavior='teacher'|'selfplay',
  teacher_depth=1,alpha=0.1,epsilon=0.05,max_moves=6000,start_board=None)`.

Episode возвращает dict seed,result(goal/loss/limit),moves,caps,board,
first_caps (ключи'1'/'2', точный cap-count, board16 ПОСЛЕ spawn перед выбором
игрока, только legal/non-goal),updates,trajectory_digest. observe не меняет
параметры, MC обновляет все nonterminal afterstates после полного исхода,
TD0 обновляет предыдущее afterstate по следующему выбранному afterstate value,
последний target — goal1/loss0. На nonterminal немедленная награда0, discount1.
Для teacher фиксированный depth-limit1 ручной эвристики, никакой активной
learned-модели; selfplay использует greedy afterstate-value +epsilon exploration.
Tie-breaking детерминирован. После goal случайная плитка не появляется.

PRNG принадлежит эпизоду, детерминирован по uint64 seed; uniform empty cell,
распределение rank из заданной таблицы по максимуму после хода. Начало — две
плитки, либо явно переданное валидное незавершённое board. Невалидный вызов
отвергается до изменения параметров. start_board уже находится в фазе выбора
игрока, дополнительное начальное появление к нему не добавляется. Limit не
превращается в loss/MC-label; trainer откатывает все TD-обновления такого
эпизода, updates=0 (attempted_updates может сохраняться как диагностика).

## Владение

Исполнитель модели: td_model.py, vendor/td_model.h, vendor/td_train.cpp,
test_td_model.py, test_td_train.py, TD-MODEL-IMPLEMENTATION.md.
Исполнитель эксперимента: experiments/td/run.py, test_td_cycle.py,
TD-CYCLE-IMPLEMENTATION.md. plan.json и vendor/donor — координатор.
Координатор: engine_adapted.cpp/bot.py/integration tests/документация/данные.
