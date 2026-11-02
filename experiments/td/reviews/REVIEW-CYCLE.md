# Независимое ревью TD runner до данных

Область: точный runner diff `35eb431..c7f1cb9df3f18c678fee43b0dd5f6643776fb2ae` в отдельном review-worktree; reserved-output исправление `c7f1cb9..18205dff6c0a0c08813d340efa26c72c6efe59aa` прочитано отдельно и проверено на main. Код/данные не менялись, native rebuild/реальных fit/полных партий/делегирования не было.

**Один подтверждённый P2 в проверке входного freeze. Дефектов, которые сами по себе портили бы штатные pool/train данные, не найдено.** Исправить P2 до данных практично, поскольку run.py входит в producer fingerprint и позднее изменение намеренно сломает resume старого producer.

## P2: read-side freeze допускает non-TD finalist и доверяет непроверенному promotion

**Места в c7f1cb9:** `experiments/td/run.py:686–703,756–779,800–807`.

Штатный `freeze()` выбирает только `td_teacher`/`td_selfplay` и правильно вычисляет promotion. Однако `verify_freeze()` проверяет только файлы из hashes. При чтении manifest ни holdout, ни finalize не требуют TD-only finalist и не сверяют finalist/promoted с уже замороженным screen. Сам finalist.json не является одним из собственных hashes; его SHA попадает в header уже после чтения.

Если готовый manifest повреждён/изменён до первого holdout, достаточно поменять только `finalist` на `old_mc`: все source/model/data hashes остаются прежними. Holdout затем принимает исторический MC как кандидата, а finalize способен объявить `joint_confirmed=True` для него. Это не наблюдённая утечка и не ошибка штатного writer; finding относится к неподдержанной семантике принятого входного manifest.

**Синтетическое воспроизведение:** обычные mocked pool/train/screen/freeze существующего fixture; затем изменено только поле finalist в его JSON, simulated old_mc выиграл все6 пар. Без реальных игр/обучения получено:

```text
accepted_holdout_arms = ['heuristic', 'mc_teacher', 'old_mc']
summary_finalist = 'old_mc'
joint_confirmed = True
p_values = [0.015625, 0.015625]
```

**Минимальная мера:** на read-side до каких-либо control games и в finalize проверять допустимый TD-only finalist, policy и соответствие выбора/promotion замороженным screen-данным. Не переписывать freeze для проверки. Добавить mock-регрессии non-TD finalist и изменённого promotion с отказом до timed_play/summary. Нормальные сохранённые модели/данные для этого менять не требуется.

## Остальная проверенная логика

- Pool использует observe/model-off teacher, точные seed-prefix и после-spawn legal/non-goal snapshots. Отбор unique первых cap1/cap2 не смотрит на исход источника. Загрузка неполного pool не продолжает сбор скрыто.
- Matched MC/TD-teacher получают одинаковые seed, curriculum starts, epsilon0 и число эпизодов; digest/board/moves/result/first_caps должны совпасть. На limit или mismatch обе модели откатываются, invalid записывается отдельно и не принимается за обучение. Selfplay имеет teacher warmup с0, затем learned behavior с0.05.
- Validation — observe/epsilon0, отдельные full/cap1/cap2 seed; параметрический hash контролирует отсутствие обновлений. Prefix содержит stage/start, model/pool/source SHA; неполные validation продолжаются только внутри разрешённого обучения. Готовые checks читаются без новых игр. Совместная metric — покомпонентная сумма tuple двух matched моделей; самый ранний лучший checkpoint сохраняется. Начальный count0 явен и не считается одним из трёх trained checks.
- Модели current сохраняются в чередующиеся слоты до публикации state; source, binary, pool, реальный model/parameter SHA, metadata run/arm/count и SHA-цепь records проверяются. Полностью записанный episode-tail детерминированно проигрывается из опубликованной модели и сверяется без seconds. Pending milestone validation заканчивается до следующего обучающего эпизода. Неполный этап не запускается из freeze/finalize.
- Stop реализует минимум3 trained checks/2 stall, иначе cap обозначается budget_limited. Обе matched модели выбираются на одном count; selfplay отдельно. Source/donor/MIT/API/реальные trainer и evaluator binary входят в provenance, implicit rebuild отсутствует.
- Screen вращает пять arm, holdout вращает три; партии последовательны. Writer выбирает TD-only, учитывает heuristic, matched MC и old MC в promotion. По валидному manifest finalize использует два парных точных one-sided p<=0.025 и строго больше побед, отдельно считает actual choose-wall/move и±10% cost-флаг.
- Реальный timed-result проходит существующий `learning/cycle.validate_games`: result/goal согласуются с конечными boards, loss не имеет legal moves. Timed loop останавливается до spawn после goal. Поэтому paired может использовать уже проверенные result, а limit никогда не становится loss.

## Уже известный reserved-output крайний случай

Не открыт повторно как новая находка. Исправление18205df вводит `checked_output()` первой операцией pool/train/evaluate/freeze: resolved output не может совпасть с глобальным marker. Проверка выполняется до source/trainer/model/timed side effects. Тест прошёл для всех пяти стадий при отсутствующем и существующем marker, включая нормализованный path alias; существующий marker и защищённые файлы сохранились побайтно.

## Независимые проверки

На c7f1cb9 отдельно выполнены пять существующих mock-тестов по рискам replay, pending validation, полного freeze/finalize с exact p, no-goal-spawn и model tampering: **5/5 OK,1.010с**. На исправленном main выполнен `test_reserved_marker_output_rejected_before_stage_side_effects`: **OK,0.447с**. Дополнительно воспроизведён единственный finding выше на mock fixture. `git diff --check 35eb431..c7f1cb9` успешен, tracked review-worktree чистый.

Полный17/18 suite, повторные core121 и actual CLI build не запускались мной; их результаты координатора использованы только как контекст. Новых настоящих данных нет. Финальное решение о начале pool/train и исправлении P2 остаётся за координатором.
