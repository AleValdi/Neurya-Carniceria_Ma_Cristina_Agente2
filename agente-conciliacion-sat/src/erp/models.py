"""
Modelos de datos para remisiones del ERP SAV7
"""
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import List, Optional
from enum import Enum


class EstatusRemision(Enum):
    """Estados posibles de una remisión"""
    PENDIENTE = "PENDIENTE"
    RECIBIDA = "RECIBIDA"
    PARCIAL = "PARCIAL"
    FACTURADA = "FACTURADA"
    CANCELADA = "CANCELADA"


@dataclass
class DetalleRemision:
    """Representa un producto/línea en la remisión"""

    id_detalle: str
    id_remision: str
    descripcion_producto: str
    cantidad: Decimal
    unidad: str
    precio_unitario: Decimal
    importe: Decimal

    # Campos opcionales según estructura de SAV7
    codigo_producto: Optional[str] = None
    numero_lote: Optional[str] = None
    fecha_caducidad: Optional[datetime] = None

    @property
    def importe_calculado(self) -> Decimal:
        """Calcular importe basado en cantidad y precio"""
        return self.cantidad * self.precio_unitario

    def to_dict(self) -> dict:
        """Convertir a diccionario"""
        return {
            'id_detalle': self.id_detalle,
            'id_remision': self.id_remision,
            'descripcion_producto': self.descripcion_producto,
            'codigo_producto': self.codigo_producto,
            'cantidad': float(self.cantidad),
            'unidad': self.unidad,
            'precio_unitario': float(self.precio_unitario),
            'importe': float(self.importe),
        }


@dataclass
class Remision:
    """Representa una remisión/recepción del ERP SAV7"""

    id_remision: str  # Serie-NumRec combinado
    numero_remision: str  # NumRec
    fecha_remision: datetime

    # Proveedor
    id_proveedor: str
    rfc_proveedor: str
    nombre_proveedor: str

    # Montos
    subtotal: Decimal
    iva: Decimal
    total: Decimal

    # Estado
    estatus: EstatusRemision = EstatusRemision.PENDIENTE

    # Detalles
    detalles: List[DetalleRemision] = field(default_factory=list)

    # Campos SAV7 específicos
    serie: Optional[str] = None  # Serie de la recepción
    factura_proveedor: Optional[str] = None  # Número de factura del proveedor
    uuid_factura: Optional[str] = None  # TimbradoFolioFiscal - UUID del CFDI

    # Campos opcionales
    observaciones: Optional[str] = None
    numero_orden_compra: Optional[str] = None
    fecha_registro: Optional[datetime] = None
    usuario_registro: Optional[str] = None

    # Campos de conciliación
    uuid_factura_vinculada: Optional[str] = None
    fecha_conciliacion: Optional[datetime] = None

    # Campos adicionales para consolidación (tomados de remisión base)
    comprador: Optional[str] = None  # Usuario que capturó la remisión
    plazo: int = 0  # Días de crédito
    sucursal: int = 5  # Sucursal de la remisión

    # Campos del proveedor
    ciudad_proveedor: Optional[str] = None
    estado_proveedor: Optional[str] = None
    tipo_proveedor: Optional[str] = None
    serie_rfc: Optional[str] = None  # SerieRFC del proveedor

    @property
    def total_productos(self) -> int:
        """Número de líneas de detalle"""
        return len(self.detalles)

    @property
    def suma_cantidades(self) -> Decimal:
        """Suma total de cantidades"""
        return sum(d.cantidad for d in self.detalles)

    @property
    def esta_facturada(self) -> bool:
        """Verificar si ya está vinculada a una factura"""
        return (
            self.uuid_factura_vinculada is not None
            or self.uuid_factura is not None and self.uuid_factura != ''
            or self.estatus == EstatusRemision.FACTURADA
        )

    def to_dict(self) -> dict:
        """Convertir a diccionario para reportes"""
        return {
            'id_remision': self.id_remision,
            'serie': self.serie,
            'numero_remision': self.numero_remision,
            'fecha_remision': self.fecha_remision.isoformat() if self.fecha_remision else None,
            'id_proveedor': self.id_proveedor,
            'rfc_proveedor': self.rfc_proveedor,
            'nombre_proveedor': self.nombre_proveedor,
            'subtotal': float(self.subtotal),
            'iva': float(self.iva),
            'total': float(self.total),
            'estatus': self.estatus.value if self.estatus else None,
            'total_productos': self.total_productos,
            'factura_proveedor': self.factura_proveedor,
            'uuid_factura': self.uuid_factura,
            'uuid_factura_vinculada': self.uuid_factura_vinculada,
            'detalles': [d.to_dict() for d in self.detalles],
        }

    def __str__(self) -> str:
        return f"Remisión {self.numero_remision} - {self.nombre_proveedor} - ${self.total:,.2f}"


@dataclass
class ResultadoConciliacion:
    """Resultado de la conciliación entre factura y remisión(es)"""

    # Factura
    uuid_factura: str
    identificador_factura: str
    rfc_emisor: str
    nombre_emisor: str
    fecha_factura: datetime
    total_factura: Decimal

    # Remisión vinculada (puede ser None si no se encontró) - para compatibilidad
    remision: Optional[Remision] = None
    numero_remision: Optional[str] = None
    fecha_remision: Optional[datetime] = None
    total_remision: Optional[Decimal] = None

    # Múltiples remisiones (para caso 1 factura = N remisiones)
    remisiones: List[Remision] = field(default_factory=list)
    es_multi_remision: bool = False

    # Resultado del matching
    conciliacion_exitosa: bool = False
    score_matching: float = 0.0

    # Diferencias encontradas
    diferencia_monto: Optional[Decimal] = None
    diferencia_porcentaje: Optional[float] = None
    diferencia_cantidad: Optional[Decimal] = None

    # Alertas
    alertas: List[str] = field(default_factory=list)

    # Resultado de consolidación (se llena después del paso de consolidación)
    numero_factura_erp: Optional[str] = None

    # Metadatos
    fecha_procesamiento: datetime = field(default_factory=datetime.now)
    metodo_matching: str = "automatico"

    @property
    def tiene_alertas(self) -> bool:
        """Verificar si hay alertas"""
        return len(self.alertas) > 0

    @property
    def total_remisiones_combinado(self) -> Decimal:
        """Suma del total de todas las remisiones vinculadas"""
        if self.remisiones:
            return sum(r.total for r in self.remisiones)
        elif self.total_remision:
            return self.total_remision
        return Decimal('0')

    @property
    def numeros_remisiones(self) -> str:
        """Lista de números de remisiones separados por coma"""
        if self.remisiones:
            return ', '.join(r.id_remision for r in self.remisiones)
        elif self.numero_remision:
            return self.numero_remision
        return ''

    @property
    def cantidad_remisiones(self) -> int:
        """Cantidad de remisiones vinculadas"""
        return len(self.remisiones) if self.remisiones else (1 if self.remision else 0)

    @property
    def resumen_estatus(self) -> str:
        """Resumen del estatus de conciliación"""
        if self.conciliacion_exitosa:
            if self.es_multi_remision:
                return "CONCILIADO_MULTI"
            return "CONCILIADO"
        elif self.remision or self.remisiones:
            return "CON_DIFERENCIAS"
        else:
            return "SIN_REMISION"

    def to_dict(self) -> dict:
        """Convertir a diccionario para reportes"""
        return {
            'uuid_factura': self.uuid_factura,
            'identificador_factura': self.identificador_factura,
            'rfc_emisor': self.rfc_emisor,
            'nombre_emisor': self.nombre_emisor,
            'fecha_factura': self.fecha_factura.isoformat() if self.fecha_factura else None,
            'total_factura': float(self.total_factura),
            'numero_remision': self.numeros_remisiones,
            'cantidad_remisiones': self.cantidad_remisiones,
            'es_multi_remision': self.es_multi_remision,
            'fecha_remision': self.fecha_remision.isoformat() if self.fecha_remision else None,
            'total_remision': float(self.total_remisiones_combinado) if self.total_remisiones_combinado else None,
            'conciliacion_exitosa': self.conciliacion_exitosa,
            'score_matching': self.score_matching,
            'diferencia_monto': float(self.diferencia_monto) if self.diferencia_monto else None,
            'diferencia_porcentaje': self.diferencia_porcentaje,
            'estatus': self.resumen_estatus,
            'alertas': self.alertas,
            'fecha_procesamiento': self.fecha_procesamiento.isoformat(),
        }
