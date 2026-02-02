"""
Sistema de gestión de alertas para la conciliación
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Dict
from enum import Enum
from loguru import logger


class TipoAlerta(Enum):
    """Tipos de alerta por severidad"""
    CRITICA = "CRITICA"      # Requiere acción inmediata
    ALTA = "ALTA"            # Requiere revisión urgente
    MEDIA = "MEDIA"          # Requiere atención
    BAJA = "BAJA"            # Informativa
    INFO = "INFO"            # Solo información


class CategoriaAlerta(Enum):
    """Categorías de alertas"""
    SIN_REMISION = "SIN_REMISION"
    DIFERENCIA_MONTO = "DIFERENCIA_MONTO"
    DIFERENCIA_CANTIDAD = "DIFERENCIA_CANTIDAD"
    FECHA_DESFASADA = "FECHA_DESFASADA"
    REMISION_DUPLICADA = "REMISION_DUPLICADA"
    REMISION_FALTANTE = "REMISION_FALTANTE"
    MATCH_BAJO = "MATCH_BAJO"
    OTRO = "OTRO"


@dataclass
class Alerta:
    """Representa una alerta individual"""

    tipo: TipoAlerta
    categoria: CategoriaAlerta
    mensaje: str
    uuid_factura: Optional[str] = None
    numero_remision: Optional[str] = None
    valor_esperado: Optional[str] = None
    valor_encontrado: Optional[str] = None
    fecha_generacion: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict:
        """Convertir a diccionario"""
        return {
            'tipo': self.tipo.value,
            'categoria': self.categoria.value,
            'mensaje': self.mensaje,
            'uuid_factura': self.uuid_factura,
            'numero_remision': self.numero_remision,
            'valor_esperado': self.valor_esperado,
            'valor_encontrado': self.valor_encontrado,
            'fecha_generacion': self.fecha_generacion.isoformat(),
        }

    def __str__(self) -> str:
        return f"[{self.tipo.value}] {self.categoria.value}: {self.mensaje}"


class AlertManager:
    """Gestor centralizado de alertas"""

    def __init__(self):
        self.alertas: List[Alerta] = []
        self._contadores: Dict[TipoAlerta, int] = {t: 0 for t in TipoAlerta}

    def agregar(
        self,
        tipo: TipoAlerta,
        categoria: CategoriaAlerta,
        mensaje: str,
        uuid_factura: Optional[str] = None,
        numero_remision: Optional[str] = None,
        **kwargs
    ) -> Alerta:
        """
        Agregar una nueva alerta

        Args:
            tipo: Severidad de la alerta
            categoria: Categoría de la alerta
            mensaje: Mensaje descriptivo
            uuid_factura: UUID de la factura relacionada
            numero_remision: Número de remisión relacionada
            **kwargs: Campos adicionales (valor_esperado, valor_encontrado)

        Returns:
            Alerta creada
        """
        alerta = Alerta(
            tipo=tipo,
            categoria=categoria,
            mensaje=mensaje,
            uuid_factura=uuid_factura,
            numero_remision=numero_remision,
            valor_esperado=kwargs.get('valor_esperado'),
            valor_encontrado=kwargs.get('valor_encontrado'),
        )

        self.alertas.append(alerta)
        self._contadores[tipo] += 1

        # Log según severidad
        if tipo == TipoAlerta.CRITICA:
            logger.error(str(alerta))
        elif tipo == TipoAlerta.ALTA:
            logger.warning(str(alerta))
        elif tipo == TipoAlerta.MEDIA:
            logger.warning(str(alerta))
        else:
            logger.info(str(alerta))

        return alerta

    def agregar_sin_remision(
        self,
        uuid_factura: str,
        rfc_emisor: str,
        total_factura: float
    ):
        """Agregar alerta de factura sin remisión"""
        self.agregar(
            tipo=TipoAlerta.CRITICA,
            categoria=CategoriaAlerta.SIN_REMISION,
            mensaje=f"Factura de {rfc_emisor} por ${total_factura:,.2f} sin remisión asociada",
            uuid_factura=uuid_factura,
        )

    def agregar_diferencia_monto(
        self,
        uuid_factura: str,
        numero_remision: str,
        monto_factura: float,
        monto_remision: float,
        diferencia_porcentaje: float
    ):
        """Agregar alerta de diferencia de monto"""
        diferencia = abs(monto_factura - monto_remision)

        # Determinar severidad según porcentaje
        if diferencia_porcentaje > 10:
            tipo = TipoAlerta.CRITICA
        elif diferencia_porcentaje > 5:
            tipo = TipoAlerta.ALTA
        else:
            tipo = TipoAlerta.MEDIA

        self.agregar(
            tipo=tipo,
            categoria=CategoriaAlerta.DIFERENCIA_MONTO,
            mensaje=f"Diferencia de ${diferencia:,.2f} ({diferencia_porcentaje:.2f}%)",
            uuid_factura=uuid_factura,
            numero_remision=numero_remision,
            valor_esperado=f"${monto_factura:,.2f}",
            valor_encontrado=f"${monto_remision:,.2f}",
        )

    def agregar_fecha_desfasada(
        self,
        uuid_factura: str,
        numero_remision: str,
        fecha_factura: datetime,
        fecha_remision: datetime,
        dias_diferencia: int
    ):
        """Agregar alerta de desfase de fechas"""
        if dias_diferencia > 30:
            tipo = TipoAlerta.ALTA
        else:
            tipo = TipoAlerta.MEDIA

        self.agregar(
            tipo=tipo,
            categoria=CategoriaAlerta.FECHA_DESFASADA,
            mensaje=f"Desfase de {dias_diferencia} días entre factura y remisión",
            uuid_factura=uuid_factura,
            numero_remision=numero_remision,
            valor_esperado=fecha_factura.strftime('%Y-%m-%d'),
            valor_encontrado=fecha_remision.strftime('%Y-%m-%d'),
        )

    def agregar_remision_duplicada(
        self,
        numero_remision: str,
        uuid_facturas: List[str]
    ):
        """Agregar alerta de remisión duplicada"""
        self.agregar(
            tipo=TipoAlerta.ALTA,
            categoria=CategoriaAlerta.REMISION_DUPLICADA,
            mensaje=f"Remisión vinculada a múltiples facturas: {', '.join(uuid_facturas[:3])}",
            numero_remision=numero_remision,
        )

    def limpiar(self):
        """Limpiar todas las alertas"""
        self.alertas.clear()
        self._contadores = {t: 0 for t in TipoAlerta}

    def get_por_tipo(self, tipo: TipoAlerta) -> List[Alerta]:
        """Obtener alertas por tipo de severidad"""
        return [a for a in self.alertas if a.tipo == tipo]

    def get_por_categoria(self, categoria: CategoriaAlerta) -> List[Alerta]:
        """Obtener alertas por categoría"""
        return [a for a in self.alertas if a.categoria == categoria]

    def get_por_factura(self, uuid_factura: str) -> List[Alerta]:
        """Obtener alertas de una factura específica"""
        return [a for a in self.alertas if a.uuid_factura == uuid_factura]

    def get_resumen(self) -> dict:
        """Obtener resumen de alertas"""
        return {
            'total': len(self.alertas),
            'por_tipo': {t.value: c for t, c in self._contadores.items() if c > 0},
            'por_categoria': {
                cat.value: len(self.get_por_categoria(cat))
                for cat in CategoriaAlerta
                if len(self.get_por_categoria(cat)) > 0
            },
            'criticas': self._contadores[TipoAlerta.CRITICA],
            'altas': self._contadores[TipoAlerta.ALTA],
        }

    def hay_alertas_criticas(self) -> bool:
        """Verificar si hay alertas críticas"""
        return self._contadores[TipoAlerta.CRITICA] > 0

    def to_list(self) -> List[dict]:
        """Convertir todas las alertas a lista de diccionarios"""
        return [a.to_dict() for a in self.alertas]

    def __len__(self) -> int:
        return len(self.alertas)

    def __iter__(self):
        return iter(self.alertas)
