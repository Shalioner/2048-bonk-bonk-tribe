@echo off
setlocal
type nul >"%~dp0STOP"
exit /b %ERRORLEVEL%
