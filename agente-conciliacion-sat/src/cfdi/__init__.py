"""
Módulo CFDI para generación de PDF y gestión de adjuntos
"""
from .pdf_generator import PDFGenerator
from .attachment_manager import AttachmentManager, AttachmentResult

__all__ = ['PDFGenerator', 'AttachmentManager', 'AttachmentResult']
