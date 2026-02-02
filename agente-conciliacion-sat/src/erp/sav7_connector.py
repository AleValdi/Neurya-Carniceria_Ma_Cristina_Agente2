"""
Conector para el ERP SAV7 (SQL Server)
Maneja la conexión y consultas básicas a la base de datos
"""
from typing import Optional, List, Dict, Any
from datetime import datetime
from decimal import Decimal
from loguru import logger

from config.database import DatabaseConnection, DatabaseConfig
from config.settings import sav7_config, SAV7Config


class SAV7Connector:
    """Conector principal para SAV7"""

    def __init__(
        self,
        db_config: Optional[DatabaseConfig] = None,
        sav7_cfg: Optional[SAV7Config] = None
    ):
        self.db = DatabaseConnection(db_config)
        self.config = sav7_cfg or sav7_config

    def test_connection(self) -> bool:
        """Probar conexión a la base de datos"""
        try:
            result = self.db.test_connection()
            if result:
                logger.info("Conexión a SAV7 exitosa")
            else:
                logger.error("No se pudo conectar a SAV7")
            return result
        except Exception as e:
            logger.error(f"Error al probar conexión: {e}")
            return False

    def get_tables(self) -> List[str]:
        """Obtener lista de tablas en la base de datos"""
        query = """
            SELECT TABLE_NAME
            FROM INFORMATION_SCHEMA.TABLES
            WHERE TABLE_TYPE = 'BASE TABLE'
            ORDER BY TABLE_NAME
        """
        try:
            results = self.db.execute_query(query)
            return [row['TABLE_NAME'] for row in results]
        except Exception as e:
            logger.error(f"Error al obtener tablas: {e}")
            return []

    def get_table_columns(self, table_name: str) -> List[Dict[str, Any]]:
        """Obtener columnas de una tabla específica"""
        query = """
            SELECT
                COLUMN_NAME,
                DATA_TYPE,
                IS_NULLABLE,
                CHARACTER_MAXIMUM_LENGTH,
                NUMERIC_PRECISION
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_NAME = ?
            ORDER BY ORDINAL_POSITION
        """
        try:
            return self.db.execute_query(query, (table_name,))
        except Exception as e:
            logger.error(f"Error al obtener columnas de {table_name}: {e}")
            return []

    def search_tables_by_keyword(self, keyword: str) -> List[str]:
        """Buscar tablas que contengan una palabra clave en su nombre"""
        query = """
            SELECT TABLE_NAME
            FROM INFORMATION_SCHEMA.TABLES
            WHERE TABLE_TYPE = 'BASE TABLE'
              AND TABLE_NAME LIKE ?
            ORDER BY TABLE_NAME
        """
        try:
            results = self.db.execute_query(query, (f'%{keyword}%',))
            return [row['TABLE_NAME'] for row in results]
        except Exception as e:
            logger.error(f"Error al buscar tablas con '{keyword}': {e}")
            return []

    def execute_custom_query(
        self,
        query: str,
        params: tuple = ()
    ) -> List[Dict[str, Any]]:
        """
        Ejecutar query personalizado

        Args:
            query: Query SQL a ejecutar
            params: Parámetros para el query

        Returns:
            Lista de diccionarios con los resultados
        """
        try:
            return self.db.execute_query(query, params)
        except Exception as e:
            logger.error(f"Error al ejecutar query: {e}")
            raise

    def get_sample_data(self, table_name: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Obtener datos de muestra de una tabla"""
        query = f"SELECT TOP {limit} * FROM [{table_name}]"
        try:
            return self.db.execute_query(query)
        except Exception as e:
            logger.error(f"Error al obtener muestra de {table_name}: {e}")
            return []

    def close(self):
        """Cerrar conexión"""
        self.db.disconnect()


class SAV7Explorer:
    """
    Clase auxiliar para explorar la estructura de SAV7
    Útil para descubrir tablas y campos de remisiones
    """

    def __init__(self, connector: Optional[SAV7Connector] = None):
        self.connector = connector or SAV7Connector()

    def find_remision_tables(self) -> Dict[str, List[str]]:
        """
        Buscar tablas que podrían contener remisiones

        Returns:
            Diccionario con tablas encontradas y sus columnas
        """
        keywords = ['REMISION', 'RECEPCION', 'ENTRADA', 'COMPRA', 'PEDIDO']
        result = {}

        for keyword in keywords:
            tables = self.connector.search_tables_by_keyword(keyword)
            for table in tables:
                columns = self.connector.get_table_columns(table)
                result[table] = [col['COLUMN_NAME'] for col in columns]

        return result

    def find_proveedor_tables(self) -> Dict[str, List[str]]:
        """Buscar tablas de proveedores"""
        keywords = ['PROVEEDOR', 'VENDOR', 'SUPPLIER']
        result = {}

        for keyword in keywords:
            tables = self.connector.search_tables_by_keyword(keyword)
            for table in tables:
                columns = self.connector.get_table_columns(table)
                result[table] = [col['COLUMN_NAME'] for col in columns]

        return result

    def generate_exploration_report(self) -> str:
        """
        Generar reporte de exploración de la base de datos
        Útil para configurar las queries de remisiones
        """
        report_lines = []
        report_lines.append("=" * 60)
        report_lines.append("REPORTE DE EXPLORACIÓN SAV7")
        report_lines.append("=" * 60)

        # Buscar tablas de remisiones
        report_lines.append("\n### TABLAS DE REMISIONES/RECEPCIONES ###")
        remision_tables = self.find_remision_tables()
        if remision_tables:
            for table, columns in remision_tables.items():
                report_lines.append(f"\nTabla: {table}")
                report_lines.append(f"  Columnas: {', '.join(columns[:10])}")
                if len(columns) > 10:
                    report_lines.append(f"  ... y {len(columns) - 10} más")
        else:
            report_lines.append("  No se encontraron tablas de remisiones")

        # Buscar tablas de proveedores
        report_lines.append("\n### TABLAS DE PROVEEDORES ###")
        proveedor_tables = self.find_proveedor_tables()
        if proveedor_tables:
            for table, columns in proveedor_tables.items():
                report_lines.append(f"\nTabla: {table}")
                report_lines.append(f"  Columnas: {', '.join(columns[:10])}")
        else:
            report_lines.append("  No se encontraron tablas de proveedores")

        report_lines.append("\n" + "=" * 60)

        return "\n".join(report_lines)
