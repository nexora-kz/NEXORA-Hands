@echo off
setlocal
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/nexora-kz/NEXORA-Hands/main/remote.ps1 | iex"
if errorlevel 1 pause
