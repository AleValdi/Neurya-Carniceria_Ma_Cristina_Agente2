"""
Repositorio de remisiones para SAV7
Maneja las consultas específicas de remisiones
"""
from typing import Optional, List
from datetime import datetime, timedelta
from decimal import Decimal
from loguru import logger

from config.settings import settings, sav7_config
from .sav7_connector import SAV7Connector
from .models import Remision, DetalleRemision, EstatusRemision


class RemisionesRepository:
    """Repositorio para operaciones con remisiones"""

    def __init__(self, connector: Optional[SAV7Connector] = None):
        self.connector = connector or SAV7Connector()
        self.config = sav7_config

    def buscar_por_rfc_proveedor(
        self,
        rfc_proveedor: str,
        fecha_inicio: Optional[datetime] = None,
        fecha_fin: Optional[datetime] = None
    ) -> List[Remision]:
        """
        Buscar remisiones por RFC del proveedor y rango de fechas

        Args:
            rfc_proveedor: RFC del proveedor
            fecha_inicio: Fecha inicial del rango
            fecha_fin: Fecha final del rango

        Returns:
            Lista de remisiones encontradas
        """
        # Query para SAV7 - SAVRecC y SAVProveedor
        query = f"""
            SELECT
                r.{self.config.campo_serie_remision} as serie,
                r.{self.config.campo_numero_remision} as numero_remision,
                r.{self.config.campo_fecha_remision} as fecha_remision,
                r.{self.config.campo_id_proveedor} as id_proveedor,
                COALESCE(p.{self.config.campo_proveedor_rfc}, r.{self.config.campo_rfc_proveedor}) as rfc_proveedor,
                COALESCE(r.{self.config.campo_nombre_proveedor}, p.{self.config.campo_proveedor_nombre}, '') as nombre_proveedor,
                r.{self.config.campo_subtotal_remision} as subtotal,
                r.{self.config.campo_iva_remision} as iva,
                r.{self.config.campo_total_remision} as total,
                r.{self.config.campo_estatus} as estatus,
                r.{self.config.campo_factura} as factura_proveedor,
                r.{self.config.campo_uuid} as uuid_factura,
                r.Comprador as comprador,
                r.Plazo as plazo,
                r.Sucursal as sucursal,
                p.Ciudad as ciudad_proveedor,
                p.Estado as estado_proveedor,
                p.Tipo as tipo_proveedor
            FROM {self.config.tabla_remisiones} r
            LEFT JOIN {self.config.tabla_proveedores} p
                ON r.{self.config.campo_id_proveedor} = p.{self.config.campo_proveedor_id}
            WHERE r.{self.config.campo_serie_remision} = 'R'
            AND r.{self.config.campo_estatus} != 'Consolidada'
            AND (
                p.{self.config.campo_proveedor_rfc} = ?
                OR r.{self.config.campo_rfc_proveedor} = ?
            )
        """
        params = [rfc_proveedor, rfc_proveedor]

        if fecha_inicio:
            query += f" AND r.{self.config.campo_fecha_remision} >= ?"
            params.append(fecha_inicio)

        if fecha_fin:
            query += f" AND r.{self.config.campo_fecha_remision} <= ?"
            params.append(fecha_fin)

        query += f" ORDER BY r.{self.config.campo_fecha_remision} DESC"

        try:
            results = self.connector.execute_custom_query(query, tuple(params))
            return [self._map_to_remision(row) for row in results]
        except Exception as e:
            logger.error(f"Error al buscar remisiones por RFC {rfc_proveedor}: {e}")
            return []

    def buscar_para_conciliacion(
        self,
        rfc_proveedor: str,
        fecha_factura: datetime,
        monto_total: Decimal,
        dias_rango: Optional[int] = None
    ) -> List[Remision]:
        """
        Buscar remisiones candidatas para conciliar con una factura

        Args:
            rfc_proveedor: RFC del proveedor de la factura
            fecha_factura: Fecha de la factura
            monto_total: Monto total de la factura
            dias_rango: Días de tolerancia para la búsqueda (default: configuración)

        Returns:
            Lista de remisiones candidatas ordenadas por relevancia
        """
        dias = dias_rango or settings.dias_rango_busqueda
        fecha_inicio = fecha_factura - timedelta(days=dias)
        fecha_fin = fecha_factura + timedelta(days=dias)

        # Query con criterios de conciliación para SAV7
        query = f"""
            SELECT
                r.{self.config.campo_serie_remision} as serie,
                r.{self.config.campo_numero_remision} as numero_remision,
                r.{self.config.campo_fecha_remision} as fecha_remision,
                r.{self.config.campo_id_proveedor} as id_proveedor,
                COALESCE(p.{self.config.campo_proveedor_rfc}, r.{self.config.campo_rfc_proveedor}) as rfc_proveedor,
                COALESCE(r.{self.config.campo_nombre_proveedor}, p.{self.config.campo_proveedor_nombre}, '') as nombre_proveedor,
                r.{self.config.campo_subtotal_remision} as subtotal,
                r.{self.config.campo_iva_remision} as iva,
                r.{self.config.campo_total_remision} as total,
                r.{self.config.campo_estatus} as estatus,
                r.{self.config.campo_factura} as factura_proveedor,
                r.{self.config.campo_uuid} as uuid_factura,
                r.Comprador as comprador,
                r.Plazo as plazo,
                r.Sucursal as sucursal,
                p.Ciudad as ciudad_proveedor,
                p.Estado as estado_proveedor,
                p.Tipo as tipo_proveedor,
                ABS(DATEDIFF(day, r.{self.config.campo_fecha_remision}, ?)) as dias_diferencia,
                ABS(r.{self.config.campo_total_remision} - ?) as diferencia_monto
            FROM {self.config.tabla_remisiones} r
            LEFT JOIN {self.config.tabla_proveedores} p
                ON r.{self.config.campo_id_proveedor} = p.{self.config.campo_proveedor_id}
            WHERE r.{self.config.campo_serie_remision} = 'R'
            AND r.{self.config.campo_estatus} != 'Consolidada'
            AND (
                p.{self.config.campo_proveedor_rfc} = ?
                OR r.{self.config.campo_rfc_proveedor} = ?
            )
            AND r.{self.config.campo_fecha_remision} BETWEEN ? AND ?
            ORDER BY diferencia_monto ASC, dias_diferencia ASC
        """

        params = (
            fecha_factura, monto_total,
            rfc_proveedor, rfc_proveedor,
            fecha_inicio, fecha_fin
        )

        try:
            results = self.connector.execute_custom_query(query, params)
            remisiones = [self._map_to_remision(row) for row in results]

            # Cargar detalles para las remisiones candidatas
            for remision in remisiones:
                remision.detalles = self.obtener_detalles(remision.serie, remision.numero_remision)

            return remisiones
        except Exception as e:
            logger.error(f"Error al buscar remisiones para conciliación: {e}")
            # Fallback: buscar solo por RFC y fecha
            return self.buscar_por_rfc_proveedor(rfc_proveedor, fecha_inicio, fecha_fin)

    def buscar_por_numero(
        self,
        numero_remision: str,
        rfc_proveedor: Optional[str] = None
    ) -> List[Remision]:
        """
        Buscar remisiones por número (parcial o completo).
        Útil cuando la factura indica el número de remisión.

        Args:
            numero_remision: Número de remisión a buscar
            rfc_proveedor: RFC opcional para filtrar por proveedor

        Returns:
            Lista de remisiones que coinciden
        """
        # Puede venir como "A-12345" (con serie) o solo "12345"
        if '-' in numero_remision:
            partes = numero_remision.split('-', 1)
            serie_buscar = partes[0]
            num_buscar = partes[1]
        else:
            serie_buscar = None
            num_buscar = numero_remision

        query = f"""
            SELECT
                r.{self.config.campo_serie_remision} as serie,
                r.{self.config.campo_numero_remision} as numero_remision,
                r.{self.config.campo_fecha_remision} as fecha_remision,
                r.{self.config.campo_id_proveedor} as id_proveedor,
                COALESCE(p.{self.config.campo_proveedor_rfc}, r.{self.config.campo_rfc_proveedor}) as rfc_proveedor,
                COALESCE(r.{self.config.campo_nombre_proveedor}, p.{self.config.campo_proveedor_nombre}, '') as nombre_proveedor,
                r.{self.config.campo_subtotal_remision} as subtotal,
                r.{self.config.campo_iva_remision} as iva,
                r.{self.config.campo_total_remision} as total,
                r.{self.config.campo_estatus} as estatus,
                r.{self.config.campo_factura} as factura_proveedor,
                r.{self.config.campo_uuid} as uuid_factura,
                r.Comprador as comprador,
                r.Plazo as plazo,
                r.Sucursal as sucursal,
                p.Ciudad as ciudad_proveedor,
                p.Estado as estado_proveedor,
                p.Tipo as tipo_proveedor
            FROM {self.config.tabla_remisiones} r
            LEFT JOIN {self.config.tabla_proveedores} p
                ON r.{self.config.campo_id_proveedor} = p.{self.config.campo_proveedor_id}
            WHERE r.{self.config.campo_serie_remision} = 'R'
            AND r.{self.config.campo_estatus} != 'Consolidada'
            AND CAST(r.{self.config.campo_numero_remision} AS VARCHAR) = ?
        """
        params = [num_buscar]

        # Nota: serie_buscar ya no se usa porque solo buscamos en Serie R
        # Se mantiene el parámetro por compatibilidad pero se ignora

        if rfc_proveedor:
            query += f" AND (p.{self.config.campo_proveedor_rfc} = ? OR r.{self.config.campo_rfc_proveedor} = ?)"
            params.extend([rfc_proveedor, rfc_proveedor])

        try:
            results = self.connector.execute_custom_query(query, tuple(params))
            remisiones = [self._map_to_remision(row) for row in results]

            # Cargar detalles para las remisiones encontradas
            for remision in remisiones:
                remision.detalles = self.obtener_detalles(remision.serie, remision.numero_remision)

            logger.info(f"Búsqueda por número {numero_remision}: {len(remisiones)} remisiones encontradas")
            return remisiones
        except Exception as e:
            logger.error(f"Error al buscar remisión por número {numero_remision}: {e}")
            return []

    def buscar_por_orden_compra(
        self,
        orden_compra: str,
        rfc_proveedor: Optional[str] = None
    ) -> List[Remision]:
        """
        Buscar remisiones por número de Orden de Compra (campo Factura).
        Útil cuando el PDF indica la OC del proveedor en lugar de números R-XXXXX.

        Args:
            orden_compra: Número de orden de compra a buscar
            rfc_proveedor: RFC opcional para filtrar por proveedor

        Returns:
            Lista de remisiones que coinciden
        """
        query = f"""
            SELECT
                r.{self.config.campo_serie_remision} as serie,
                r.{self.config.campo_numero_remision} as numero_remision,
                r.{self.config.campo_fecha_remision} as fecha_remision,
                r.{self.config.campo_id_proveedor} as id_proveedor,
                COALESCE(p.{self.config.campo_proveedor_rfc}, r.{self.config.campo_rfc_proveedor}) as rfc_proveedor,
                COALESCE(r.{self.config.campo_nombre_proveedor}, p.{self.config.campo_proveedor_nombre}, '') as nombre_proveedor,
                r.{self.config.campo_subtotal_remision} as subtotal,
                r.{self.config.campo_iva_remision} as iva,
                r.{self.config.campo_total_remision} as total,
                r.{self.config.campo_estatus} as estatus,
                r.{self.config.campo_factura} as factura_proveedor,
                r.{self.config.campo_uuid} as uuid_factura,
                r.Comprador as comprador,
                r.Plazo as plazo,
                r.Sucursal as sucursal,
                p.Ciudad as ciudad_proveedor,
                p.Estado as estado_proveedor,
                p.Tipo as tipo_proveedor
            FROM {self.config.tabla_remisiones} r
            LEFT JOIN {self.config.tabla_proveedores} p
                ON r.{self.config.campo_id_proveedor} = p.{self.config.campo_proveedor_id}
            WHERE r.{self.config.campo_serie_remision} = 'R'
            AND r.{self.config.campo_estatus} != 'Consolidada'
            AND r.{self.config.campo_factura} = ?
        """
        params = [orden_compra]

        if rfc_proveedor:
            query += f" AND (p.{self.config.campo_proveedor_rfc} = ? OR r.{self.config.campo_rfc_proveedor} = ?)"
            params.extend([rfc_proveedor, rfc_proveedor])

        try:
            results = self.connector.execute_custom_query(query, tuple(params))
            remisiones = [self._map_to_remision(row) for row in results]

            # Cargar detalles para las remisiones encontradas
            for remision in remisiones:
                remision.detalles = self.obtener_detalles(remision.serie, remision.numero_remision)

            logger.info(f"Búsqueda por Orden de Compra {orden_compra}: {len(remisiones)} remisiones encontradas")
            return remisiones
        except Exception as e:
            logger.error(f"Error al buscar remisión por Orden de Compra {orden_compra}: {e}")
            return []

    def obtener_por_id(self, serie: str, numero_remision: str) -> Optional[Remision]:
        """Obtener una remisión por su Serie y Número"""
        query = f"""
            SELECT
                r.{self.config.campo_serie_remision} as serie,
                r.{self.config.campo_numero_remision} as numero_remision,
                r.{self.config.campo_fecha_remision} as fecha_remision,
                r.{self.config.campo_id_proveedor} as id_proveedor,
                COALESCE(p.{self.config.campo_proveedor_rfc}, r.{self.config.campo_rfc_proveedor}) as rfc_proveedor,
                COALESCE(r.{self.config.campo_nombre_proveedor}, p.{self.config.campo_proveedor_nombre}, '') as nombre_proveedor,
                r.{self.config.campo_subtotal_remision} as subtotal,
                r.{self.config.campo_iva_remision} as iva,
                r.{self.config.campo_total_remision} as total,
                r.{self.config.campo_estatus} as estatus,
                r.{self.config.campo_factura} as factura_proveedor,
                r.{self.config.campo_uuid} as uuid_factura,
                r.Comprador as comprador,
                r.Plazo as plazo,
                r.Sucursal as sucursal,
                p.Ciudad as ciudad_proveedor,
                p.Estado as estado_proveedor,
                p.Tipo as tipo_proveedor
            FROM {self.config.tabla_remisiones} r
            LEFT JOIN {self.config.tabla_proveedores} p
                ON r.{self.config.campo_id_proveedor} = p.{self.config.campo_proveedor_id}
            WHERE r.{self.config.campo_serie_remision} = ?
              AND r.{self.config.campo_numero_remision} = ?
        """

        try:
            results = self.connector.execute_custom_query(query, (serie, numero_remision))
            if results:
                remision = self._map_to_remision(results[0])
                remision.detalles = self.obtener_detalles(serie, numero_remision)
                return remision
            return None
        except Exception as e:
            logger.error(f"Error al obtener remisión {serie}-{numero_remision}: {e}")
            return None

    def obtener_detalles(self, serie: str, numero_remision: str) -> List[DetalleRemision]:
        """Obtener detalles/productos de una remisión"""
        query = f"""
            SELECT
                {self.config.campo_detalle_id} as codigo_producto,
                {self.config.campo_detalle_serie} as serie,
                {self.config.campo_detalle_remision_id} as numero_remision,
                {self.config.campo_detalle_producto} as descripcion_producto,
                {self.config.campo_detalle_cantidad} as cantidad,
                {self.config.campo_detalle_unidad} as unidad,
                {self.config.campo_detalle_precio_unitario} as precio_unitario,
                {self.config.campo_detalle_importe} as importe
            FROM {self.config.tabla_detalle_remisiones}
            WHERE {self.config.campo_detalle_serie} = ?
              AND {self.config.campo_detalle_remision_id} = ?
        """

        try:
            results = self.connector.execute_custom_query(query, (serie, numero_remision))
            return [self._map_to_detalle(row) for row in results]
        except Exception as e:
            logger.error(f"Error al obtener detalles de remisión {serie}-{numero_remision}: {e}")
            return []

    def buscar_remisiones_no_facturadas(
        self,
        fecha_desde: Optional[datetime] = None
    ) -> List[Remision]:
        """
        Buscar remisiones que no tienen factura asociada

        Args:
            fecha_desde: Fecha desde la cual buscar

        Returns:
            Lista de remisiones sin factura
        """
        query = f"""
            SELECT
                r.{self.config.campo_serie_remision} as serie,
                r.{self.config.campo_numero_remision} as numero_remision,
                r.{self.config.campo_fecha_remision} as fecha_remision,
                r.{self.config.campo_id_proveedor} as id_proveedor,
                COALESCE(p.{self.config.campo_proveedor_rfc}, r.{self.config.campo_rfc_proveedor}) as rfc_proveedor,
                COALESCE(r.{self.config.campo_nombre_proveedor}, p.{self.config.campo_proveedor_nombre}, '') as nombre_proveedor,
                r.{self.config.campo_subtotal_remision} as subtotal,
                r.{self.config.campo_iva_remision} as iva,
                r.{self.config.campo_total_remision} as total,
                r.{self.config.campo_estatus} as estatus,
                r.{self.config.campo_factura} as factura_proveedor,
                r.{self.config.campo_uuid} as uuid_factura,
                r.Comprador as comprador,
                r.Plazo as plazo,
                r.Sucursal as sucursal,
                p.Ciudad as ciudad_proveedor,
                p.Estado as estado_proveedor,
                p.Tipo as tipo_proveedor
            FROM {self.config.tabla_remisiones} r
            LEFT JOIN {self.config.tabla_proveedores} p
                ON r.{self.config.campo_id_proveedor} = p.{self.config.campo_proveedor_id}
            WHERE r.{self.config.campo_serie_remision} = 'R'
            AND r.{self.config.campo_estatus} != 'Consolidada'
            AND (r.{self.config.campo_uuid} IS NULL OR r.{self.config.campo_uuid} = '')
        """

        params = []
        if fecha_desde:
            query += f" AND r.{self.config.campo_fecha_remision} >= ?"
            params.append(fecha_desde)

        query += f" ORDER BY r.{self.config.campo_fecha_remision} DESC"

        try:
            results = self.connector.execute_custom_query(query, tuple(params))
            return [self._map_to_remision(row) for row in results]
        except Exception as e:
            logger.error(f"Error al buscar remisiones no facturadas: {e}")
            return []

    def _map_to_remision(self, row: dict) -> Remision:
        """Mapear resultado de query a objeto Remision"""
        estatus_str = str(row.get('estatus', '')) or 'PENDIENTE'
        try:
            estatus = EstatusRemision(estatus_str)
        except ValueError:
            estatus = EstatusRemision.PENDIENTE

        return Remision(
            id_remision=f"{row.get('serie', '')}-{row.get('numero_remision', '')}",
            serie=str(row.get('serie', '')),
            numero_remision=str(row.get('numero_remision', '')),
            fecha_remision=row.get('fecha_remision'),
            id_proveedor=str(row.get('id_proveedor', '')),
            rfc_proveedor=str(row.get('rfc_proveedor', '') or ''),
            nombre_proveedor=str(row.get('nombre_proveedor', '') or ''),
            subtotal=Decimal(str(row.get('subtotal', 0) or 0)),
            iva=Decimal(str(row.get('iva', 0) or 0)),
            total=Decimal(str(row.get('total', 0) or 0)),
            estatus=estatus,
            factura_proveedor=str(row.get('factura_proveedor', '') or ''),
            uuid_factura=str(row.get('uuid_factura', '') or ''),
            # Campos adicionales para consolidación
            comprador=str(row.get('comprador', '') or ''),
            plazo=int(row.get('plazo', 0) or 0),
            sucursal=int(row.get('sucursal', 5) or 5),
            ciudad_proveedor=str(row.get('ciudad_proveedor', '') or ''),
            estado_proveedor=str(row.get('estado_proveedor', '') or ''),
            tipo_proveedor=str(row.get('tipo_proveedor', '') or ''),
        )

    def _map_to_detalle(self, row: dict) -> DetalleRemision:
        """Mapear resultado de query a objeto DetalleRemision"""
        return DetalleRemision(
            id_detalle=str(row.get('codigo_producto', '')),
            id_remision=f"{row.get('serie', '')}-{row.get('numero_remision', '')}",
            descripcion_producto=str(row.get('descripcion_producto', '') or ''),
            cantidad=Decimal(str(row.get('cantidad', 0) or 0)),
            unidad=str(row.get('unidad', '') or ''),
            precio_unitario=Decimal(str(row.get('precio_unitario', 0) or 0)),
            importe=Decimal(str(row.get('importe', 0) or 0)),
        )
