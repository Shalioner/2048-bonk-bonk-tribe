# Закрытие read-side freeze P2

**P2 из REVIEW-CYCLE.md закрыт. Новых явных регрессий в проверенном исправлении не найдено.**

Проверен точный diff `18205dff6c0a0c08813d340efa26c72c6efe59aa..88063e146551403be0e9b304664617746fae361f` в чистом отдельном review-worktree. Область ограничена run.py/test/doc изменением чтения freeze; аудит не расширялся. Продукт не изменялся.

- `verify_freeze` до control games и finalize требует dict, точную integer version1, TD-only finalist, настоящий boolean promotion и точный holdout policy плана. Исторический MC больше нельзя принять как TD-finalist.
- Прежние проверки всех frozen file SHA сохранены. После них читается исходный screen через `evaluate(..., resume=True, validate_only=True)`; неполный screen не продолжается, новый timed_play не вызывается.
- Общая `screen_decision` используется writer и verifier. Она заново вычисляет summary, TD selection с теми же tie/cost правилами и promotion с heuristic/matchedMC/oldMC gate. Finalist, promotion в обе стороны, screen table и model paths должны точно совпасть с проверенным screen.
- Положительный полный mock-цикл freeze→holdout→finalize проходит. Вызов screen из verifier не рекурсивен: screen-ветка evaluate не вызывает verify_freeze. Источники/model/binary SHA и критерии двух exact paired p<=0.025 не ослаблены.

Независимо выполнены только пять тестов, затрагивающих изменённый контракт:

```sh
rtk proxy env PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v \
  test_td_cycle.CycleTests.test_freeze_read_side_rejects_semantic_tampering \
  test_td_cycle.CycleTests.test_bad_finalist_rejected_by_holdout_and_finalizer \
  test_td_cycle.CycleTests.test_freeze_read_requires_complete_screen_without_continuation \
  test_td_cycle.CycleTests.test_historical_mc_participates_in_screen_gate \
  test_td_cycle.CycleTests.test_complete_serial_cycle_freeze_hashes_and_exact_tests
```

**5/5 OK за4.691с.** Они подтвердили отказ от семи подмен без изменения native counters, отказ holdout/finalize от old_mc до создания outputs, отсутствие скрытого завершения screen, отказ ложному promoted=True при oldMC winner и успешный штатный цикл со статистикой. Все episodes/модели в этих проверках — явные mock; реальных fit/партий/native-сборок не было.

`git diff --check 18205df..88063e1` успешен; tracked review-worktree чистый. Полные21/121 suite и CLI build здесь не повторялись. Предыдущая оговорка о read-side freeze не остаётся блокером начала данных на проверенном исходнике; дальнейшее решение о запуске принадлежит координатору.
