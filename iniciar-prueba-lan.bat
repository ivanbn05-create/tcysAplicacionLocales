@echo off
setlocal
cd /d "%~dp0"

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0iniciar-prueba-lan.ps1"
set "CODIGO=%ERRORLEVEL%"

if not "%CODIGO%"=="0" (
    echo.
    echo No fue posible iniciar Los Tocayos POS. Revisa el mensaje anterior.
    pause
)

exit /b %CODIGO%
