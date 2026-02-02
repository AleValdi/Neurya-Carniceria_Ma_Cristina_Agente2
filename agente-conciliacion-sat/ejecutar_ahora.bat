@echo off
REM ============================================
REM Ejecutar Agente de Conciliación manualmente
REM ============================================

cd /d %~dp0
echo.
echo Ejecutando Agente de Conciliacion...
echo.
python main.py
echo.
echo Proceso terminado.
pause
