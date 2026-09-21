@echo off
setlocal
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
set "BOOTSTRAP=%TEMP%\NEXORA-Hands-remote.ps1"
curl.exe -fL --retry 2 --connect-timeout 15 "https://raw.githubusercontent.com/nexora-kz/NEXORA-Hands/main/remote.ps1" -o "%BOOTSTRAP%"
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
