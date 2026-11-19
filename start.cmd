@echo off
setlocal
pushd "%~dp0" || exit /b 1
call windows-path.cmd
set PYTHONUTF8=1
set "RESULT=1"
if not exist ".venv\Scripts\python.exe" (
    echo Run setup.cmd first.
    goto done
)
.venv\Scripts\python.exe -u bot.py --clear-stop %*
set "RESULT=%ERRORLEVEL%"

:done
popd
if "%~1"=="" pause
exit /b %RESULT%
