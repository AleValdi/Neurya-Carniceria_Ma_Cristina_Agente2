@echo off
REM ============================================
REM Ejecutar Agente de Conciliacion manualmente
REM ============================================
REM Detecta automaticamente la estructura del venv
REM (MSYS2 usa bin/, Python estandar usa Scripts/)

set AGENTE_DIR=C:\Tools\Agente2\agente-conciliacion-satCorrecto
cd /d %AGENTE_DIR%

REM Detectar python del venv
if exist "venv\Scripts\python.exe" (
    set PYTHON=venv\Scripts\python.exe
) else if exist "venv\bin\python.exe" (
    set PYTHON=venv\bin\python.exe
) else (
    echo ERROR: No se encontro el venv. Ejecuta primero:
    echo   python -m venv venv
    echo   venv\Scripts\pip install -r requirements.txt
    pause
    exit /b 1
)

echo.
echo ============================================
echo  AGENTE DE CONCILIACION SAT-ERP
echo  %date% %time%
echo ============================================
echo  Python: %PYTHON%
echo.

REM scheduler.py --una-vez = descarga SAT + concilia + reporte
%PYTHON% scheduler.py --una-vez

echo.
echo Proceso terminado. Revisa data\reportes\ para resultados.
pause
