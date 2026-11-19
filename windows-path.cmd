@echo off
rem Called by setup.cmd and start.cmd. Keep these paths local to their sessions.
set "PATH=%~dp0platform-tools;%LOCALAPPDATA%\Android\Sdk\platform-tools;%ProgramFiles%\Tesseract-OCR;%LOCALAPPDATA%\Programs\Tesseract-OCR;%PATH%"
