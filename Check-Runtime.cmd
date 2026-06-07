@echo off
powershell -ExecutionPolicy Bypass -File "%~dp0scripts\check-runtime.ps1" %*
