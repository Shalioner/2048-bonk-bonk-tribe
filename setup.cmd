@echo off
setlocal
pushd "%~dp0" || exit /b 1
call windows-path.cmd

if not exist "vendor\engine.dll" (
    echo Missing vendor\engine.dll. Use the Windows archive or run vendor\build-windows.cmd first.
    goto failed
)
adb version >nul 2>&1
if errorlevel 1 (
    echo ADB is unavailable. Extract Android Platform-Tools next to setup.cmd or add it to PATH.
    goto failed
)
tesseract --list-langs 2>nul | findstr /x "eng" >nul
if errorlevel 1 (
    echo Tesseract with English recognition data is required. See WINDOWS.md.
    goto failed
)
set "BONK_PYTHON_CHECK=import platform, sys; raise SystemExit(sys.version_info[:2] != (3, 13) or platform.machine() != 'AMD64')"
if exist ".venv\Scripts\python.exe" goto install_packages
set "BONK_PYTHON=py -3.13"
%BONK_PYTHON% -c "%BONK_PYTHON_CHECK%" >nul 2>&1
if not errorlevel 1 goto create_venv
set "BONK_PYTHON=python"
%BONK_PYTHON% -c "%BONK_PYTHON_CHECK%" >nul 2>&1
if errorlevel 1 (
    echo Install Python 3.13 for Windows x64 with py or python on PATH. See WINDOWS.md.
    goto failed
)

:create_venv
%BONK_PYTHON% -m venv .venv || goto failed

:install_packages
.venv\Scripts\python.exe -c "%BONK_PYTHON_CHECK%" || goto failed
.venv\Scripts\python.exe -m pip install --only-binary=:all: -r requirements.txt || goto failed
.venv\Scripts\python.exe -c "import bot" || goto failed
echo Setup complete. Connect Android, open the game board, then run start.cmd --dry-run.
popd
pause
exit /b 0

:failed
echo Setup failed. See WINDOWS.md and the error above.
popd
pause
exit /b 1
