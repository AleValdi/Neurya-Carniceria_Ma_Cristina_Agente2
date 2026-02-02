"""
Módulo para sincronización con Google Drive.

Permite descargar automáticamente XMLs y PDFs de facturas desde una carpeta de Drive.
"""

from .sync import (
    DriveSync,
    DriveSyncResult,
    configurar_drive,
    sincronizar_archivos,
    verificar_credenciales,
    DRIVE_SUPPORT
)

__all__ = [
    'DriveSync',
    'DriveSyncResult',
    'configurar_drive',
    'sincronizar_archivos',
    'verificar_credenciales',
    'DRIVE_SUPPORT'
]
