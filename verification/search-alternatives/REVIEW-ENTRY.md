# Независимая приёмка opt-in entrypoint

Проверен точный diff `b9207fe..8d2c31076a40d757805b9e7fe85068b06bd63891` (worker `9e030721`): только `play_search_alternative.py` и `test_search_entry.py`. Frozen producers, plan, модель среды и измеренные screen/control не меняются. Предыдущий REVIEW-RESULT.md не изменён.

**Подтверждённых блокирующих дефектов в этом diff не найдено.** Это явный opt-in для измеренного banked resource profile, а не переключение bot.py default или доказательство работы на телефоне.

## Gate и runtime routing

- `--help` и argparse/проверки measured-mode аргументов выполняются до `load_cycle()`. Запросы другого depth/think/policy, nan/inf think и неизвестные флаги завершаются до native/artifact/ADB пути. Steps/delay/serial/dry-run передаются прежнему bot CLI; их существующая семантика не меняется.
- `launch` под cycle lease вызывает `verify_freeze` и complete control checkpoint с `validate_only=True`. Через прежний runner проверяются точные source/native/freeze fingerprints, полный ordered control и limits. Неполный этап не продолжается, `play`/`finalize` не вызываются.
- Paired result вычисляется заново по validated control. Требуются strict true для его improvement и summary confirmation, banked finalist, точное совпадение paired и ссылки summary SHA на фактические finalist/control. Таким образом, stale/unconfirmed/negative/baseline/partial результат не допускается.
- Дополнительно сверяются kind, thresholds1e-4/1e-5/1e-6, полный ledger profile10ms allowance/early cap10ms/late cap40ms и search depth8/progressive/nominal0.01. Изменённый профиль не принимается как прежний измеренный режим. Summary CPU/cost поля не используются для изменения политики; её большая измеренная цена явно указана в help.
- После gate выбирается именно frozen `banked_endgame` native через `cycle.configure`, проверяются selected module, thresholds и progressive symbol; API2/CAP11/goal3 проверяет прежний `bot.load_engine`. Missing/incompatible artifacts завершаются до bot.main/ADB, не вызывая implicit rebuild/restore. Для нового checkout остаётся ранее проверенный отдельный restore workflow.

## Ledger и восстановление состояния

`BankedChooser` использует именно runner `allocate_budget`/`charge_budget`, а не копию формулы. Новый запуск получает credit0. Pre-call phase определяется count11; bot.main проверяет goal>=3 до choose. Вызов с иными depth/think/policy отклоняется до изменения credit.

Фактический choose wall списывается в `finally`, включая terminal None, исключение и KeyboardInterrupt. Долг не обнуляется; info дополнительно получает supplied budget, external wall и credit. Для последующих запусков wrapper не накапливается: outer finally возвращает исходные choose, NATIVE, THRESHOLDS и тот же sys.argv как при обычном завершении, так и при configure/runtime failure. Во время bot.main передаются явные10ms/depth8/progressive, глобальный DEFAULT_THINK10s не переписывается. Cycle lease освобождается до bot.main; существующий bot.lock и прежние наблюдение/жесты/обновление spawn сохраняются.

По исходникам проверены девять mocked регрессий: routing/restoration, early help/args, negative/partial/stale gate, profile drift, late40/debt/None, reset/no stacking, exception charge и native-load failure. Полный suite здесь повторно не запускался. Прочитан `entry-preflight.json`: reported actual read-only gate/profile/API2/11/3, неизменные data hashes, bot.main=false, choose=false, phone_calls0. Это контекст проверки координатора; самостоятельно native gate или live bot не запускались.

Ресурсный scope изложен корректно:63/100 против49/100 относится к прежнему offline control с wall/ход≈1.595× и CPU/ход≈1.562×; late supplied cap40ms не является прежним per-call10ms cap, наблюдённый max≈168ms не является гарантией. Equal-cost, uniform-bigger-budget и phone/default10s выводы этим entrypoint не добавляются. Реальный пользовательский сценарий на устройстве остаётся непроверенным в этой приёмке.

Продукт и данные reviewer не менял; записан только этот отчёт. Сборки, native, bot.main, choose, ADB, новые игры и дополнительные общие gates не запускались.
