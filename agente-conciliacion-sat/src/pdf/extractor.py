"""
Módulo para extraer números de remisión desde PDFs de facturas de proveedores.

Soporta diferentes formatos de proveedores mediante patrones regex flexibles.
"""

import re
from pathlib import Path
from typing import List, Optional, Tuple, Dict
from dataclasses import dataclass
from loguru import logger

try:
    import fitz  # PyMuPDF
    PDF_SUPPORT = True
    PDF_LIBRARY = "pymupdf"
except ImportError:
    try:
        import pdfplumber
        PDF_SUPPORT = True
        PDF_LIBRARY = "pdfplumber"
    except ImportError:
        PDF_SUPPORT = False
        PDF_LIBRARY = None
        logger.warning("PyMuPDF ni pdfplumber instalados. Soporte PDF deshabilitado.")


@dataclass
class PDFExtractResult:
    """Resultado de la extracción de un PDF"""
    archivo: str
    remisiones_encontradas: List[int]
    texto_contexto: str  # Texto donde se encontraron las remisiones
    uuid_encontrado: Optional[str] = None
    total_encontrado: Optional[float] = None
    orden_compra: Optional[str] = None  # Número de Orden de Compra del proveedor
    exito: bool = True
    error: Optional[str] = None


class PDFRemisionExtractor:
    """
    Extrae números de remisión desde PDFs de facturas de proveedores.

    Busca patrones comunes como:
    - R-12345, R-12346, R-12347
    - Remisiones: 12345, 12346
    - Facturas/Remisiones: R-16662, R-16672...
    """

    # Patrones para encontrar números de remisión
    PATRONES_REMISION = [
        # Patrón principal: R-XXXXX (el más común en SAV7)
        r'R-(\d{4,6})',
        # Patrón alternativo: números después de "remision(es)"
        r'[Rr]emisi[oó]n(?:es)?[:\s]+(?:R-)?(\d{4,6})',
        # Patrón para listas separadas por comas
        r'[Rr]emisi[oó]n(?:es)?[:\s]+([\d,\s\-R]+)',
    ]

    # Patrón para UUID de factura SAT
    PATRON_UUID = r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}'

    # Patrón para totales monetarios
    PATRON_TOTAL = r'TOTAL[:\s]*\$?([\d,]+\.?\d*)'

    def __init__(self):
        if not PDF_SUPPORT:
            raise ImportError(
                "PyMuPDF ni pdfplumber están instalados. "
                "Ejecute: pip install pymupdf"
            )

    def extraer_remisiones(self, pdf_path: Path) -> PDFExtractResult:
        """
        Extrae números de remisión de un archivo PDF.

        Args:
            pdf_path: Ruta al archivo PDF

        Returns:
            PDFExtractResult con las remisiones encontradas
        """
        if not pdf_path.exists():
            return PDFExtractResult(
                archivo=str(pdf_path),
                remisiones_encontradas=[],
                texto_contexto="",
                exito=False,
                error=f"Archivo no encontrado: {pdf_path}"
            )

        try:
            texto_completo = self._extraer_texto(pdf_path)

            if not texto_completo:
                return PDFExtractResult(
                    archivo=str(pdf_path),
                    remisiones_encontradas=[],
                    texto_contexto="",
                    exito=False,
                    error="No se pudo extraer texto del PDF"
                )

            # Buscar remisiones
            remisiones, contexto = self._buscar_remisiones(texto_completo)

            # Buscar UUID
            uuid = self._buscar_uuid(texto_completo)

            # Buscar total
            total = self._buscar_total(texto_completo)

            # Buscar orden de compra
            orden_compra = self._buscar_orden_compra(texto_completo)

            logger.info(
                f"PDF {pdf_path.name}: {len(remisiones)} remisiones encontradas"
                f"{f', UUID: {uuid[:8]}...' if uuid else ''}"
                f"{f', OC: {orden_compra}' if orden_compra else ''}"
            )

            return PDFExtractResult(
                archivo=str(pdf_path),
                remisiones_encontradas=remisiones,
                texto_contexto=contexto,
                uuid_encontrado=uuid,
                total_encontrado=total,
                orden_compra=orden_compra,
                exito=True
            )

        except Exception as e:
            logger.error(f"Error procesando PDF {pdf_path}: {e}")
            return PDFExtractResult(
                archivo=str(pdf_path),
                remisiones_encontradas=[],
                texto_contexto="",
                exito=False,
                error=str(e)
            )

    def _extraer_texto(self, pdf_path: Path) -> str:
        """Extrae todo el texto del PDF usando PyMuPDF o pdfplumber"""
        texto_paginas = []

        if PDF_LIBRARY == "pymupdf":
            import fitz
            with fitz.open(pdf_path) as pdf:
                for pagina in pdf:
                    texto = pagina.get_text()
                    if texto:
                        texto_paginas.append(texto)
        else:
            import pdfplumber
            with pdfplumber.open(pdf_path) as pdf:
                for pagina in pdf.pages:
                    texto = pagina.extract_text()
                    if texto:
                        texto_paginas.append(texto)

        return "\n".join(texto_paginas)

    def _buscar_remisiones(self, texto: str) -> Tuple[List[int], str]:
        """
        Busca números de remisión en el texto usando múltiples patrones.

        Returns:
            Tuple de (lista de números, contexto donde se encontraron)
        """
        remisiones = set()
        contexto = ""

        # Patrón 1: Buscar línea con "Facturas/Remisiones:" o similar
        patron_linea = r'(?:Facturas?/?|)[Rr]emisi[oó]n(?:es)?[:\s]+([\s\S]{0,200}?)(?:\n\n|Observaciones|Cantidad)'
        match_linea = re.search(patron_linea, texto)

        if match_linea:
            bloque = match_linea.group(1)
            contexto = bloque[:200]

            # Extraer todos los R-XXXXX del bloque
            numeros = re.findall(r'R-(\d{4,6})', bloque)
            for num in numeros:
                remisiones.add(int(num))

        # Patrón 2: Buscar todos los R-XXXXX en el documento
        if not remisiones:
            # Solo si no encontramos en el bloque específico
            todos_r = re.findall(r'R-(\d{4,6})', texto)

            # Filtrar: solo considerar si hay varios números consecutivos
            # (evita falsos positivos de códigos de producto)
            if len(todos_r) >= 2:
                for num in todos_r:
                    remisiones.add(int(num))
                contexto = f"Encontrados {len(remisiones)} patrones R-XXXXX en el documento"

        # Patrón 3: Buscar en observaciones
        if not remisiones:
            patron_obs = r'[Oo]bservaciones[:\s]+([\s\S]{0,300})'
            match_obs = re.search(patron_obs, texto)
            if match_obs:
                bloque_obs = match_obs.group(1)
                numeros = re.findall(r'R-(\d{4,6})', bloque_obs)
                for num in numeros:
                    remisiones.add(int(num))
                if remisiones:
                    contexto = f"Observaciones: {bloque_obs[:100]}"

        return sorted(list(remisiones)), contexto

    def _buscar_uuid(self, texto: str) -> Optional[str]:
        """Busca el UUID de la factura SAT en el texto"""
        # Buscar cerca de "Folio Fiscal" primero
        patron_folio = r'Folio\s*Fiscal[^:]*:\s*(' + self.PATRON_UUID + r')'
        match = re.search(patron_folio, texto, re.IGNORECASE)
        if match:
            return match.group(1).upper()

        # Buscar cualquier UUID
        match = re.search(self.PATRON_UUID, texto)
        if match:
            return match.group(0).upper()

        return None

    def _buscar_total(self, texto: str) -> Optional[float]:
        """Busca el total de la factura en el texto"""
        match = re.search(self.PATRON_TOTAL, texto, re.IGNORECASE)
        if match:
            total_str = match.group(1).replace(',', '')
            try:
                return float(total_str)
            except ValueError:
                pass
        return None

    def _buscar_orden_compra(self, texto: str) -> Optional[str]:
        """Busca el número de Orden de Compra en el texto del PDF"""
        # Patrón para "Orden de Compra:" seguido de número
        patrones = [
            r'[Oo]rden\s+de\s+[Cc]ompra[:\s]+(\d+)',
            r'[Oo]rd(?:en)?\.?\s*[Cc]ompra[:\s]+(\d+)',
            r'O\.?\s*C\.?[:\s]+(\d+)',
            r'OC[:\s]+(\d+)',
        ]

        for patron in patrones:
            match = re.search(patron, texto)
            if match:
                return match.group(1)

        return None


class PDFIndexer:
    """
    Indexa PDFs por UUID para vinculación automática con XMLs.

    Escanea todos los PDFs en una carpeta, extrae el UUID de cada uno,
    y permite buscar el PDF correspondiente a un UUID específico.
    """

    def __init__(self):
        self._indice: Dict[str, Path] = {}  # UUID -> Path del PDF
        self._escaneado = False
        self._carpeta_actual: Optional[Path] = None

    def escanear_carpeta(self, carpeta: Path) -> int:
        """
        Escanea todos los PDFs en la carpeta y extrae sus UUIDs.

        Args:
            carpeta: Carpeta a escanear

        Returns:
            Número de PDFs indexados con UUID válido
        """
        if not PDF_SUPPORT:
            logger.warning("Soporte PDF no disponible, no se indexarán PDFs")
            return 0

        self._indice.clear()
        self._carpeta_actual = carpeta

        pdfs = list(carpeta.glob("*.pdf")) + list(carpeta.glob("*.PDF"))

        if not pdfs:
            logger.debug(f"No se encontraron PDFs en {carpeta}")
            return 0

        logger.info(f"Escaneando {len(pdfs)} PDFs para extraer UUIDs...")
        extractor = PDFRemisionExtractor()

        for pdf_path in pdfs:
            try:
                resultado = extractor.extraer_remisiones(pdf_path)
                if resultado.uuid_encontrado:
                    uuid_normalizado = resultado.uuid_encontrado.upper()
                    self._indice[uuid_normalizado] = pdf_path
                    logger.debug(f"PDF indexado: {pdf_path.name} -> UUID {uuid_normalizado[:8]}...")
            except Exception as e:
                logger.warning(f"Error procesando PDF {pdf_path.name}: {e}")

        self._escaneado = True
        logger.info(f"PDFs indexados: {len(self._indice)} de {len(pdfs)} tienen UUID válido")
        return len(self._indice)

    def buscar_por_uuid(self, uuid: str) -> Optional[Path]:
        """
        Busca el PDF que corresponde a un UUID.

        Args:
            uuid: UUID de la factura (del XML)

        Returns:
            Path al PDF si se encuentra, None si no
        """
        if not self._escaneado:
            logger.warning("El índice de PDFs no ha sido escaneado")
            return None

        uuid_normalizado = uuid.upper()
        return self._indice.get(uuid_normalizado)

    def get_estadisticas(self) -> dict:
        """Retorna estadísticas del índice"""
        return {
            "pdfs_indexados": len(self._indice),
            "carpeta": str(self._carpeta_actual) if self._carpeta_actual else None,
            "escaneado": self._escaneado
        }


# Instancia global del indexador
_pdf_indexer: Optional[PDFIndexer] = None


def obtener_indexador() -> PDFIndexer:
    """Obtiene la instancia global del indexador de PDFs"""
    global _pdf_indexer
    if _pdf_indexer is None:
        _pdf_indexer = PDFIndexer()
    return _pdf_indexer


def buscar_pdf_para_xml(xml_path: Path, uuid_factura: Optional[str] = None) -> Optional[Path]:
    """
    Busca un PDF correspondiente a un archivo XML.

    Estrategias de búsqueda (en orden de prioridad):
    1. Por UUID: Si se proporciona UUID, busca en el índice de PDFs
    2. Mismo nombre con extensión .pdf
    3. PDF con nombre similar

    Args:
        xml_path: Ruta al archivo XML
        uuid_factura: UUID de la factura (opcional, mejora la búsqueda)

    Returns:
        Ruta al PDF si se encuentra, None si no
    """
    carpeta = xml_path.parent
    nombre_base = xml_path.stem

    # Estrategia 1: Buscar por UUID en el índice
    if uuid_factura:
        indexador = obtener_indexador()

        # Si no ha sido escaneado o es otra carpeta, escanear
        if not indexador._escaneado or indexador._carpeta_actual != carpeta:
            indexador.escanear_carpeta(carpeta)

        pdf_por_uuid = indexador.buscar_por_uuid(uuid_factura)
        if pdf_por_uuid:
            logger.debug(f"PDF encontrado por UUID: {pdf_por_uuid.name}")
            return pdf_por_uuid

    # Estrategia 2: Mismo nombre
    pdf_mismo_nombre = carpeta / f"{nombre_base}.pdf"
    if pdf_mismo_nombre.exists():
        logger.debug(f"PDF encontrado por nombre: {pdf_mismo_nombre.name}")
        return pdf_mismo_nombre

    # Estrategia 3: Buscar PDF con nombre similar (ignorando case)
    for pdf in carpeta.glob("*.pdf"):
        if pdf.stem.lower() == nombre_base.lower():
            logger.debug(f"PDF encontrado (case insensitive): {pdf.name}")
            return pdf

    # Estrategia 4: Buscar PDF con nombre similar (contenido parcial)
    for pdf in carpeta.glob("*.pdf"):
        if nombre_base.lower() in pdf.stem.lower() or pdf.stem.lower() in nombre_base.lower():
            logger.debug(f"PDF encontrado por coincidencia parcial: {pdf.name}")
            return pdf

    return None


# Función de conveniencia para uso directo
def extraer_remisiones_de_pdf(pdf_path: Path) -> List[int]:
    """
    Función simple para extraer remisiones de un PDF.

    Args:
        pdf_path: Ruta al PDF

    Returns:
        Lista de números de remisión encontrados
    """
    if not PDF_SUPPORT:
        logger.warning("Soporte PDF no disponible")
        return []

    extractor = PDFRemisionExtractor()
    resultado = extractor.extraer_remisiones(pdf_path)
    return resultado.remisiones_encontradas
