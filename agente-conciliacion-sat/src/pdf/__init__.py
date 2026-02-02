"""
Módulo para procesamiento de PDFs de facturas de proveedores.
"""

from .extractor import (
    PDFRemisionExtractor,
    PDFExtractResult,
    PDFIndexer,
    buscar_pdf_para_xml,
    extraer_remisiones_de_pdf,
    obtener_indexador,
    PDF_SUPPORT
)

__all__ = [
    'PDFRemisionExtractor',
    'PDFExtractResult',
    'PDFIndexer',
    'buscar_pdf_para_xml',
    'extraer_remisiones_de_pdf',
    'obtener_indexador',
    'PDF_SUPPORT'
]
