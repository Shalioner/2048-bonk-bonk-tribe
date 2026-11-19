@echo off
setlocal
pushd "%~dp0" || exit /b 1
x86_64-w64-mingw32-clang++ -O3 -std=c++11 -DBUILDING_DLL -static -shared engine_adapted.cpp engine.def -o engine.dll
set "RESULT=%ERRORLEVEL%"
popd
exit /b %RESULT%
