@echo off
setlocal
powershell -ExecutionPolicy Bypass -File "%~dp0scripts\smoke-memory.ps1" %*
exit /b %ERRORLEVEL%
