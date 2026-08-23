@echo off
title Instalador de Los Tocayos POS
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Instalar-LosTocayosPOS.ps1"
set RESULTADO=%ERRORLEVEL%
echo.
if not "%RESULTADO%"=="0" echo La instalacion no se completo. Codigo: %RESULTADO%
pause
exit /b %RESULTADO%
