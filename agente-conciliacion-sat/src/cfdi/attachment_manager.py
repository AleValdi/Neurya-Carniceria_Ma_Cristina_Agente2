"""
Gestor de adjuntos CFDI para SAV7

Este módulo maneja la copia de archivos XML y PDF al directorio de recepciones
de SAV7 y actualiza los campos FacturaElectronica* en la base de datos.

El flujo es:
1. Generar nombre SAV7: {RFC}_REC_F{NUMREC}_{YYYYMMDD}
2. Copiar XML al directorio de red
3. Buscar PDF existente o generarlo desde XML
4. Copiar PDF al directorio de red
5. Actualizar campos en SAVRecC
"""
import shutil
from pathlib import Path
from datetime import datetime
from typing import Optional, TYPE_CHECKING
from dataclasses import dataclass
from loguru import logger

from config.settings import settings
from src.cfdi.pdf_generator import PDFGenerator

if TYPE_CHECKING:
    from config.database import DatabaseConnection
    from src.sat.models import Factura


@dataclass
class AttachmentResult:
    """Resultado de adjuntar archivos a una factura consolidada"""
    exito: bool
    nombre_base: Optional[str] = None
    archivo_xml_destino: Optional[str] = None
    archivo_pdf_destino: Optional[str] = None
    mensaje: str = ""
    error: Optional[str] = None
    pdf_generado: bool = False  # True si el PDF fue generado desde XML


class AttachmentManager:
    """
    Gestiona la copia de archivos CFDI al directorio SAV7
    y actualiza los campos FacturaElectronica* en la BD.

    Características:
    - Genera nombres según convención SAV7: {RFC}_REC_F{NUMREC}_{YYYYMMDD}
    - Copia XML y PDF al directorio de red configurado
    - Puede generar PDF desde XML si no existe (usando satcfdi)
    - Actualiza campos FacturaElectronica* en SAVRecC
    - Los errores NO bloquean la consolidación (solo generan alertas)
    """

    # Número máximo de reintentos para copiar archivos a red
    MAX_REINTENTOS = 3
    DELAY_ENTRE_REINTENTOS = 1  # segundos

    def __init__(self, db: Optional['DatabaseConnection'] = None):
        """
        Inicializa el gestor de adjuntos.

        Args:
            db: Conexión a base de datos. Si no se proporciona,
                se creará una nueva al momento de actualizar.
        """
        self._db = db
        self.pdf_generator = PDFGenerator()
        self.directorio_destino = settings.cfdi_adjuntos_dir

    @property
    def db(self) -> 'DatabaseConnection':
        """Obtiene la conexión a BD (lazy loading)."""
        if self._db is None:
            from config.database import db_connection
            self._db = db_connection
        return self._db

    def generar_nombre_sav7(
        self,
        rfc_emisor: str,
        num_rec: int,
        fecha: datetime
    ) -> str:
        """
        Genera nombre de archivo según convención SAV7.

        Formato: {RFC_EMISOR}_REC_F{NUMREC}_{YYYYMMDD}
        Ejemplo: SIS850415B31_REC_F068590_20260206

        Args:
            rfc_emisor: RFC del emisor de la factura
            num_rec: Número de recepción de la factura F creada
            fecha: Fecha de la consolidación

        Returns:
            Nombre base del archivo (sin extensión)
        """
        fecha_str = fecha.strftime('%Y%m%d')
        # Formato con 6 dígitos para el número de recepción
        return f"{rfc_emisor}_REC_F{num_rec:06d}_{fecha_str}"

    def adjuntar(
        self,
        factura: 'Factura',
        num_rec: int,
        fecha_consolidacion: datetime
    ) -> AttachmentResult:
        """
        Adjunta XML y PDF de una factura consolidada al directorio SAV7.

        Args:
            factura: Factura SAT con ruta al XML original
            num_rec: Número de recepción de la factura F creada
            fecha_consolidacion: Fecha de la consolidación

        Returns:
            AttachmentResult con el resultado de la operación
        """
        # Verificar si está habilitado
        if not settings.cfdi_adjuntos_habilitado:
            return AttachmentResult(
                exito=True,
                mensaje="Adjuntos deshabilitados por configuración"
            )

        # Verificar directorio destino
        if not self._verificar_directorio_destino():
            return AttachmentResult(
                exito=False,
                error=f"Directorio no accesible: {self.directorio_destino}"
            )

        # Generar nombre base
        nombre_base = self.generar_nombre_sav7(
            factura.rfc_emisor,
            num_rec,
            fecha_consolidacion
        )

        try:
            # 1. Copiar XML
            xml_resultado = self._copiar_xml(factura, nombre_base)

            # 2. Copiar o generar PDF
            pdf_resultado, pdf_generado = self._procesar_pdf(factura, nombre_base)

            # 3. Actualizar BD solo si al menos un archivo fue copiado
            if xml_resultado or pdf_resultado:
                self._actualizar_bd(num_rec, nombre_base)

            # Construir mensaje de resultado
            archivos = []
            if xml_resultado:
                archivos.append("XML")
            if pdf_resultado:
                archivos.append("PDF" + (" (generado)" if pdf_generado else ""))

            if archivos:
                mensaje = f"Archivos adjuntados: {', '.join(archivos)} -> {nombre_base}"
            else:
                mensaje = "No se pudo adjuntar ningún archivo"

            return AttachmentResult(
                exito=bool(xml_resultado or pdf_resultado),
                nombre_base=nombre_base,
                archivo_xml_destino=xml_resultado,
                archivo_pdf_destino=pdf_resultado,
                pdf_generado=pdf_generado,
                mensaje=mensaje
            )

        except Exception as e:
            logger.error(f"Error adjuntando archivos para F-{num_rec}: {e}")
            return AttachmentResult(
                exito=False,
                nombre_base=nombre_base,
                error=str(e)
            )

    def _verificar_directorio_destino(self) -> bool:
        """
        Verifica que el directorio destino sea accesible.

        Returns:
            True si el directorio existe y es accesible
        """
        try:
            # Para rutas de red, verificar si existe
            if str(self.directorio_destino).startswith('\\\\'):
                # Ruta UNC (red Windows)
                return self.directorio_destino.exists()
            else:
                # Ruta local
                return self.directorio_destino.exists() and self.directorio_destino.is_dir()
        except (OSError, PermissionError) as e:
            logger.warning(f"No se puede acceder a {self.directorio_destino}: {e}")
            return False

    def _copiar_xml(
        self,
        factura: 'Factura',
        nombre_base: str
    ) -> Optional[str]:
        """
        Copia el XML al directorio destino.

        Args:
            factura: Factura con ruta al XML original
            nombre_base: Nombre base para el archivo destino

        Returns:
            Ruta del archivo destino si se copió exitosamente, None si no
        """
        if not factura.archivo_xml:
            logger.warning(f"Factura {factura.uuid[:8]} sin ruta de archivo XML")
            return None

        xml_origen = Path(factura.archivo_xml)
        if not xml_origen.exists():
            logger.warning(f"XML no encontrado: {xml_origen}")
            return None

        xml_destino = self.directorio_destino / f"{nombre_base}.xml"

        try:
            shutil.copy2(xml_origen, xml_destino)
            logger.debug(f"XML copiado: {xml_origen.name} -> {xml_destino.name}")
            return str(xml_destino)
        except Exception as e:
            logger.error(f"Error copiando XML a {xml_destino}: {e}")
            return None

    def _procesar_pdf(
        self,
        factura: 'Factura',
        nombre_base: str
    ) -> tuple[Optional[str], bool]:
        """
        Busca PDF existente o lo genera desde XML.

        Args:
            factura: Factura con ruta al XML original
            nombre_base: Nombre base para el archivo destino

        Returns:
            Tupla (ruta_pdf_destino, fue_generado)
            - ruta_pdf_destino: Ruta del PDF si se copió/generó, None si no
            - fue_generado: True si el PDF fue generado desde XML
        """
        pdf_destino = self.directorio_destino / f"{nombre_base}.pdf"

        # 1. Buscar PDF existente junto al XML
        if factura.archivo_xml:
            xml_path = Path(factura.archivo_xml)

            # Importar función de búsqueda de PDF
            try:
                from src.pdf import buscar_pdf_para_xml
                pdf_existente = buscar_pdf_para_xml(xml_path, uuid_factura=factura.uuid)

                if pdf_existente and pdf_existente.exists():
                    try:
                        shutil.copy2(pdf_existente, pdf_destino)
                        logger.debug(f"PDF copiado: {pdf_existente.name} -> {pdf_destino.name}")
                        return str(pdf_destino), False
                    except Exception as e:
                        logger.warning(f"Error copiando PDF existente: {e}")
            except ImportError:
                logger.debug("Módulo PDF no disponible para buscar PDF existente")

        # 2. Generar PDF desde XML si está habilitado
        if settings.cfdi_generar_pdf and self.pdf_generator.disponible:
            if factura.archivo_xml:
                xml_path = Path(factura.archivo_xml)
                if self.pdf_generator.generar_desde_xml(xml_path, pdf_destino):
                    logger.info(f"PDF generado desde XML: {pdf_destino.name}")
                    return str(pdf_destino), True

        logger.warning(f"No se pudo obtener PDF para {factura.uuid[:8]}")
        return None, False

    def _actualizar_bd(self, num_rec: int, nombre_base: str):
        """
        Actualiza campos FacturaElectronica* en SAVRecC.

        Args:
            num_rec: Número de recepción de la factura F
            nombre_base: Nombre base del archivo (sin extensión)

        Raises:
            Exception si hay error en la actualización
        """
        query = """
            UPDATE SAVRecC
            SET
                FacturaElectronica = ?,
                FacturaElectronicaExiste = 1,
                FacturaElectronicaValida = 1,
                FacturaElectronicaEstatus = 'Vigente'
            WHERE Serie = 'F' AND NumRec = ?
        """

        try:
            with self.db.get_cursor() as cursor:
                cursor.execute(query, (nombre_base, num_rec))
            logger.debug(f"BD actualizada: FacturaElectronica='{nombre_base}' para F-{num_rec}")
        except Exception as e:
            logger.error(f"Error actualizando BD para F-{num_rec}: {e}")
            raise
