@echo off
REM ============================================
REM Instalador del Agente de Conciliación
REM ============================================

echo.
echo ============================================
echo  AGENTE DE CONCILIACION SAT-ERP
echo  Instalador para Windows
echo ============================================
echo.

REM Verificar permisos de administrador
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo ERROR: Este script requiere permisos de administrador.
    echo Haz clic derecho y selecciona "Ejecutar como administrador"
    pause
    exit /b 1
)

REM Obtener directorio actual
set SCRIPT_DIR=%~dp0
cd /d %SCRIPT_DIR%

echo Directorio de instalacion: %SCRIPT_DIR%
echo.

REM Verificar Python
python --version >nul 2>&1
if %errorLevel% neq 0 (
    echo ERROR: Python no esta instalado o no esta en el PATH
    echo Por favor instala Python 3.9+ desde https://python.org
    pause
    exit /b 1
)

echo [1/4] Verificando Python...
python --version
echo.

echo [2/4] Instalando dependencias...
pip install -r requirements.txt
if %errorLevel% neq 0 (
    echo ERROR: No se pudieron instalar las dependencias
    pause
    exit /b 1
)
echo.

echo [3/4] Creando archivo .env si no existe...
if not exist .env (
    copy .env.example .env
    echo Archivo .env creado. IMPORTANTE: Edita .env con tus datos de conexion
) else (
    echo Archivo .env ya existe
)
echo.

echo [4/4] Creando tarea programada...
echo.

set /p HORA="Ingresa la hora de ejecucion diaria (ej: 07:00): "
if "%HORA%"=="" set HORA=07:00

REM Crear tarea programada
schtasks /create /tn "AgenteConciliacionSAT" /tr "python \"%SCRIPT_DIR%scheduler.py\" --una-vez" /sc daily /st %HORA% /ru SYSTEM /f

if %errorLevel% equ 0 (
    echo.
    echo ============================================
    echo  INSTALACION COMPLETADA
    echo ============================================
    echo.
    echo El agente se ejecutara diariamente a las %HORA%
    echo.
    echo IMPORTANTE - Configura estos archivos:
    echo   1. Edita .env con los datos de conexion a SQL Server
    echo   2. Si tienes FIEL, agrega las rutas en .env
    echo.
    echo Para ejecutar manualmente:
    echo   python main.py
    echo.
    echo Para ver el estado de la tarea:
    echo   schtasks /query /tn "AgenteConciliacionSAT"
    echo.
    echo Para eliminar la tarea:
    echo   schtasks /delete /tn "AgenteConciliacionSAT" /f
    echo.
) else (
    echo ERROR: No se pudo crear la tarea programada
)

pause
