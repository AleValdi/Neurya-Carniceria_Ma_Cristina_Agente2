"""
Configuración general del Agente de Conciliación SAT-ERP
"""
import os
import sys
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional
from dotenv import load_dotenv


def get_base_dir() -> Path:
    """
    Obtener directorio base del proyecto.
    Detecta si está corriendo como ejecutable PyInstaller o como script Python.
    """
    if getattr(sys, 'frozen', False):
        # Corriendo como ejecutable (PyInstaller)
        # sys.executable es la ruta al .exe
        # Las carpetas data/, logs/ están al mismo nivel que el .exe
        return Path(sys.executable).parent
    else:
        # Corriendo como script Python normal
        return Path(__file__).resolve().parent.parent


# Directorio base del proyecto
BASE_DIR = get_base_dir()

# Cargar variables de entorno desde el directorio base
# .env.local sobreescribe .env (para desarrollo remoto sin modificar .env de produccion)
load_dotenv(BASE_DIR / '.env')
load_dotenv(BASE_DIR / '.env.local', override=True)


@dataclass
class Settings:
    """Configuración principal del agente"""

    # Rutas de datos
    input_dir: Path = field(default_factory=lambda: BASE_DIR / "data" / "xml_facturas")
    output_dir: Path = field(default_factory=lambda: BASE_DIR / "data" / "reportes")
    processed_dir: Path = field(default_factory=lambda: BASE_DIR / "data" / "procesados")
    alertas_dir: Path = field(default_factory=lambda: BASE_DIR / "data" / "alertas")
    logs_dir: Path = field(default_factory=lambda: BASE_DIR / "logs")

    # Configuración de conciliación
    tolerancia_monto_porcentaje: float = 2.0  # Tolerancia del 2% en diferencias de montos
    dias_rango_busqueda: int = 15  # Buscar remisiones ±15 días de la fecha de factura (proveedores tardan en facturar)
    dias_alerta_desfase: int = 7  # Alertar si fecha difiere más de 7 días

    # Configuración de matching difuso
    umbral_similitud_texto: int = 80  # Porcentaje mínimo de similitud para matching de texto

    # Configuración de reportes
    nombre_reporte: str = "conciliacion_sat_erp"
    incluir_fecha_en_reporte: bool = True

    # Configuración de logging
    log_level: str = "INFO"
    log_format: str = "{time:YYYY-MM-DD HH:mm:ss} | {level} | {message}"

    # Configuración de adjuntos CFDI
    cfdi_adjuntos_dir: Path = field(
        default_factory=lambda: Path(os.getenv(
            'CFDI_ADJUNTOS_DIR',
            r'\\SERVERMC\Asesoft\SAV7-1\Recepciones CFDI'
        ))
    )
    cfdi_adjuntos_habilitado: bool = field(
        default_factory=lambda: os.getenv('CFDI_ADJUNTOS_HABILITADO', 'true').lower() == 'true'
    )
    cfdi_generar_pdf: bool = field(
        default_factory=lambda: os.getenv('CFDI_GENERAR_PDF', 'true').lower() == 'true'
    )

    def __post_init__(self):
        """Crear directorios si no existen"""
        for dir_path in [self.input_dir, self.output_dir, self.processed_dir, self.alertas_dir, self.logs_dir]:
            dir_path.mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_env(cls) -> 'Settings':
        """Crear configuración desde variables de entorno"""
        return cls(
            tolerancia_monto_porcentaje=float(os.getenv('TOLERANCIA_MONTO', '2.0')),
            dias_rango_busqueda=int(os.getenv('DIAS_RANGO_BUSQUEDA', '3')),
            dias_alerta_desfase=int(os.getenv('DIAS_ALERTA_DESFASE', '7')),
            umbral_similitud_texto=int(os.getenv('UMBRAL_SIMILITUD', '80')),
            log_level=os.getenv('LOG_LEVEL', 'INFO'),
        )


@dataclass
class SAV7Config:
    """Configuración específica para consultas en SAV7"""

    # Nombres de tablas SAV7
    tabla_remisiones: str = "SAVRecC"  # Encabezados de recepciones
    tabla_detalle_remisiones: str = "SAVRecD"  # Detalle de productos
    tabla_proveedores: str = "SAVProveedor"  # Catálogo de proveedores

    # Campos de SAVRecC (encabezados de remisiones/recepciones)
    campo_id_remision: str = "NumRec"  # Número de recepción (junto con Serie es la PK)
    campo_serie_remision: str = "Serie"  # Serie de la recepción
    campo_numero_remision: str = "NumRec"  # Número de recepción
    campo_fecha_remision: str = "Fecha"  # Fecha de la recepción
    campo_id_proveedor: str = "Proveedor"  # Clave del proveedor
    campo_nombre_proveedor: str = "ProveedorNombre"  # Nombre del proveedor en la recepción
    campo_rfc_proveedor: str = "RFC"  # RFC en la recepción
    campo_total_remision: str = "Total"  # Total
    campo_subtotal_remision: str = "SubTotal1"  # Subtotal
    campo_iva_remision: str = "Iva"  # IVA
    campo_estatus: str = "Estatus"  # Estatus de la recepción
    campo_factura: str = "Factura"  # Número de factura del proveedor
    campo_uuid: str = "TimbradoFolioFiscal"  # UUID de la factura electrónica

    # Campos de SAVRecD (detalle de remisiones)
    campo_detalle_id: str = "Producto"  # Código de producto
    campo_detalle_serie: str = "Serie"  # Serie de la recepción
    campo_detalle_remision_id: str = "NumRec"  # Número de recepción
    campo_detalle_producto: str = "Nombre"  # Nombre/descripción del producto
    campo_detalle_cantidad: str = "Cantidad"  # Cantidad recibida
    campo_detalle_precio_unitario: str = "Costo"  # Costo unitario
    campo_detalle_importe: str = "CostoImp"  # Importe (costo con impuestos)
    campo_detalle_unidad: str = "Unidad"  # Unidad de medida

    # Campos de SAVProveedor
    campo_proveedor_id: str = "Clave"  # Clave del proveedor
    campo_proveedor_rfc: str = "RFC"  # RFC
    campo_proveedor_nombre: str = "Empresa"  # Nombre/Razón social

    @classmethod
    def from_env(cls) -> 'SAV7Config':
        """Crear configuración desde variables de entorno"""
        return cls(
            tabla_remisiones=os.getenv('SAV7_TABLA_REMISIONES', 'SAVRecC'),
            tabla_detalle_remisiones=os.getenv('SAV7_TABLA_DETALLE', 'SAVRecD'),
            tabla_proveedores=os.getenv('SAV7_TABLA_PROVEEDORES', 'SAVProveedor'),
        )


# Instancias globales de configuración
settings = Settings.from_env()
sav7_config = SAV7Config.from_env()
