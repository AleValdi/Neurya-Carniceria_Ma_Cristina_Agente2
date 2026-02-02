"""
Programador de tareas para el Agente de Conciliación

Modos de ejecución:
1. Manual: python main.py
2. Tarea programada: python scheduler.py --hora 07:00
3. Servicio continuo: python scheduler.py --servicio --intervalo 60

También puede instalarse como servicio de Windows.
"""
import sys
import argparse
import time
from datetime import datetime, timedelta
from pathlib import Path
import schedule
from loguru import logger

# Configurar path
sys.path.insert(0, str(Path(__file__).parent))

from config.settings import settings
from src.sat.sat_downloader import SATDownloader
from main import procesar_lote, configurar_logging


class AgentScheduler:
    """Programador de tareas para el agente de conciliación"""

    def __init__(self):
        self.ultima_ejecucion = None
        self.ejecuciones_hoy = 0
        self.errores_consecutivos = 0
        self.max_errores = 3

    def ejecutar_conciliacion(self):
        """Ejecutar el proceso de conciliación completo"""
        logger.info("=" * 70)
        logger.info(f"EJECUCIÓN PROGRAMADA - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info("=" * 70)

        try:
            # 1. Intentar descargar del SAT si está configurado
            self._intentar_descarga_sat()

            # 2. Procesar XMLs y conciliar
            procesar_lote()

            # Actualizar estadísticas
            self.ultima_ejecucion = datetime.now()
            self.ejecuciones_hoy += 1
            self.errores_consecutivos = 0

            logger.success(f"Ejecución completada exitosamente")

        except Exception as e:
            self.errores_consecutivos += 1
            logger.error(f"Error en ejecución: {e}")

            if self.errores_consecutivos >= self.max_errores:
                logger.critical(
                    f"Se alcanzó el máximo de errores consecutivos ({self.max_errores}). "
                    "Revisa la configuración y los logs."
                )

    def _intentar_descarga_sat(self):
        """Intentar descargar facturas del SAT si está configurado"""
        try:
            downloader = SATDownloader()
            disponible, mensaje = downloader.is_available()

            if disponible:
                logger.info("Descargando facturas del SAT...")
                # Descargar facturas del último mes
                fecha_inicio = datetime.now() - timedelta(days=30)
                archivos = downloader.descargar_recibidas(fecha_inicio)
                logger.info(f"Descargados {len(archivos)} XMLs del SAT")
            else:
                logger.debug(f"Descarga SAT no disponible: {mensaje}")
                logger.info("Procesando XMLs existentes en carpeta input/")

        except Exception as e:
            logger.warning(f"No se pudo descargar del SAT: {e}")
            logger.info("Continuando con XMLs existentes...")

    def monitorear_carpeta(self, intervalo_minutos: int = 5):
        """
        Monitorear carpeta de entrada por nuevos XMLs

        Args:
            intervalo_minutos: Intervalo de verificación en minutos
        """
        logger.info(f"Iniciando monitoreo de carpeta: {settings.input_dir}")
        logger.info(f"Intervalo de verificación: {intervalo_minutos} minutos")

        archivos_procesados = set()

        while True:
            try:
                # Buscar nuevos XMLs
                xmls_actuales = set(settings.input_dir.glob("*.xml"))
                xmls_actuales.update(settings.input_dir.glob("*.XML"))

                nuevos = xmls_actuales - archivos_procesados

                if nuevos:
                    logger.info(f"Detectados {len(nuevos)} nuevos XMLs")
                    self.ejecutar_conciliacion()
                    archivos_procesados.update(nuevos)
                else:
                    logger.debug("Sin nuevos XMLs")

                time.sleep(intervalo_minutos * 60)

            except KeyboardInterrupt:
                logger.info("Monitoreo detenido por el usuario")
                break
            except Exception as e:
                logger.error(f"Error en monitoreo: {e}")
                time.sleep(60)  # Esperar 1 minuto antes de reintentar

    def get_status(self) -> dict:
        """Obtener estado del scheduler"""
        return {
            'ultima_ejecucion': self.ultima_ejecucion.isoformat() if self.ultima_ejecucion else None,
            'ejecuciones_hoy': self.ejecuciones_hoy,
            'errores_consecutivos': self.errores_consecutivos,
            'proximo_job': str(schedule.next_run()) if schedule.jobs else None,
        }


def ejecutar_servicio(hora_programada: str = None, intervalo_minutos: int = None):
    """
    Ejecutar como servicio continuo

    Args:
        hora_programada: Hora para ejecución diaria (ej: "07:00")
        intervalo_minutos: Intervalo para ejecución periódica
    """
    scheduler = AgentScheduler()

    logger.info("=" * 70)
    logger.info("AGENTE DE CONCILIACIÓN - MODO SERVICIO")
    logger.info("=" * 70)

    if hora_programada:
        # Programar ejecución diaria
        schedule.every().day.at(hora_programada).do(scheduler.ejecutar_conciliacion)
        logger.info(f"Programado: Ejecución diaria a las {hora_programada}")

    if intervalo_minutos:
        # Programar ejecución periódica
        schedule.every(intervalo_minutos).minutes.do(scheduler.ejecutar_conciliacion)
        logger.info(f"Programado: Ejecución cada {intervalo_minutos} minutos")

    if not hora_programada and not intervalo_minutos:
        # Por defecto: monitorear carpeta
        logger.info("Sin programación específica, iniciando monitoreo de carpeta...")
        scheduler.monitorear_carpeta(intervalo_minutos=5)
        return

    # Ejecutar una vez al inicio
    logger.info("Ejecutando conciliación inicial...")
    scheduler.ejecutar_conciliacion()

    # Loop principal
    logger.info("\nServicio activo. Presiona Ctrl+C para detener.\n")

    try:
        while True:
            schedule.run_pending()
            time.sleep(60)  # Verificar cada minuto
    except KeyboardInterrupt:
        logger.info("\nServicio detenido por el usuario")


def main():
    """Punto de entrada para el scheduler"""
    parser = argparse.ArgumentParser(
        description="Programador del Agente de Conciliación",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos de uso:

  # Ejecución única (como tarea programada de Windows)
  python scheduler.py --una-vez

  # Ejecución diaria a las 7:00 AM
  python scheduler.py --hora 07:00

  # Ejecución cada 60 minutos
  python scheduler.py --intervalo 60

  # Ejecución diaria + cada hora
  python scheduler.py --hora 07:00 --intervalo 60

  # Monitoreo continuo de carpeta (cada 5 min por defecto)
  python scheduler.py --monitorear

  # Como servicio de Windows (requiere pywin32):
  python scheduler.py --instalar-servicio
        """
    )

    parser.add_argument(
        '--una-vez',
        action='store_true',
        help='Ejecutar una sola vez y terminar'
    )

    parser.add_argument(
        '--hora',
        type=str,
        help='Hora para ejecución diaria (formato HH:MM, ej: 07:00)'
    )

    parser.add_argument(
        '--intervalo',
        type=int,
        help='Intervalo en minutos para ejecución periódica'
    )

    parser.add_argument(
        '--monitorear',
        action='store_true',
        help='Monitorear carpeta input/ por nuevos XMLs'
    )

    parser.add_argument(
        '--intervalo-monitoreo',
        type=int,
        default=5,
        help='Minutos entre verificaciones de carpeta (default: 5)'
    )

    args = parser.parse_args()

    # Configurar logging
    configurar_logging()

    scheduler = AgentScheduler()

    if args.una_vez:
        # Ejecución única
        scheduler.ejecutar_conciliacion()

    elif args.monitorear:
        # Modo monitoreo
        scheduler.monitorear_carpeta(args.intervalo_monitoreo)

    elif args.hora or args.intervalo:
        # Modo servicio con programación
        ejecutar_servicio(
            hora_programada=args.hora,
            intervalo_minutos=args.intervalo
        )

    else:
        # Sin argumentos: mostrar ayuda
        parser.print_help()
        print("\n" + "=" * 50)
        print("MODOS DE EJECUCIÓN DISPONIBLES:")
        print("=" * 50)
        print("""
1. MANUAL (bajo demanda):
   python main.py

2. TAREA PROGRAMADA (una vez):
   python scheduler.py --una-vez
   (Configura esto en el Programador de Tareas de Windows)

3. SERVICIO CONTINUO:
   python scheduler.py --hora 07:00 --intervalo 60
   (Ejecuta diario a las 7am + cada hora)

4. MONITOREO DE CARPETA:
   python scheduler.py --monitorear
   (Procesa automáticamente cuando aparecen nuevos XMLs)
        """)


if __name__ == "__main__":
    main()
