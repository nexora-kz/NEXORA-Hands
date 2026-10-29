@echo off
setlocal
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
set "BOOTSTRAP=%TEMP%\NEXORA-Hands-remote.ps1"
curl.exe -fL --retry 2 --connect-timeout 15 -H "Cache-Control: no-cache" "https://raw.githubusercontent.com/nexora-kz/NEXORA-Hands/096be8fb7f63e8736471cc29c15575cf0470f36e/remote.ps1" -o "%BOOTSTRAP%"
if errorlevel 1 goto :fail
powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File "%BOOTSTRAP%"
set "RC=%ERRORLEVEL%"
del /q "%BOOTSTRAP%" >nul 2>&1
if not "%RC%"=="0" pause
exit /b %RC%
:fail
echo NEXORA Hands - download failed.
pause
exit /b 1
