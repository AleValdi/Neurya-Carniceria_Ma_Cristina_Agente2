"""
Validador de conciliaciones - Verifica reglas de negocio y genera alertas
"""
from typing import List, Optional
from decimal import Decimal
from datetime import datetime, timedelta
from loguru import logger

from config.settings import settings
from src.sat.models import Factura
from src.erp.models import Remision, ResultadoConciliacion


class ConciliacionValidator:
    """Validador de reglas de negocio para conciliaciones"""

    def __init__(self):
        self.tolerancia_monto = Decimal(str(settings.tolerancia_monto_porcentaje / 100))
        self.dias_alerta_desfase = settings.dias_alerta_desfase

    def validar_resultado(self, resultado: ResultadoConciliacion) -> List[str]:
        """
        Validar un resultado de conciliación y generar alertas

        Args:
            resultado: Resultado de conciliación a validar

        Returns:
            Lista de alertas generadas
        """
        alertas = []

        # 1. Validar si hay remisión asociada
        if not resultado.remision:
            alertas.append("CRITICA: Factura sin remisión asociada en el ERP")
            return alertas

        # 2. Validar diferencia de montos
        alertas.extend(self._validar_montos(resultado))

        # 3. Validar diferencia de fechas
        alertas.extend(self._validar_fechas(resultado))

        # 4. Validar cantidades (si hay detalles)
        if resultado.remision.detalles:
            alertas.extend(self._validar_cantidades(resultado))

        return alertas

    def validar_lote(
        self,
        resultados: List[ResultadoConciliacion]
    ) -> List[ResultadoConciliacion]:
        """
        Validar un lote de resultados y agregar alertas

        Args:
            resultados: Lista de resultados a validar

        Returns:
            Lista de resultados con alertas actualizadas
        """
        # Validar cada resultado individualmente
        for resultado in resultados:
            nuevas_alertas = self.validar_resultado(resultado)
            resultado.alertas.extend(nuevas_alertas)

        # Validaciones de lote
        alertas_lote = self._validar_duplicados(resultados)
        alertas_lote.extend(self._validar_remisiones_faltantes(resultados))

        # Agregar alertas de lote a resultados afectados
        for alerta in alertas_lote:
            # Las alertas de lote se agregan al primer resultado relevante
            # o se pueden manejar de forma especial en el reporte
            logger.warning(alerta)

        return resultados

    def _validar_montos(self, resultado: ResultadoConciliacion) -> List[str]:
        """Validar diferencias de montos"""
        alertas = []

        if resultado.diferencia_monto is None or resultado.total_remision is None:
            return alertas

        diferencia = resultado.diferencia_monto
        porcentaje = resultado.diferencia_porcentaje or 0

        # Clasificar por severidad
        if porcentaje > 10:
            alertas.append(
                f"CRITICA: Diferencia de monto muy alta: "
                f"${diferencia:,.2f} ({porcentaje:.2f}%)"
            )
        elif porcentaje > 5:
            alertas.append(
                f"ALTA: Diferencia de monto significativa: "
                f"${diferencia:,.2f} ({porcentaje:.2f}%)"
            )
        elif porcentaje > settings.tolerancia_monto_porcentaje:
            alertas.append(
                f"MEDIA: Diferencia de monto fuera de tolerancia: "
                f"${diferencia:,.2f} ({porcentaje:.2f}%)"
            )

        return alertas

    def _validar_fechas(self, resultado: ResultadoConciliacion) -> List[str]:
        """Validar diferencia de fechas"""
        alertas = []

        if resultado.fecha_factura is None or resultado.fecha_remision is None:
            return alertas

        dias_diferencia = abs((resultado.fecha_factura - resultado.fecha_remision).days)

        if dias_diferencia > 30:
            alertas.append(
                f"CRITICA: Desfase de fechas muy alto: {dias_diferencia} días"
            )
        elif dias_diferencia > self.dias_alerta_desfase:
            alertas.append(
                f"MEDIA: Desfase de fechas: {dias_diferencia} días entre factura y remisión"
            )

        # Validar que la remisión no sea posterior a la factura por mucho tiempo
        if resultado.fecha_remision > resultado.fecha_factura + timedelta(days=7):
            alertas.append(
                f"ALERTA: Remisión registrada {dias_diferencia} días después de la factura"
            )

        return alertas

    def _validar_cantidades(self, resultado: ResultadoConciliacion) -> List[str]:
        """Validar diferencias de cantidades entre conceptos"""
        alertas = []

        # Esta validación requiere tener los conceptos de la factura
        # Por ahora solo verificamos que existan detalles
        if resultado.remision and resultado.remision.total_productos == 0:
            alertas.append("ALERTA: Remisión sin detalles de productos")

        return alertas

    def _validar_duplicados(
        self,
        resultados: List[ResultadoConciliacion]
    ) -> List[str]:
        """Detectar remisiones duplicadas en el lote"""
        alertas = []
        remisiones_usadas = {}

        for resultado in resultados:
            if resultado.numero_remision:
                num_rem = resultado.numero_remision
                if num_rem in remisiones_usadas:
                    alerta = (
                        f"DUPLICADO: Remisión {num_rem} vinculada a múltiples facturas: "
                        f"{remisiones_usadas[num_rem]} y {resultado.uuid_factura}"
                    )
                    alertas.append(alerta)
                    resultado.alertas.append(alerta)
                else:
                    remisiones_usadas[num_rem] = resultado.uuid_factura

        return alertas

    def _validar_remisiones_faltantes(
        self,
        resultados: List[ResultadoConciliacion]
    ) -> List[str]:
        """Generar alerta si hay muchas facturas sin remisión"""
        alertas = []

        sin_remision = sum(1 for r in resultados if not r.remision)
        total = len(resultados)

        if total > 0:
            porcentaje_sin_remision = (sin_remision / total) * 100

            if porcentaje_sin_remision > 50:
                alertas.append(
                    f"CRITICA: {porcentaje_sin_remision:.0f}% de facturas sin remisión "
                    f"({sin_remision} de {total})"
                )
            elif porcentaje_sin_remision > 20:
                alertas.append(
                    f"ALTA: {porcentaje_sin_remision:.0f}% de facturas sin remisión "
                    f"({sin_remision} de {total})"
                )

        return alertas

    def generar_resumen_validacion(
        self,
        resultados: List[ResultadoConciliacion]
    ) -> dict:
        """
        Generar resumen estadístico de la validación

        Args:
            resultados: Lista de resultados validados

        Returns:
            Diccionario con estadísticas
        """
        total = len(resultados)
        conciliados = sum(1 for r in resultados if r.conciliacion_exitosa)
        con_diferencias = sum(
            1 for r in resultados
            if r.remision and not r.conciliacion_exitosa
        )
        sin_remision = sum(1 for r in resultados if not r.remision)

        # Contar alertas por tipo
        alertas_por_tipo = {}
        for resultado in resultados:
            for alerta in resultado.alertas:
                tipo = alerta.split(':')[0] if ':' in alerta else 'OTRO'
                alertas_por_tipo[tipo] = alertas_por_tipo.get(tipo, 0) + 1

        # Calcular diferencia promedio
        diferencias = [
            r.diferencia_monto for r in resultados
            if r.diferencia_monto is not None
        ]
        diferencia_promedio = sum(diferencias) / len(diferencias) if diferencias else Decimal(0)

        return {
            'total_facturas': total,
            'conciliadas_exitosamente': conciliados,
            'con_diferencias': con_diferencias,
            'sin_remision': sin_remision,
            'porcentaje_exito': (conciliados / total * 100) if total > 0 else 0,
            'diferencia_promedio': float(diferencia_promedio),
            'alertas_por_tipo': alertas_por_tipo,
            'total_alertas': sum(len(r.alertas) for r in resultados),
        }
