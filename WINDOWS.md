# Bonk Bonk Tribe 2048 на Windows

## Играть на компьютере

Распакуйте архив и откройте `simulator/index.html` в Edge или Chrome. Двигайте плитки стрелками. Кнопки «Подсказка» и «Автоигра» запускают расчёт хода в браузере. Для игры достаточно этого HTML-файла. При отсутствии интернета браузер использует системные шрифты.

## Запустить бота на телефоне

Понадобятся Windows 10/11 x64, Android с открытым режимом «Звёздное провидение» и USB-кабель с передачей данных. В готовом архиве уже есть `vendor/engine.dll`.

1. Установите [Python 3.13 для Windows x64](https://www.python.org/downloads/windows/) с командой `py` или `python` в `PATH`. Если установили Python Install Manager, запустите `py install 3.13`.
2. Скачайте [Android SDK Platform-Tools для Windows](https://developer.android.com/tools/releases/platform-tools) и распакуйте папку `platform-tools` рядом с `setup.cmd`. Подойдёт и уже установленный `adb` из Android Studio или `PATH`.
3. Установите [Tesseract 5 для Windows](https://tesseract-ocr.github.io/tessdoc/Installation.html#windows) с английскими данными `eng`. Оставьте стандартный путь `C:\Program Files\Tesseract-OCR` или добавьте папку с `tesseract.exe` в `PATH`.
4. Откройте `setup.cmd`. Он проверит зависимости, установит Python-пакеты в `.venv` и загрузит движок для проверки. Для установки пакетов нужен интернет.
5. Включите отладку по USB на телефоне, подключите его и подтвердите запрос доверия на разблокированном экране. Откройте полностью видимое поле 4×4 в портретном режиме. Распознавание рассчитано на интерфейс, откалиброванный по экрану 1440×3040. На другом устройстве сначала проверьте поле.
6. Откройте терминал в папке проекта и выполните `.\start.cmd --dry-run`. Бот выведет 16 уровней слева направо по строкам, предложит направление и сохранит `latest.png`. Сверьте поле. Ноль означает пустую клетку.
7. Если поле совпало, откройте `start.cmd`. Для остановки откройте `stop.cmd` или нажмите Ctrl+C в окне бота. Команда `.\start.cmd --steps 1` ограничивает первый запуск одним ходом. Пока бот работает, не делайте ходы вручную.

Список Android-устройств можно получить командой `.\platform-tools\adb.exe devices`, если распаковали Platform-Tools рядом с `setup.cmd`. При `unauthorized` подтвердите отладку на телефоне. Если список пуст, проверьте кабель, режим USB и [драйвер производителя телефона](https://developer.android.com/studio/run/oem-usb). При нескольких устройствах выполните `.\start.cmd --serial СЕРИЙНЫЙ_НОМЕР --dry-run` и указывайте тот же `--serial` при игре.

Если импорт `cv2` завершается сообщением о недостающей DLL, проверьте наличие [Microsoft Visual C++ Redistributable x64](https://learn.microsoft.com/cpp/windows/latest-supported-vc-redist). Если бот останавливается на распознавании, перед повторным запуском проверьте `recognition-error.png`, расположение поля и открытые поверх него окна.

## Пересобрать движок из исходников

На Windows скачайте [LLVM MinGW UCRT для хоста x86_64](https://github.com/mstorsjo/llvm-mingw/releases/tag/20261117), распакуйте его и добавьте его `bin` в `PATH` текущего терминала. Запустите `.\vendor\build-windows.cmd`, затем `setup.cmd`. Скрипт собирает `vendor/engine.dll` для Windows x64. В исходниках GitHub DLL нужно собрать перед установкой.
