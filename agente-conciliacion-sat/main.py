"""
Agente de Conciliación SAT-ERP (SAV7)
Punto de entrada principal para la conciliación de facturas con remisiones

Uso:
    python main.py                      # Procesar XMLs y consolidar matches 100%
    python main.py --dry-run            # Simular sin escribir en BD (para pruebas)
    python main.py --explorar           # Explorar estructura de BD SAV7
    python main.py --test-conexion      # Probar conexión a BD
    python main.py --archivo ruta.xml   # Procesar un archivo específico
    python main.py --sync-drive         # Sincronizar desde Google Drive antes de procesar
    python main.py --config-drive       # Configurar conexión a Google Drive
"""
import sys
import argparse
from pathlib import Path
from datetime import datetime
from loguru import logger

# Configurar path para imports
sys.path.insert(0, str(Path(__file__).parent))

from config.settings import settings
from config.database import DatabaseConnection
from src.sat.xml_parser import CFDIParser
from src.sat.models import Factura
from src.erp.sav7_connector import SAV7Connector, SAV7Explorer
from src.erp.remisiones import RemisionesRepository
from src.erp.consolidacion import ConsolidadorSAV7, ResultadoConsolidacion
from src.erp.models import ResultadoConciliacion
from src.conciliacion.matcher import ConciliacionMatcher
from src.conciliacion.validator import ConciliacionValidator
from src.conciliacion.alerts import AlertManager
from src.reports.excel_generator import ExcelReportGenerator

# Google Drive (opcional)
try:
    from src.drive import (
        DriveSync, DRIVE_SUPPORT,
        configurar_drive, sincronizar_archivos, verificar_credenciales
    )
except ImportError:
    DRIVE_SUPPORT = False


def configurar_logging():
    """Configurar sistema de logging"""
    log_file = settings.logs_dir / f"conciliacion_{datetime.now().strftime('%Y%m%d')}.log"

    logger.remove()  # Remover handler por defecto

    # Consola
    logger.add(
        sys.stdout,
        format=settings.log_format,
        level=settings.log_level,
        colorize=True,
    )

    # Archivo
    logger.add(
        str(log_file),
        format=settings.log_format,
        level="DEBUG",
        rotation="10 MB",
        retention="30 days",
    )

    return log_file


def test_conexion():
    """Probar conexión a la base de datos SAV7"""
    logger.info("Probando conexión a SAV7...")

    connector = SAV7Connector()
    if connector.test_connection():
        logger.success("✓ Conexión exitosa a SAV7")

        # Mostrar algunas tablas
        tablas = connector.get_tables()[:10]
        logger.info(f"Primeras tablas encontradas: {tablas}")

        return True
    else:
        logger.error("✗ No se pudo conectar a SAV7")
        logger.info("Verifica las variables de entorno en .env:")
        logger.info("  DB_SERVER, DB_DATABASE, DB_USERNAME, DB_PASSWORD")
        return False


def explorar_estructura():
    """Explorar estructura de la base de datos para encontrar tablas de remisiones"""
    logger.info("Explorando estructura de SAV7...")

    explorer = SAV7Explorer()
    reporte = explorer.generate_exploration_report()

    print(reporte)

    # Guardar reporte
    reporte_path = settings.output_dir / f"exploracion_sav7_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    with open(reporte_path, 'w', encoding='utf-8') as f:
        f.write(reporte)

    logger.info(f"Reporte de exploración guardado en: {reporte_path}")


def procesar_archivo(ruta_xml: str):
    """Procesar un archivo XML individual"""
    logger.info(f"Procesando archivo: {ruta_xml}")

    parser = CFDIParser()
    factura = parser.parse_archivo(ruta_xml)

    if not factura:
        logger.error("No se pudo parsear el archivo XML")
        return

    logger.info(f"Factura parseada: {factura}")
    logger.info(f"  UUID: {factura.uuid}")
    logger.info(f"  Emisor: {factura.nombre_emisor} ({factura.rfc_emisor})")
    logger.info(f"  Total: ${factura.total:,.2f}")
    logger.info(f"  Conceptos: {factura.total_conceptos}")

    # Intentar conciliar
    try:
        matcher = ConciliacionMatcher()
        resultado = matcher.conciliar_factura(factura)

        logger.info(f"\nResultado de conciliación:")
        logger.info(f"  Estatus: {resultado.resumen_estatus}")
        if resultado.remision:
            logger.info(f"  Remisión: {resultado.numero_remision}")
            logger.info(f"  Score: {resultado.score_matching:.2f}")
        if resultado.alertas:
            logger.info(f"  Alertas: {resultado.alertas}")

    except Exception as e:
        logger.warning(f"No se pudo conciliar (¿conexión a BD?): {e}")


def procesar_lote(dry_run: bool = False):
    """
    Procesar todos los XMLs en la carpeta input y consolidar matches al 100%

    Args:
        dry_run: Si es True, simula la consolidación sin escribir en BD
    """
    logger.info("=" * 60)
    logger.info("INICIANDO PROCESO DE CONCILIACIÓN")
    if dry_run:
        logger.info("MODO: SIMULACIÓN (dry-run, no escribe en BD)")
    else:
        logger.info("MODO: CONSOLIDACIÓN AUTOMÁTICA")
    logger.info("=" * 60)

    # 1. Cargar y parsear XMLs
    logger.info(f"\n[1/5] Cargando XMLs de: {settings.input_dir}")
    parser = CFDIParser()
    facturas = parser.parse_directorio(settings.input_dir)

    if not facturas:
        logger.warning("No se encontraron facturas para procesar")
        logger.info(f"Coloca archivos XML en: {settings.input_dir}")
        return

    logger.info(f"Facturas cargadas: {len(facturas)}")

    # 2. Conciliar facturas
    logger.info("\n[2/5] Conciliando facturas con remisiones...")
    matcher = ConciliacionMatcher()
    resultados = matcher.conciliar_lote(facturas)

    # Detectar duplicados
    alertas_duplicados = matcher.detectar_remisiones_duplicadas(resultados)
    if alertas_duplicados:
        logger.warning(f"Se detectaron {len(alertas_duplicados)} remisiones duplicadas:")
        for alerta in alertas_duplicados:
            logger.warning(f"  {alerta}")

    # 3. Validar resultados
    logger.info("\n[3/5] Validando resultados...")
    validator = ConciliacionValidator()
    resultados = validator.validar_lote(resultados)

    # Resumen de validación
    resumen = validator.generar_resumen_validacion(resultados)
    logger.info(f"\nResumen de conciliación:")
    logger.info(f"  Total facturas: {resumen['total_facturas']}")
    logger.info(f"  Conciliadas: {resumen['conciliadas_exitosamente']} ({resumen['porcentaje_exito']:.1f}%)")
    logger.info(f"  Con diferencias: {resumen['con_diferencias']}")
    logger.info(f"  Sin remisión: {resumen['sin_remision']}")
    logger.info(f"  Total alertas: {resumen['total_alertas']}")

    # 4. Consolidar matches 100%
    resultados_consolidacion = []
    logger.info("\n[4/5] Consolidando matches al 100%...")
    resultados_consolidacion = consolidar_matches_100(
        facturas, resultados, dry_run
    )

    # 5. Generar reportes
    logger.info("\n[5/5] Generando reportes...")
    report_generator = ExcelReportGenerator()

    # Reporte Excel
    excel_path = report_generator.generar_reporte(resultados)
    logger.info(f"  Excel: {excel_path}")

    # Reporte CSV
    csv_path = report_generator.generar_csv(resultados)
    logger.info(f"  CSV: {csv_path}")

    # Mover XMLs procesados (solo si no es dry-run)
    if not dry_run:
        mover_procesados(facturas)

    logger.info("\n" + "=" * 60)
    logger.info("PROCESO COMPLETADO")
    logger.info("=" * 60)

    # Resumen de consolidación
    if resultados_consolidacion:
        exitosos = sum(1 for r in resultados_consolidacion if r.exito)
        logger.info(f"\nConsolidación: {exitosos}/{len(resultados_consolidacion)} exitosos")

        for r in resultados_consolidacion:
            if r.exito:
                logger.success(f"  ✓ {r.numero_factura_erp}: {r.mensaje}")
            else:
                logger.warning(f"  ✗ {r.mensaje}")

    # Mostrar alertas críticas
    criticas = [r for r in resultados if any('CRITICA' in a for a in r.alertas)]
    if criticas:
        logger.warning(f"\n⚠ ATENCIÓN: {len(criticas)} facturas con alertas críticas")
        for r in criticas[:5]:
            logger.warning(f"  - {r.identificador_factura}: {r.alertas[0]}")


def consolidar_matches_100(
    facturas: list,
    resultados: list,
    dry_run: bool = False
) -> list:
    """
    Consolidar facturas que tienen match al 100%

    Args:
        facturas: Lista de facturas parseadas
        resultados: Lista de ResultadoConciliacion
        dry_run: Si True, solo simula sin escribir

    Returns:
        Lista de ResultadoConsolidacion
    """
    # Crear diccionario factura por UUID para lookup rápido
    facturas_dict = {f.uuid: f for f in facturas}

    # Filtrar matches al 100%
    matches_100 = [
        (facturas_dict[r.uuid_factura], r)
        for r in resultados
        if r.conciliacion_exitosa
        and r.diferencia_porcentaje == 0
        and r.remisiones
        and r.uuid_factura in facturas_dict
    ]

    if not matches_100:
        logger.info("No hay matches al 100% para consolidar")
        return []

    logger.info(f"Encontrados {len(matches_100)} matches al 100%")

    if dry_run:
        logger.info("SIMULACIÓN - Los siguientes registros SE CONSOLIDARÍAN:")
        resultados_sim = []
        for factura, resultado in matches_100:
            remisiones_ids = [r.id_remision for r in resultado.remisiones]
            logger.info(
                f"  Factura {factura.folio or factura.uuid[:8]} -> "
                f"Remisiones: {remisiones_ids}"
            )
            resultados_sim.append(ResultadoConsolidacion(
                exito=True,
                numero_factura_erp="(simulado)",
                remisiones_consolidadas=remisiones_ids,
                mensaje=f"SIMULACIÓN: Se consolidaría con {len(remisiones_ids)} remisiones"
            ))
        return resultados_sim

    # Consolidar de verdad
    consolidador = ConsolidadorSAV7()
    return consolidador.consolidar_lote(matches_100)


def mover_procesados(facturas):
    """Mover XMLs procesados a la carpeta processed"""
    for factura in facturas:
        if factura.archivo_xml:
            origen = Path(factura.archivo_xml)
            if origen.exists():
                destino = settings.processed_dir / origen.name
                try:
                    origen.rename(destino)
                    logger.debug(f"Movido: {origen.name} -> processed/")
                except Exception as e:
                    logger.warning(f"No se pudo mover {origen.name}: {e}")


def configurar_google_drive():
    """Asistente para configurar Google Drive"""
    if not DRIVE_SUPPORT:
        logger.error("Soporte de Google Drive no disponible")
        logger.info("Instale las dependencias:")
        logger.info("  pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib")
        return False

    logger.info("=" * 60)
    logger.info("CONFIGURACIÓN DE GOOGLE DRIVE")
    logger.info("=" * 60)

    # Verificar credenciales
    existe, msg = verificar_credenciales()
    if not existe:
        logger.error(msg)
        return False

    logger.info("Credenciales encontradas. Iniciando autenticación...")

    try:
        sync = DriveSync()

        # Autenticar (auto-detecta tipo de credenciales)
        if not sync.autenticar():
            logger.error("Error en autenticación")
            return False

        # Mostrar info de cuenta
        info = sync.obtener_info_cuenta()
        email = info.get('emailAddress', 'desconocido')
        logger.success(f"✓ Conectado como: {email}")

        # Si es Service Account, pedir ID de carpeta directamente
        if sync.es_service_account():
            logger.info("\n" + "=" * 60)
            logger.info("CONFIGURACIÓN SERVICE ACCOUNT")
            logger.info("=" * 60)
            logger.info("")
            logger.info("Para usar Google Drive con Service Account:")
            logger.info(f"  1. Comparte la carpeta de Drive con: {email}")
            logger.info("  2. Copia el ID de la carpeta de la URL:")
            logger.info("     https://drive.google.com/drive/folders/XXXXX")
            logger.info("                                          ^^^^^ <- Este es el ID")
            logger.info("")

            print("-" * 40)
            carpeta_id = input("Ingrese el ID de la carpeta: ").strip()

            if not carpeta_id:
                logger.error("Debe ingresar un ID de carpeta")
                return False

            # Intentar acceder a la carpeta
            if not sync.configurar_carpeta(carpeta_id=carpeta_id):
                logger.error("No se pudo acceder a la carpeta")
                logger.info("Verifique que:")
                logger.info(f"  - La carpeta esté compartida con: {email}")
                logger.info("  - El ID de la carpeta sea correcto")
                return False

            carpeta_elegida = {
                'id': carpeta_id,
                'name': sync._carpeta_nombre or carpeta_id
            }
        else:
            # OAuth: Listar carpetas disponibles
            logger.info("\nCarpetas disponibles en Drive:")
            carpetas = sync.listar_carpetas()

            if not carpetas:
                logger.warning("No se encontraron carpetas")
                return False

            for i, carpeta in enumerate(carpetas[:20], 1):
                logger.info(f"  {i}. {carpeta['name']} (ID: {carpeta['id'][:15]}...)")

            # Pedir selección
            print("\n" + "-" * 40)
            seleccion = input("Ingrese el número o nombre de la carpeta a usar: ").strip()

            if seleccion.isdigit():
                idx = int(seleccion) - 1
                if 0 <= idx < len(carpetas):
                    carpeta_elegida = carpetas[idx]
                else:
                    logger.error("Número inválido")
                    return False
            else:
                # Buscar por nombre
                carpeta_elegida = None
                for c in carpetas:
                    if c['name'].lower() == seleccion.lower():
                        carpeta_elegida = c
                        break
                if not carpeta_elegida:
                    logger.error(f"No se encontró carpeta: {seleccion}")
                    return False

            sync.configurar_carpeta(carpeta_id=carpeta_elegida['id'])

        # Guardar configuración
        config_file = settings.logs_dir.parent / "config" / "drive_config.txt"
        config_file.parent.mkdir(parents=True, exist_ok=True)
        with open(config_file, 'w') as f:
            f.write(f"DRIVE_FOLDER_ID={carpeta_elegida['id']}\n")
            f.write(f"DRIVE_FOLDER_NAME={carpeta_elegida['name']}\n")

        logger.success(f"\n✓ Configuración guardada")
        logger.info(f"  Carpeta: {carpeta_elegida['name']}")
        logger.info(f"  ID: {carpeta_elegida['id']}")
        logger.info(f"  Config: {config_file}")

        # Probar listando archivos
        archivos = sync.listar_archivos()
        logger.info(f"\nArchivos XML/PDF encontrados: {len(archivos)}")
        for a in archivos[:5]:
            logger.info(f"  - {a['name']}")
        if len(archivos) > 5:
            logger.info(f"  ... y {len(archivos) - 5} más")

        return True

    except Exception as e:
        logger.error(f"Error: {e}")
        return False


def sincronizar_desde_drive():
    """Sincronizar archivos desde Google Drive"""
    if not DRIVE_SUPPORT:
        logger.error("Soporte de Google Drive no disponible")
        return False

    logger.info("=" * 60)
    logger.info("SINCRONIZACIÓN GOOGLE DRIVE")
    logger.info("=" * 60)

    # Leer configuración
    config_file = settings.logs_dir.parent / "config" / "drive_config.txt"
    if not config_file.exists():
        logger.error("Google Drive no configurado")
        logger.info("Ejecute: python main.py --config-drive")
        return False

    # Leer carpeta ID
    carpeta_id = None
    carpeta_nombre = None
    with open(config_file, 'r') as f:
        for line in f:
            if line.startswith('DRIVE_FOLDER_ID='):
                carpeta_id = line.split('=', 1)[1].strip()
            elif line.startswith('DRIVE_FOLDER_NAME='):
                carpeta_nombre = line.split('=', 1)[1].strip()

    if not carpeta_id:
        logger.error("No se encontró ID de carpeta en configuración")
        return False

    logger.info(f"Carpeta Drive: {carpeta_nombre or carpeta_id}")
    logger.info(f"Destino local: {settings.input_dir}")

    try:
        sync = DriveSync()
        if not sync.autenticar():
            return False

        sync.configurar_carpeta(carpeta_id=carpeta_id)

        # Sincronizar
        resultado = sync.sincronizar(settings.input_dir)

        if resultado.exito:
            logger.success(f"✓ {resultado.resumen()}")
            if resultado.archivos_nuevos:
                logger.info("Archivos nuevos:")
                for a in resultado.archivos_nuevos:
                    logger.info(f"  + {a}")
            if resultado.archivos_actualizados:
                logger.info("Archivos actualizados:")
                for a in resultado.archivos_actualizados:
                    logger.info(f"  ↻ {a}")
        else:
            logger.warning(f"Sincronización con errores: {resultado.errores}")

        return resultado.exito

    except Exception as e:
        logger.error(f"Error: {e}")
        return False


def main():
    """Función principal"""
    parser = argparse.ArgumentParser(
        description="Agente de Conciliación SAT-ERP",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  python main.py                      Procesar XMLs y consolidar matches 100%%
  python main.py --dry-run            Simular consolidación sin escribir en BD
  python main.py --explorar           Explorar estructura de BD
  python main.py --test-conexion      Probar conexión a SAV7
  python main.py --archivo factura.xml  Procesar archivo específico
  python main.py --config-drive       Configurar Google Drive (primera vez)
  python main.py --sync-drive         Sincronizar desde Drive y procesar
  python main.py -s -d                Sincronizar y simular (sin escribir BD)
        """
    )

    parser.add_argument(
        '--explorar', '-e',
        action='store_true',
        help='Explorar estructura de base de datos SAV7'
    )

    parser.add_argument(
        '--test-conexion', '-t',
        action='store_true',
        help='Probar conexión a la base de datos'
    )

    parser.add_argument(
        '--archivo', '-a',
        type=str,
        help='Procesar un archivo XML específico'
    )

    parser.add_argument(
        '--dry-run', '-d',
        action='store_true',
        help='Simular consolidación sin escribir en la base de datos'
    )

    parser.add_argument(
        '--verbose', '-v',
        action='store_true',
        help='Mostrar información detallada'
    )

    parser.add_argument(
        '--sync-drive', '-s',
        action='store_true',
        help='Sincronizar archivos desde Google Drive antes de procesar'
    )

    parser.add_argument(
        '--config-drive', '-c',
        action='store_true',
        help='Configurar conexión a Google Drive (asistente interactivo)'
    )

    args = parser.parse_args()

    # Configurar logging
    if args.verbose:
        settings.log_level = "DEBUG"

    log_file = configurar_logging()
    logger.info(f"Log: {log_file}")

    # Ejecutar acción solicitada
    if args.config_drive:
        configurar_google_drive()
    elif args.test_conexion:
        test_conexion()
    elif args.explorar:
        if test_conexion():
            explorar_estructura()
    elif args.archivo:
        procesar_archivo(args.archivo)
    else:
        # Sincronizar desde Drive si se solicitó
        if args.sync_drive:
            if not sincronizar_desde_drive():
                logger.warning("Sincronización de Drive falló, continuando con archivos locales...")

        # Proceso principal - siempre consolida matches al 100%
        # Usa --dry-run para simular sin escribir
        procesar_lote(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
