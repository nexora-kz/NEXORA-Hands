@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/nexora-kz/NEXORA-Hands/38dbef3dcec1f8edc33008b90851a1acb00ff62e/remote.ps1 | iex"
if errorlevel 1 (
  echo.
  echo NEXORA Hands failed to start. Press any key to close.
  pause >nul
)
