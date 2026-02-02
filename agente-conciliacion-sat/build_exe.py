# -*- coding: utf-8 -*-
"""
Script para generar el ejecutable del Agente de Conciliación SAT-ERP
"""

import PyInstaller.__main__
import os
import shutil

# Directorio base
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

def build():
    print("=" * 60)
    print("GENERANDO EJECUTABLE - Agente Conciliación SAT-ERP")
    print("=" * 60)

    # Usar output2 para evitar conflictos con carpeta dist existente
    output_dir = os.path.join(BASE_DIR, 'output2')

    # Argumentos para PyInstaller
    args = [
        'main.py',                          # Script principal
        '--name=AgenteConciliacionSAT',     # Nombre del ejecutable
        '--onedir',                          # Crear directorio con dependencias
        '--console',                         # Mostrar consola (para ver logs)
        '--noconfirm',                       # No preguntar confirmación
        '--clean',                           # Limpiar cache
        f'--distpath={output_dir}',          # Directorio de salida

        # Incluir módulos necesarios
        '--hidden-import=pyodbc',
        '--hidden-import=lxml',
        '--hidden-import=lxml.etree',
        '--hidden-import=openpyxl',
        '--hidden-import=pandas',
        '--hidden-import=fuzzywuzzy',
        '--hidden-import=python-Levenshtein',
        '--hidden-import=loguru',
        '--hidden-import=dotenv',
        '--hidden-import=requests',
        '--hidden-import=cfdiclient',

        # PDF Support
        '--hidden-import=fitz',          # PyMuPDF
        '--hidden-import=pymupdf',

        # Google Drive Support
        '--hidden-import=google.auth',
        '--hidden-import=google.auth.transport.requests',
        '--hidden-import=google.oauth2.credentials',
        '--hidden-import=google_auth_oauthlib.flow',
        '--hidden-import=googleapiclient.discovery',
        '--hidden-import=googleapiclient.http',

        # Incluir carpetas de datos
        '--add-data=config;config',
        '--add-data=src;src',

        # Icono (opcional)
        # '--icon=icon.ico',
    ]

    print("\nCompilando...")
    PyInstaller.__main__.run(args)

    # Crear estructura de carpetas necesarias en output2
    dist_dir = os.path.join(output_dir, 'AgenteConciliacionSAT')

    # Crear carpetas de datos
    for folder in ['data/xml_facturas', 'data/reportes', 'data/alertas', 'logs', 'fiel']:
        folder_path = os.path.join(dist_dir, folder)
        os.makedirs(folder_path, exist_ok=True)
        print(f"  Creada carpeta: {folder}")

    # Copiar archivo .env.example
    env_example = os.path.join(BASE_DIR, '.env.example')
    if os.path.exists(env_example):
        shutil.copy(env_example, os.path.join(dist_dir, '.env.example'))
        shutil.copy(env_example, os.path.join(dist_dir, '.env'))
        print("  Copiado: .env.example -> .env")

    # Copiar README
    readme = os.path.join(BASE_DIR, 'README.md')
    if os.path.exists(readme):
        shutil.copy(readme, os.path.join(dist_dir, 'README.md'))
        print("  Copiado: README.md")

    # Crear batch files para ejecución fácil
    crear_batch_files(dist_dir)

    print("\n" + "=" * 60)
    print("EJECUTABLE GENERADO EXITOSAMENTE")
    print("=" * 60)
    print(f"\nUbicación: {dist_dir}")
    print("\nArchivos generados:")
    print("  - AgenteConciliacionSAT.exe (ejecutable principal)")
    print("  - .env (configuración - EDITAR antes de usar)")
    print("  - ejecutar.bat (doble clic para ejecutar)")
    print("  - probar_conexion.bat (probar conexión a BD)")
    print("  - explorar_bd.bat (explorar estructura de BD)")
    print("  - configurar_drive.bat (configurar Google Drive)")
    print("  - sincronizar_drive.bat (descargar archivos de Drive)")
    print("\nPASOS PARA INSTALAR:")
    print("  1. Copiar toda la carpeta 'AgenteConciliacionSAT' al servidor")
    print("  2. Editar el archivo .env con los datos de conexión")
    print("  3. Ejecutar 'probar_conexion.bat' para verificar")
    print("  4. (Opcional) Ejecutar 'configurar_drive.bat' para habilitar Google Drive")
    print("  5. Ejecutar 'ejecutar.bat' para iniciar el agente")
    print("=" * 60)


def crear_batch_files(dist_dir):
    """Crea archivos batch para facilitar la ejecución"""

    # Ejecutar agente
    ejecutar_bat = """@echo off
chcp 65001 > nul
echo ============================================================
echo    AGENTE DE CONCILIACION SAT-ERP
echo ============================================================
echo.
AgenteConciliacionSAT.exe
pause
"""
    with open(os.path.join(dist_dir, 'ejecutar.bat'), 'w', encoding='utf-8') as f:
        f.write(ejecutar_bat)
    print("  Creado: ejecutar.bat")

    # Probar conexión
    probar_bat = """@echo off
chcp 65001 > nul
echo ============================================================
echo    PROBANDO CONEXION A BASE DE DATOS
echo ============================================================
echo.
AgenteConciliacionSAT.exe --test-conexion
pause
"""
    with open(os.path.join(dist_dir, 'probar_conexion.bat'), 'w', encoding='utf-8') as f:
        f.write(probar_bat)
    print("  Creado: probar_conexion.bat")

    # Explorar BD
    explorar_bat = """@echo off
chcp 65001 > nul
echo ============================================================
echo    EXPLORANDO ESTRUCTURA DE BASE DE DATOS
echo ============================================================
echo.
AgenteConciliacionSAT.exe --explorar
pause
"""
    with open(os.path.join(dist_dir, 'explorar_bd.bat'), 'w', encoding='utf-8') as f:
        f.write(explorar_bat)
    print("  Creado: explorar_bd.bat")

    # Procesar XMLs
    procesar_bat = """@echo off
chcp 65001 > nul
echo ============================================================
echo    PROCESANDO XMLS
echo ============================================================
echo.
echo Coloque los archivos XML en la carpeta data/xml_facturas
echo.
AgenteConciliacionSAT.exe --verbose
pause
"""
    with open(os.path.join(dist_dir, 'procesar_xmls.bat'), 'w', encoding='utf-8') as f:
        f.write(procesar_bat)
    print("  Creado: procesar_xmls.bat")

    # Configurar Google Drive
    config_drive_bat = """@echo off
chcp 65001 > nul
echo ============================================================
echo    CONFIGURACION DE GOOGLE DRIVE
echo ============================================================
echo.
echo Este asistente le permitira conectar el agente con una
echo carpeta de Google Drive para sincronizar automaticamente
echo los archivos XML y PDF de facturas.
echo.
echo REQUISITOS:
echo   - Archivo credentials.json en la carpeta config/
echo   - Cuenta de Google con acceso a la carpeta de facturas
echo.
echo Se abrira el navegador para autorizar el acceso.
echo.
pause
AgenteConciliacionSAT.exe --config-drive
pause
"""
    with open(os.path.join(dist_dir, 'configurar_drive.bat'), 'w', encoding='utf-8') as f:
        f.write(config_drive_bat)
    print("  Creado: configurar_drive.bat")

    # Sincronizar desde Google Drive
    sync_drive_bat = """@echo off
chcp 65001 > nul
echo ============================================================
echo    SINCRONIZAR DESDE GOOGLE DRIVE
echo ============================================================
echo.
echo Descargando archivos nuevos desde Google Drive...
echo.
AgenteConciliacionSAT.exe --sync-drive
pause
"""
    with open(os.path.join(dist_dir, 'sincronizar_drive.bat'), 'w', encoding='utf-8') as f:
        f.write(sync_drive_bat)
    print("  Creado: sincronizar_drive.bat")

    # Sincronizar y procesar
    sync_procesar_bat = """@echo off
chcp 65001 > nul
echo ============================================================
echo    SINCRONIZAR Y PROCESAR (FLUJO COMPLETO)
echo ============================================================
echo.
echo 1. Descargando archivos de Google Drive...
echo 2. Procesando facturas XML...
echo 3. Conciliando con remisiones en ERP...
echo 4. Generando reportes...
echo.
AgenteConciliacionSAT.exe --sync-drive --verbose
pause
"""
    with open(os.path.join(dist_dir, 'sincronizar_y_procesar.bat'), 'w', encoding='utf-8') as f:
        f.write(sync_procesar_bat)
    print("  Creado: sincronizar_y_procesar.bat")


if __name__ == '__main__':
    build()
