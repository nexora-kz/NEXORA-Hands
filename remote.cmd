@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/nexora-kz/NEXORA-Hands/main/remote.ps1 | iex"
if errorlevel 1 (
  echo.
  echo NEXORA Hands failed to start. Press any key to close.
  pause >nul
)
