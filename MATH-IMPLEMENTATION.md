# Реализация математического цикла

Первый этап: активные bot/core восстановлены из pure `e28aad01f8c8e3aa725e41640625e5fda7a02701`.
Core побайтно совпадает (SHA256 `257050c392bf3a743f894b0f674ff8d7da5c8f2165e2dff003f0e3b4192cb24d`).
В bot изменён только default CLI `--search-policy progressive`; Python API сохраняет
`policy="legacy"`, глубина8 и бюджет10с сохранены. Старые budget-тесты вызывают
legacy явно. Cache/progressive алгоритмы и их gates не ослаблены.

ML backend, inference headers, trainer, donor copy, тесты и experiments/learning,
experiments/td удалены из новой ветки. Полные code/data/license архивы сохранены
без изменений в `6e421fe`/`local/strategy-td-port-20261102` и
`45fd360`/`local/strategy-learning-20261101`. README/SOURCE/RESEARCH-NOTES указывают
архивы и текущий MATHEMATICAL.md. Модель частот появлений и её обновления сохранены.

Проверка первого этапа:

```sh
sh vendor/build.sh
python3 -m unittest test_bot test_benchmark test_progressive_search test_search_cache test_pure_math -v
```

65 тестов прошли за6.484s. Четыре прежних pure-модуля содержат61 тест
(32+15+9+5), новый модуль добавляет4 gates отсутствия ML, CLI default,
legacy CLI и неизменных depth/think/spawn updates. Указанные ранее74 не являются
числом тестов этих исходных файлов; тесты не удалялись и не пропускались.
Телефон/runtime/main/старые worktree не менялись. Реальные партии не запускались.
