# Независимое ревью закрытия --out P2

**Итог: прежний P2 закрыт; новых подтверждённых дефектов в ограниченной области не найдено.**

Проверен общий точный диапазон `94c2100..5d9f62d9d4dd73d3c8416a7a56cc1882a9fd6e24`: сначала detached review-worktree на `fc695b4`, затем отдельное дополнение `fc695b4..5d9f62d` в основном worktree. Исходники, данные и предыдущие отчёты не менялись; область записи — только этот файл. Аудит не расширялся за пределы guard, resume и сохранения fingerprint.

## Проверенные контракты

- `cycle.evaluate` сохраняет общий `ROOT/evaluation-started.json` атомарным checkpoint до входа в цикл партий, независимо от пути `--out`. Проверка конфигурации/существующего checkpoint происходит раньше; если marker сохранить не удалось, партия не начинается. KeyboardInterrupt в первом `play` оставляет marker и закрывает обучение даже при внешнем результате без стандартного screen.json.
- `cycle.evaluation_started()` и `fit_round2._evaluated()` учитывают marker вместе с прежними screen/holdout/finalist. Содержимое marker не используется как разрешение: достаточно существования файла, включая пустой/нечитаемый. Прежний обход завершённым screen во внешнем output больше не разрешает новый original fit или refinement.
- Original fit проверяет существующие config/dataset/source/model SHA и возвращает готовый checkpoint только через совместимый `--resume`. Если готовой пары нет, marker приводит к отказу до создания модели и вызова fit, в том числе при `resume=True`. Неполная активная пара по-прежнему отвергается.
- Round2 без сохранённого selection отказывает после marker до запуска вариантов. При сохранённом selection отсутствующий binary не разрешает refit. Полностью готовый совместимый selection с desired active-парой можно проверить через resume без изменения весов и без новых fit. Полуопубликованную active-пару после marker допубликовать нельзя: `_publish` требует уже точное desired_active.
- Существующая recovery-ветка отсутствующих variant metrics остаётся: JSON может быть восстановлен из проверенного сохранённого binary без обучения, после чего сверяется сохранённое решение. Это восстановление metadata, не разрешение нового fit или изменения активных весов; для полного готового набора resume является validate-only.
- Дополнение `5d9f62d` запрещает `out.resolve()==marker.resolve()` до checkpoint, marker и партий. Новый тест подтверждает отказ при output, совпавшем с зарезервированным путём, без создания файла или вызова play.

## SHA и измеренный снимок

Условия fingerprint не удалены и не ослаблены: сохраняются точные config/dataset/source/model SHA для fit, заголовки/metadata вариантов, selection, known active hashes и frozen files holdout. Новый marker добавлен как запрет, а не как исключение из проверки происхождения.

В общем diff нет изменений native core, весов, планов обучения или старых JSON результатов. Исторические source hashes не переписаны под новый cycle.py. Документация явно отделяет измеренный commit94c2100 от исправленного runner: прежний контроль относится к сохранённому измеренному снимку. Сообщённые координатором повторные команды в measured-worktree здесь не запускались; их успешность не выдаётся за независимо повторённую проверку этого ревью.

## Независимые проверки

```sh
rtk proxy env TMPDIR="$REVIEW_TMP" python3 -m unittest -v test_learning_cycle.py test_value_refinement.py test_learning_study.py
rtk proxy env TMPDIR="$REVIEW_TMP" python3 -m unittest -v test_learning_cycle.CycleTests.test_evaluation_output_cannot_replace_its_own_marker
rtk proxy git diff --check 94c2100..5d9f62d
```

На fc695b4 все31 связанных mock-теста прошли за2.631с, включая пять новых guard-регрессий, прежние interruption/resume, provenance, publication и freeze gates. После минимального дополнения отдельно выполнена новая reserved-output регрессия: прошла за0.004с. Точный committed diff дополнения сопоставлен с проверенными файлами; повторять успешный общий suite не потребовалось. `diff --check` успешен, tracked деревья main и review чистые.

`REVIEW_TMP` — существующая `.review-temp` внутри review-worktree. Все партии и fit в этих тестах заменены явными mock; настоящие игры, обучение, native rebuild и native эксперименты не выполнялись.

Guard рассчитан на заявленный последовательный экспериментальный процесс. Он не является sandbox против ручного удаления служебных файлов или прямого вызова ValueModel.fit, что соответствует документированной границе.
