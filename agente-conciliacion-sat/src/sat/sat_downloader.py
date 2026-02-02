"""
Descargador automático de facturas del SAT usando FIEL

Este módulo implementa la descarga masiva de CFDIs desde el SAT
utilizando la librería cfdiclient que maneja el Web Service oficial.

Requisitos:
- Certificado .cer de la FIEL
- Llave privada .key de la FIEL
- Contraseña de la llave privada

Instalación:
    pip install cfdiclient
"""
import os
import base64
import zipfile
import tempfile
from pathlib import Path
from typing import Optional, List, Tuple
from datetime import datetime
from dataclasses import dataclass
from enum import Enum
import time
from loguru import logger

# Intentar importar cfdiclient
try:
    from cfdiclient import (
        Autenticacion,
        SolicitaDescarga,
        VerificaSolicitudDescarga,
        DescargaMasiva,
        Fiel
    )
    CFDICLIENT_DISPONIBLE = True
except ImportError:
    CFDICLIENT_DISPONIBLE = False
    logger.warning(
        "cfdiclient no está instalado. "
        "Ejecuta: pip install cfdiclient"
    )

from config.settings import settings


class TipoDescarga(Enum):
    """Tipo de descarga"""
    CFDI = "CFDI"           # XMLs completos
    METADATA = "Metadata"    # Solo metadatos


class TipoComprobante(Enum):
    """Tipo de comprobante"""
    RECIBIDOS = "RfcReceptor"
    EMITIDOS = "RfcEmisor"


class EstadoSolicitud(Enum):
    """Estados de la solicitud"""
    ACEPTADA = 1
    EN_PROCESO = 2
    TERMINADA = 3
    ERROR = 4
    RECHAZADA = 5
    VENCIDA = 6


@dataclass
class FIELConfig:
    """Configuración de la FIEL"""

    cer_path: Path
    key_path: Path
    password: str

    def __post_init__(self):
        if self.cer_path:
            self.cer_path = Path(self.cer_path)
        else:
            self.cer_path = Path("")
        if self.key_path:
            self.key_path = Path(self.key_path)
        else:
            self.key_path = Path("")

    @classmethod
    def from_env(cls) -> 'FIELConfig':
        """Crear desde variables de entorno"""
        return cls(
            cer_path=os.getenv('FIEL_CER_PATH', ''),
            key_path=os.getenv('FIEL_KEY_PATH', ''),
            password=os.getenv('FIEL_PASSWORD', ''),
        )

    def is_valid(self) -> Tuple[bool, str]:
        """Verificar si la configuración es válida"""
        if not self.cer_path or str(self.cer_path) == "" or str(self.cer_path) == ".":
            return False, "Ruta del certificado .cer no configurada (FIEL_CER_PATH)"

        if not self.cer_path.exists():
            return False, f"Certificado no encontrado: {self.cer_path}"

        if not self.key_path or str(self.key_path) == "" or str(self.key_path) == ".":
            return False, "Ruta de la llave .key no configurada (FIEL_KEY_PATH)"

        if not self.key_path.exists():
            return False, f"Llave privada no encontrada: {self.key_path}"

        if not self.password:
            return False, "Contraseña no configurada (FIEL_PASSWORD)"

        return True, "Configuración válida"


class SATDownloader:
    """
    Descargador automático de facturas del SAT

    Uso:
        downloader = SATDownloader()

        # Verificar si está configurado
        disponible, mensaje = downloader.is_available()
        if disponible:
            archivos = downloader.descargar_recibidas(
                fecha_inicio=datetime(2024, 1, 1),
                fecha_fin=datetime(2024, 1, 31)
            )
        else:
            print(mensaje)
    """

    def __init__(self, fiel_config: Optional[FIELConfig] = None):
        self.config = fiel_config or FIELConfig.from_env()
        self.output_dir = settings.input_dir
        self._fiel = None
        self._rfc = None

    def is_available(self) -> Tuple[bool, str]:
        """Verificar si la descarga automática está disponible"""
        if not CFDICLIENT_DISPONIBLE:
            return False, "Librería cfdiclient no instalada. Ejecuta: pip install cfdiclient"

        return self.config.is_valid()

    def _get_fiel(self):
        """Obtener objeto FIEL para autenticación"""
        if not CFDICLIENT_DISPONIBLE:
            raise RuntimeError("cfdiclient no está instalado")

        if self._fiel is None:
            # Leer archivos de la FIEL
            with open(self.config.cer_path, 'rb') as f:
                cer_der = f.read()

            with open(self.config.key_path, 'rb') as f:
                key_der = f.read()

            self._fiel = Fiel(cer_der, key_der, self.config.password)
            self._rfc = self._fiel.rfc

            logger.info(f"FIEL cargada para RFC: {self._rfc}")

        return self._fiel

    def descargar_recibidas(
        self,
        fecha_inicio: datetime,
        fecha_fin: Optional[datetime] = None,
        rfc_emisor: Optional[str] = None,
        tipo_descarga: TipoDescarga = TipoDescarga.CFDI,
        max_intentos_verificacion: int = 30,
        intervalo_verificacion: int = 30,
    ) -> List[Path]:
        """
        Descargar facturas recibidas del SAT

        Args:
            fecha_inicio: Fecha inicial (inclusive)
            fecha_fin: Fecha final (inclusive, default=hoy)
            rfc_emisor: Filtrar por RFC del emisor
            tipo_descarga: CFDI (XMLs) o Metadata
            max_intentos_verificacion: Máximo de intentos para verificar estado
            intervalo_verificacion: Segundos entre verificaciones

        Returns:
            Lista de rutas a los XMLs descargados
        """
        disponible, mensaje = self.is_available()
        if not disponible:
            logger.error(mensaje)
            print(self.get_instrucciones())
            raise RuntimeError(mensaje)

        fecha_fin = fecha_fin or datetime.now()

        logger.info("=" * 60)
        logger.info("DESCARGA AUTOMÁTICA DEL SAT")
        logger.info("=" * 60)

        # 1. Cargar FIEL y autenticar
        fiel = self._get_fiel()
        logger.info(f"RFC: {self._rfc}")
        logger.info(f"Periodo: {fecha_inicio.date()} a {fecha_fin.date()}")
        logger.info(f"Tipo: Facturas recibidas")

        try:
            # 2. Autenticar con el SAT
            logger.info("\n[1/4] Autenticando con el SAT...")
            auth = Autenticacion(fiel)
            token = auth.obtener_token()
            logger.success("Autenticación exitosa")

            # 3. Crear solicitud de descarga
            logger.info("\n[2/4] Creando solicitud de descarga...")
            solicitud = SolicitaDescarga(fiel)

            resultado = solicitud.solicitar_descarga(
                token=token,
                rfc_solicitante=self._rfc,
                fecha_inicial=fecha_inicio.strftime('%Y-%m-%d'),
                fecha_final=fecha_fin.strftime('%Y-%m-%d'),
                tipo_solicitud=tipo_descarga.value,
                tipo_comprobante=TipoComprobante.RECIBIDOS.value,
                rfc_emisor=rfc_emisor,
            )

            if resultado.get('cod_estatus') != '5000':
                error = resultado.get('mensaje', 'Error desconocido')
                logger.error(f"Error en solicitud: {error}")
                raise RuntimeError(f"SAT rechazó la solicitud: {error}")

            id_solicitud = resultado.get('id_solicitud')
            logger.success(f"Solicitud creada: {id_solicitud}")

            # 4. Esperar procesamiento
            logger.info("\n[3/4] Esperando procesamiento del SAT...")
            paquetes = self._esperar_procesamiento(
                token=token,
                id_solicitud=id_solicitud,
                fiel=fiel,
                max_intentos=max_intentos_verificacion,
                intervalo=intervalo_verificacion,
            )

            if not paquetes:
                logger.warning("No se encontraron facturas en el periodo solicitado")
                return []

            logger.success(f"{len(paquetes)} paquete(s) disponible(s)")

            # 5. Descargar paquetes
            logger.info("\n[4/4] Descargando archivos...")
            archivos = self._descargar_paquetes(token, paquetes, fiel)

            logger.info("\n" + "=" * 60)
            logger.success(f"DESCARGA COMPLETADA: {len(archivos)} archivos XML")
            logger.info(f"  Ubicación: {self.output_dir}")
            logger.info("=" * 60)

            return archivos

        except Exception as e:
            logger.error(f"Error en descarga del SAT: {e}")
            raise

    def _esperar_procesamiento(
        self,
        token: str,
        id_solicitud: str,
        fiel,
        max_intentos: int,
        intervalo: int,
    ) -> List[str]:
        """Esperar a que el SAT procese la solicitud"""
        verificador = VerificaSolicitudDescarga(fiel)

        for intento in range(1, max_intentos + 1):
            resultado = verificador.verificar_descarga(
                token=token,
                rfc_solicitante=self._rfc,
                id_solicitud=id_solicitud,
            )

            estado = int(resultado.get('estado_solicitud', 0))
            codigo = resultado.get('cod_estatus', '')
            mensaje = resultado.get('mensaje', '')

            logger.debug(f"Verificación {intento}/{max_intentos}: Estado={estado}, Código={codigo}")

            if estado == EstadoSolicitud.TERMINADA.value:
                # Obtener lista de paquetes
                paquetes = resultado.get('paquetes', [])
                if isinstance(paquetes, str):
                    paquetes = [paquetes] if paquetes else []
                return paquetes

            elif estado == EstadoSolicitud.ERROR.value:
                raise RuntimeError(f"Error del SAT: {mensaje}")

            elif estado == EstadoSolicitud.RECHAZADA.value:
                raise RuntimeError(f"Solicitud rechazada: {mensaje}")

            elif estado == EstadoSolicitud.VENCIDA.value:
                raise RuntimeError("La solicitud venció antes de completarse")

            elif estado in [EstadoSolicitud.ACEPTADA.value, EstadoSolicitud.EN_PROCESO.value]:
                logger.info(f"  Procesando... ({intento}/{max_intentos})")
                time.sleep(intervalo)

            else:
                logger.warning(f"  Estado desconocido: {estado}")
                time.sleep(intervalo)

        raise TimeoutError(
            f"El SAT no completó la solicitud en {max_intentos * intervalo} segundos"
        )

    def _descargar_paquetes(
        self,
        token: str,
        paquetes: List[str],
        fiel,
    ) -> List[Path]:
        """Descargar y extraer paquetes de XMLs"""
        descargador = DescargaMasiva(fiel)
        archivos_extraidos = []

        for i, id_paquete in enumerate(paquetes, 1):
            logger.info(f"  Descargando paquete {i}/{len(paquetes)}: {id_paquete[:20]}...")

            resultado = descargador.descargar_paquete(
                token=token,
                rfc_solicitante=self._rfc,
                id_paquete=id_paquete,
            )

            if resultado.get('cod_estatus') != '5000':
                logger.warning(f"  Error en paquete: {resultado.get('mensaje')}")
                continue

            # El paquete viene en base64
            paquete_b64 = resultado.get('paquete_b64')
            if not paquete_b64:
                continue

            # Extraer XMLs del ZIP
            archivos = self._extraer_zip_base64(paquete_b64, i)
            archivos_extraidos.extend(archivos)
            logger.info(f"  {len(archivos)} XMLs extraídos del paquete {i}")

        return archivos_extraidos

    def _extraer_zip_base64(self, contenido_b64: str, num_paquete: int) -> List[Path]:
        """Extraer XMLs de un ZIP en base64"""
        archivos = []

        # Decodificar base64
        zip_data = base64.b64decode(contenido_b64)

        # Crear archivo temporal
        with tempfile.NamedTemporaryFile(delete=False, suffix='.zip') as tmp:
            tmp.write(zip_data)
            tmp_path = tmp.name

        try:
            # Extraer XMLs
            with zipfile.ZipFile(tmp_path, 'r') as zf:
                for nombre in zf.namelist():
                    if nombre.lower().endswith('.xml'):
                        # Extraer a la carpeta de salida
                        destino = self.output_dir / nombre
                        with zf.open(nombre) as source:
                            with open(destino, 'wb') as target:
                                target.write(source.read())
                        archivos.append(destino)

        finally:
            # Limpiar archivo temporal
            os.unlink(tmp_path)

        return archivos

    def get_instrucciones(self) -> str:
        """Obtener instrucciones de configuración"""
        return f"""
╔══════════════════════════════════════════════════════════════════════════╗
║           CONFIGURACIÓN DE DESCARGA AUTOMÁTICA DEL SAT                   ║
╠══════════════════════════════════════════════════════════════════════════╣
║                                                                          ║
║  Para descargar facturas automáticamente, necesitas la FIEL del cliente: ║
║                                                                          ║
║  1. ARCHIVOS NECESARIOS:                                                 ║
║     • Certificado (.cer) - Archivo de certificado de la FIEL             ║
║     • Llave privada (.key) - Archivo de llave de la FIEL                 ║
║     • Contraseña de la llave privada                                     ║
║                                                                          ║
║  2. CONFIGURACIÓN EN .env:                                               ║
║     Agrega estas líneas al archivo .env:                                 ║
║                                                                          ║
║     FIEL_CER_PATH=C:/ruta/certificado.cer                                ║
║     FIEL_KEY_PATH=C:/ruta/llave.key                                      ║
║     FIEL_PASSWORD=tu_contraseña_secreta                                  ║
║                                                                          ║
║  3. INSTALAR DEPENDENCIA:                                                ║
║     pip install cfdiclient                                               ║
║                                                                          ║
║  ⚠️  IMPORTANTE: La FIEL es información sensible. Mantenla segura.       ║
║                                                                          ║
╠══════════════════════════════════════════════════════════════════════════╣
║  ALTERNATIVA SIN FIEL:                                                   ║
║  Descarga manual desde https://portalcfdi.facturaelectronica.sat.gob.mx/ ║
║  o usa ElConta Descarga Masiva y coloca los XMLs en:                     ║
║  {str(self.output_dir):<70}║
╚══════════════════════════════════════════════════════════════════════════╝
"""


# Función de conveniencia para usar desde main.py
def descargar_facturas_sat(
    fecha_inicio: datetime,
    fecha_fin: Optional[datetime] = None,
) -> List[Path]:
    """
    Función simple para descargar facturas del SAT

    Args:
        fecha_inicio: Fecha inicial
        fecha_fin: Fecha final (default: hoy)

    Returns:
        Lista de archivos XML descargados
    """
    downloader = SATDownloader()
    return downloader.descargar_recibidas(fecha_inicio, fecha_fin)
