@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$u='https://raw.githubusercontent.com/nexora-kz/NEXORA-Hands/main/remote.ps1?cb='+[guid]::NewGuid().ToString('N'); irm $u | iex"
if errorlevel 1 (
  echo.
  echo NEXORA Hands failed to start. Press any key to close.
  pause >nul
)
