"""
Módulo de consolidación de remisiones en SAV7
Registra facturas (Serie F) y actualiza remisiones (Serie R) como consolidadas
"""
from typing import List, Optional, Tuple
from datetime import datetime
from decimal import Decimal
from dataclasses import dataclass
import math
from loguru import logger

from config.settings import sav7_config
from src.erp.sav7_connector import SAV7Connector
from src.erp.models import Remision, ResultadoConciliacion
from src.sat.models import Factura


def numero_a_letra(numero: Decimal) -> str:
    """
    Convierte un número decimal a su representación en letras (español).
    Ejemplo: 2105.38 -> "DOS MIL CIENTO CINCO PESOS 38/100 M.N."
    """
    UNIDADES = ['', 'UN', 'DOS', 'TRES', 'CUATRO', 'CINCO', 'SEIS', 'SIETE', 'OCHO', 'NUEVE']
    DECENAS = ['', 'DIEZ', 'VEINTE', 'TREINTA', 'CUARENTA', 'CINCUENTA',
               'SESENTA', 'SETENTA', 'OCHENTA', 'NOVENTA']
    ESPECIALES = {
        11: 'ONCE', 12: 'DOCE', 13: 'TRECE', 14: 'CATORCE', 15: 'QUINCE',
        16: 'DIECISÉIS', 17: 'DIECISIETE', 18: 'DIECIOCHO', 19: 'DIECINUEVE',
        21: 'VEINTIUNO', 22: 'VEINTIDÓS', 23: 'VEINTITRÉS', 24: 'VEINTICUATRO',
        25: 'VEINTICINCO', 26: 'VEINTISÉIS', 27: 'VEINTISIETE', 28: 'VEINTIOCHO', 29: 'VEINTINUEVE'
    }
    CENTENAS = ['', 'CIENTO', 'DOSCIENTOS', 'TRESCIENTOS', 'CUATROCIENTOS', 'QUINIENTOS',
                'SEISCIENTOS', 'SETECIENTOS', 'OCHOCIENTOS', 'NOVECIENTOS']

    def convertir_grupo(n: int) -> str:
        """Convierte un número de 0 a 999"""
        if n == 0:
            return ''
        if n == 100:
            return 'CIEN'

        resultado = ''
        centenas = n // 100
        decenas = (n % 100) // 10
        unidades = n % 10
        resto = n % 100

        if centenas > 0:
            resultado += CENTENAS[centenas]

        if resto > 0:
            if centenas > 0:
                resultado += ' '

            if resto in ESPECIALES:
                resultado += ESPECIALES[resto]
            elif decenas > 0:
                resultado += DECENAS[decenas]
                if unidades > 0:
                    resultado += ' Y ' + UNIDADES[unidades]
            else:
                resultado += UNIDADES[unidades]

        return resultado

    try:
        numero = Decimal(str(numero))
        entero = int(numero)
        centavos = int(round((numero - entero) * 100))

        if entero == 0:
            letra = 'CERO'
        elif entero == 1:
            letra = 'UN'
        else:
            letra = ''
            millones = entero // 1000000
            miles = (entero % 1000000) // 1000
            unidades = entero % 1000

            if millones > 0:
                if millones == 1:
                    letra += 'UN MILLÓN'
                else:
                    letra += convertir_grupo(millones) + ' MILLONES'

            if miles > 0:
                if letra:
                    letra += ' '
                if miles == 1:
                    letra += 'MIL'
                else:
                    letra += convertir_grupo(miles) + ' MIL'

            if unidades > 0:
                if letra:
                    letra += ' '
                letra += convertir_grupo(unidades)

        return f"{letra} PESOS {centavos:02d}/100 M.N."
    except Exception:
        return ''


@dataclass
class ResultadoConsolidacion:
    """Resultado de una operación de consolidación"""
    exito: bool
    numero_factura_erp: Optional[str] = None  # F-XXXXX
    remisiones_consolidadas: List[str] = None  # Lista de R-XXXXX
    mensaje: str = ""
    error: Optional[str] = None
    adjuntos_resultado: Optional[str] = None  # Resultado del proceso de adjuntar archivos

    def __post_init__(self):
        if self.remisiones_consolidadas is None:
            self.remisiones_consolidadas = []


class ConsolidadorSAV7:
    """
    Consolidador de remisiones en SAV7

    Proceso:
    1. Crea registro de factura en SAVRecC (Serie F)
    2. Copia detalles de remisiones a SAVRecD (Serie F)
    3. Actualiza remisiones en SAVRecC (Serie R) como consolidadas
    4. Adjunta archivos XML y PDF al registro (opcional)
    """

    SERIE_FACTURA = 'F'
    SERIE_REMISION = 'R'
    USUARIO_SISTEMA = 'AGENTE_SAT'

    def __init__(self, connector: Optional[SAV7Connector] = None):
        self.connector = connector or SAV7Connector()
        self.config = sav7_config
        self._attachment_manager = None

    @property
    def attachment_manager(self):
        """Lazy loading del gestor de adjuntos"""
        if self._attachment_manager is None:
            from src.cfdi.attachment_manager import AttachmentManager
            self._attachment_manager = AttachmentManager(self.connector.db)
        return self._attachment_manager

    def consolidar(
        self,
        factura_sat: Factura,
        remisiones: List[Remision],
        resultado_conciliacion: ResultadoConciliacion
    ) -> ResultadoConsolidacion:
        """
        Consolidar remisiones con una factura del SAT

        Args:
            factura_sat: Factura del SAT (XML parseado)
            remisiones: Lista de remisiones a consolidar
            resultado_conciliacion: Resultado del matching

        Returns:
            ResultadoConsolidacion con el resultado de la operación
        """
        if not remisiones:
            return ResultadoConsolidacion(
                exito=False,
                mensaje="No hay remisiones para consolidar",
                error="Lista de remisiones vacía"
            )

        # Verificar que el match sea 100%
        if resultado_conciliacion.diferencia_porcentaje != 0:
            return ResultadoConsolidacion(
                exito=False,
                mensaje=f"Match no es 100% (diferencia: {resultado_conciliacion.diferencia_porcentaje:.2f}%)",
                error="Solo se consolidan matches al 100%"
            )

        # Verificar UUID antes de consolidar (debe existir siempre)
        if not factura_sat.uuid or not factura_sat.uuid.strip():
            logger.critical(f"UUID vacío para factura {factura_sat.folio} de {factura_sat.rfc_emisor} - NO se consolida")
            return ResultadoConsolidacion(
                exito=False,
                mensaje="UUID vacío en la factura SAT",
                error="TimbradoFolioFiscal quedaría vacío en BD"
            )

        try:
            # Ejecutar consolidación en transacción unica: SELECT MAX+1 con lock + INSERTs
            # El UPDLOCK/HOLDLOCK previene que otro proceso obtenga el mismo
            # NumRec entre el SELECT y el INSERT (race condition detectada
            # en produccion con F-68949).
            with self.connector.db.get_cursor() as cursor:
                # Obtener siguiente número DENTRO de la transacción con lock
                nuevo_num_rec = self._obtener_siguiente_numrec_con_lock(cursor)

                logger.info(
                    f"Iniciando consolidación: Factura SAT {factura_sat.folio} -> "
                    f"F-{nuevo_num_rec} con {len(remisiones)} remisiones"
                )

                # 1. Crear cabecera de factura (SAVRecC Serie F)
                self._insertar_cabecera_factura(
                    cursor, nuevo_num_rec, factura_sat, remisiones
                )

                # 2. Copiar detalles de remisiones (SAVRecD Serie F)
                self._insertar_detalles_factura(
                    cursor, nuevo_num_rec, remisiones
                )

                # 3. Actualizar remisiones como consolidadas (SAVRecC Serie R)
                for remision in remisiones:
                    self._actualizar_remision_consolidada(
                        cursor, remision, nuevo_num_rec
                    )

            # Si llegamos aquí, la transacción fue exitosa
            remisiones_ids = [r.id_remision for r in remisiones]
            logger.info(
                f"Consolidación exitosa: F-{nuevo_num_rec} <- {remisiones_ids}"
            )

            # Adjuntar archivos CFDI (no bloquea si falla)
            adjuntos_msg = self._adjuntar_archivos_cfdi(factura_sat, nuevo_num_rec)

            return ResultadoConsolidacion(
                exito=True,
                numero_factura_erp=f"F-{nuevo_num_rec}",
                remisiones_consolidadas=remisiones_ids,
                mensaje=f"Consolidación exitosa: {len(remisiones)} remisiones -> F-{nuevo_num_rec}",
                adjuntos_resultado=adjuntos_msg
            )

        except Exception as e:
            logger.error(f"Error en consolidación: {e}")
            return ResultadoConsolidacion(
                exito=False,
                mensaje="Error durante la consolidación",
                error=str(e)
            )

    def _adjuntar_archivos_cfdi(
        self,
        factura: Factura,
        num_rec: int
    ) -> Optional[str]:
        """
        Adjunta archivos CFDI a la factura consolidada.

        Este proceso NO debe fallar la consolidación. Si hay errores,
        solo se genera un log/alerta y se continúa.

        Args:
            factura: Factura SAT con ruta al XML original
            num_rec: Número de recepción de la factura F creada

        Returns:
            Mensaje describiendo el resultado, o None si hubo error
        """
        try:
            resultado = self.attachment_manager.adjuntar(
                factura=factura,
                num_rec=num_rec,
                fecha_consolidacion=datetime.now()
            )

            if resultado.exito:
                logger.info(f"Archivos CFDI adjuntados para F-{num_rec}: {resultado.nombre_base}")
                return resultado.mensaje
            else:
                logger.warning(
                    f"No se pudieron adjuntar archivos CFDI para F-{num_rec}: {resultado.error}"
                )
                return f"Error adjuntando: {resultado.error}"

        except Exception as e:
            logger.warning(f"Error en proceso de adjuntos para F-{num_rec} (no bloquea): {e}")
            return f"Error adjuntando: {str(e)}"

    def _obtener_siguiente_numrec(self) -> int:
        """Obtener el siguiente número de recepción para Serie F (sin lock, para consultas informativas)"""
        query = f"""
            SELECT ISNULL(MAX(NumRec), 0) + 1 as SiguienteNum
            FROM {self.config.tabla_remisiones}
            WHERE Serie = ?
        """
        result = self.connector.execute_custom_query(query, (self.SERIE_FACTURA,))
        siguiente = result[0]['SiguienteNum'] if result else 1
        return max(siguiente, self.config.numrec_rango_minimo)

    def _obtener_siguiente_numrec_con_lock(self, cursor) -> int:
        """
        Obtener siguiente NumRec DENTRO de la transaccion actual con lock.

        Usa UPDLOCK + HOLDLOCK para bloquear las filas leidas hasta que
        la transaccion termine (commit o rollback). Esto previene que
        otro proceso (Agente 3, SAV7 manual) obtenga el mismo NumRec.

        Ademas, aplica rango reservado (numrec_rango_minimo) para que
        el Agente 2 use numeros >= 800000, separados del rango normal
        del ERP (~68,000) y del Agente 3 (>= 900,000).

        IMPORTANTE: Este metodo DEBE ejecutarse dentro de un
        'with self.connector.db.get_cursor() as cursor' que tambien
        contenga los INSERTs posteriores.
        """
        query = f"""
            SELECT ISNULL(MAX(NumRec), 0) + 1 as SiguienteNum
            FROM {self.config.tabla_remisiones} WITH (UPDLOCK, HOLDLOCK)
            WHERE Serie = ?
        """
        cursor.execute(query, (self.SERIE_FACTURA,))
        row = cursor.fetchone()
        siguiente = row[0] if row else 1
        return max(siguiente, self.config.numrec_rango_minimo)

    def _insertar_cabecera_factura(
        self,
        cursor,
        num_rec: int,
        factura_sat: Factura,
        remisiones: List[Remision]
    ):
        """Insertar cabecera de factura en SAVRecC (Serie F)"""

        # Tomar datos del proveedor de la primera remisión
        remision_base = remisiones[0]

        # Construir comentario con referencias a remisiones
        nums_remisiones = [f"R-{r.numero_remision}" for r in remisiones]
        comentario = f"RECEPCIONES: {', '.join(nums_remisiones)}"

        # Calcular totales
        total = sum(r.total for r in remisiones)
        subtotal = sum(r.subtotal for r in remisiones)
        iva = sum(r.iva for r in remisiones)

        # Obtener campos IEPS y Retenciones de las remisiones R fuente
        nums_remision = [r.numero_remision for r in remisiones]
        placeholders = ','.join(['?' for _ in nums_remision])
        query_impuestos = f"""
            SELECT ISNULL(SUM(IEPS), 0) as IEPS,
                   ISNULL(SUM(IEPSAjuste), 0) as IEPSAjuste,
                   ISNULL(SUM(RetencionIVA), 0) as RetencionIVA,
                   ISNULL(SUM(RetencionIvaAjuste), 0) as RetencionIvaAjuste,
                   ISNULL(SUM(RetencionISR), 0) as RetencionISR,
                   ISNULL(SUM(RetencionISRAjuste), 0) as RetencionISRAjuste
            FROM {self.config.tabla_remisiones}
            WHERE Serie = ? AND NumRec IN ({placeholders})
        """
        cursor.execute(query_impuestos, (self.SERIE_REMISION, *nums_remision))
        row_imp = cursor.fetchone()
        ieps = row_imp[0] if row_imp else Decimal('0')
        ieps_ajuste = row_imp[1] if row_imp else Decimal('0')
        retencion_iva = row_imp[2] if row_imp else Decimal('0')
        retencion_iva_ajuste = row_imp[3] if row_imp else Decimal('0')
        retencion_isr = row_imp[4] if row_imp else Decimal('0')
        retencion_isr_ajuste = row_imp[5] if row_imp else Decimal('0')

        # Calcular artículos: suma de CANTIDADES de todos los detalles (REDONDEADO como sistema producción)
        total_articulos = sum(
            sum(d.cantidad for d in r.detalles) for r in remisiones
        )
        total_articulos = round(total_articulos)  # REDONDEAR como el sistema producción (199.8 → 200)
        # Partidas: número de líneas de detalle
        total_partidas = sum(len(r.detalles) for r in remisiones)

        # Total en letra
        total_letra = numero_a_letra(total)

        fecha_actual = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

        # Datos del proveedor (de la remisión base)
        ciudad = remision_base.ciudad_proveedor or 'NO ASIGNADA'
        estado = remision_base.estado_proveedor or 'NO ASIGNADO'
        tipo_proveedor = remision_base.tipo_proveedor or 'NACIONAL'
        comprador = remision_base.comprador or self.USUARIO_SISTEMA
        plazo = remision_base.plazo or 0
        sucursal = remision_base.sucursal or 5

        query = f"""
            INSERT INTO {self.config.tabla_remisiones} (
                Serie, NumRec, Proveedor, ProveedorNombre, Fecha,
                Comprador, Procesada, FechaAlta, UltimoCambio, Estatus,
                SubTotal1, Iva, Total, Pagado, Referencia,
                Comentario, Moneda, Paridad, Tipo, Plazo,
                SubTotal2, Factura, Saldo, Capturo, CapturoCambio,
                Articulos, Partidas, ProcesadaFecha, IntContable,
                TipoRecepcion, Consolidacion, RFC, TimbradoFolioFiscal,
                FacturaFecha, Sucursal, Departamento, Afectacion,
                MetododePago, NumOC, TotalLetra, Ciudad, Estado,
                TipoProveedor, TotalPrecio, TotalRecibidoNeto, SerieRFC,
                IEPS, IEPSAjuste, RetencionIVA, RetencionIvaAjuste,
                RetencionISR, RetencionISRAjuste
            ) VALUES (
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?
            )
        """

        params = (
            self.SERIE_FACTURA,                    # Serie
            num_rec,                                # NumRec
            remision_base.id_proveedor,            # Proveedor
            remision_base.nombre_proveedor,        # ProveedorNombre
            fecha_actual,                          # Fecha
            comprador,                             # Comprador (del usuario que capturó la remisión)
            1,                                     # Procesada
            fecha_actual,                          # FechaAlta
            fecha_actual,                          # UltimoCambio
            'Pendiente',                           # Estatus
            subtotal,                              # SubTotal1
            iva,                                   # Iva
            total,                                 # Total
            Decimal('0'),                          # Pagado
            'CREDITO',                             # Referencia
            comentario,                            # Comentario
            'PESOS',                               # Moneda
            Decimal('20.00'),                      # Paridad (20.00 como sistema producción)
            'Crédito',                             # Tipo
            plazo,                                 # Plazo (días de crédito del proveedor)
            subtotal,                              # SubTotal2
            factura_sat.folio or (factura_sat.uuid[-4:].upper() if factura_sat.uuid else ''),  # Factura (Folio del XML, o últimos 4 del UUID como SAV7)
            total,                                 # Saldo
            comprador,                             # Capturo
            comprador,                             # CapturoCambio
            int(total_articulos),                  # Articulos (suma de cantidades)
            total_partidas,                        # Partidas (número de líneas)
            fecha_actual,                          # ProcesadaFecha
            1,                                     # IntContable
            'COMPRAS',                             # TipoRecepcion
            1,                                     # Consolidacion
            factura_sat.rfc_emisor,                # RFC
            factura_sat.uuid.upper(),              # TimbradoFolioFiscal (normalizar a UPPERCASE)
            factura_sat.fecha_emision.replace(hour=0, minute=0, second=0, microsecond=0) if factura_sat.fecha_emision else fecha_actual,  # FacturaFecha (sin hora)
            sucursal,                              # Sucursal
            'TIENDA',                              # Departamento
            'TIENDA',                              # Afectacion
            factura_sat.metodo_pago.value if factura_sat.metodo_pago else 'PPD',  # MetododePago (del XML)
            0,                                     # NumOC
            total_letra,                           # TotalLetra
            ciudad,                                # Ciudad
            estado,                                # Estado
            tipo_proveedor,                        # TipoProveedor
            total,                                 # TotalPrecio
            total,                                 # TotalRecibidoNeto
            '',                                    # SerieRFC (vacío por ahora)
            ieps,                                  # IEPS (suma de remisiones R)
            ieps_ajuste,                           # IEPSAjuste (suma de remisiones R)
            retencion_iva,                         # RetencionIVA (suma de remisiones R)
            retencion_iva_ajuste,                  # RetencionIvaAjuste (suma de remisiones R)
            retencion_isr,                         # RetencionISR (suma de remisiones R)
            retencion_isr_ajuste,                  # RetencionISRAjuste (suma de remisiones R)
        )

        cursor.execute(query, params)
        logger.debug(f"Insertada cabecera F-{num_rec}")

    def _insertar_detalles_factura(
        self,
        cursor,
        num_rec: int,
        remisiones: List[Remision]
    ):
        """Insertar detalles de factura en SAVRecD (Serie F)"""

        # Obtener detalles originales de las remisiones directamente de la BD
        # para copiar todos los campos correctamente
        secuencia = 1

        for remision in remisiones:
            # Obtener detalles originales de esta remisión
            query_select = f"""
                SELECT *
                FROM {self.config.tabla_detalle_remisiones}
                WHERE Serie = ? AND NumRec = ?
                ORDER BY Orden
            """
            detalles_originales = self.connector.execute_custom_query(
                query_select,
                (self.SERIE_REMISION, remision.numero_remision)
            )

            partida_num = 1
            for detalle_orig in detalles_originales:
                # CodProv indica la referencia de origen: "R-XXXXX P1"
                cod_prov = f"R-{remision.numero_remision} P{partida_num}"

                query_insert = f"""
                    INSERT INTO {self.config.tabla_detalle_remisiones} (
                        Serie, NumRec, Producto, Talla, Nombre,
                        Proveedor, Cantidad, Costo, CostoImp, PorcDesc,
                        PorcIva, NumOC, Unidad, Unidad2, Unidad2Valor,
                        Servicio, Registro1, ControlTalla, CodProv, Modelo,
                        Pedimento, Orden, ComplementoIva, CantidadNeta, CostoDif,
                        Precio, CantidadUM2, Lotes, UltimoCostoC, IEPSPorc,
                        RetencionIvaPorc, RetencionISRPorc
                    ) VALUES (
                        ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?,
                        ?, ?
                    )
                """

                params = (
                    self.SERIE_FACTURA,                              # Serie
                    num_rec,                                          # NumRec
                    detalle_orig.get('Producto', ''),                # Producto
                    secuencia,                                        # Talla (secuencial)
                    detalle_orig.get('Nombre', ''),                  # Nombre
                    detalle_orig.get('Proveedor', ''),               # Proveedor
                    detalle_orig.get('Cantidad', 0),                 # Cantidad
                    detalle_orig.get('Costo', 0),                    # Costo
                    detalle_orig.get('CostoImp', 0),                 # CostoImp
                    detalle_orig.get('PorcDesc', 0),                 # PorcDesc
                    detalle_orig.get('PorcIva', 0),                  # PorcIva
                    detalle_orig.get('NumOC', 0),                    # NumOC
                    detalle_orig.get('Unidad', 'KG'),                # Unidad
                    detalle_orig.get('Unidad2', ''),                 # Unidad2
                    detalle_orig.get('Unidad2Valor', 1),             # Unidad2Valor
                    1,                                                # Servicio (cambia a 1)
                    detalle_orig.get('Registro1', 1),                # Registro1
                    detalle_orig.get('ControlTalla', 0),             # ControlTalla
                    cod_prov,                                         # CodProv (referencia origen)
                    detalle_orig.get('Modelo', ''),                  # Modelo
                    detalle_orig.get('Pedimento', ''),               # Pedimento
                    secuencia,                                        # Orden (secuencial)
                    detalle_orig.get('ComplementoIva', 0),           # ComplementoIva
                    detalle_orig.get('CantidadNeta', 0),             # CantidadNeta
                    detalle_orig.get('CostoDif', 0),                 # CostoDif
                    detalle_orig.get('Precio', 0),                   # Precio
                    detalle_orig.get('CantidadUM2', 0),              # CantidadUM2
                    detalle_orig.get('Lotes', 0),                    # Lotes
                    detalle_orig.get('UltimoCostoC', 0),             # UltimoCostoC
                    detalle_orig.get('IEPSPorc', 0),                 # IEPSPorc
                    detalle_orig.get('RetencionIvaPorc', 0),         # RetencionIvaPorc
                    detalle_orig.get('RetencionISRPorc', 0),         # RetencionISRPorc
                )

                cursor.execute(query_insert, params)
                secuencia += 1
                partida_num += 1

        logger.debug(f"Insertados {secuencia - 1} detalles para F-{num_rec}")

    def _actualizar_remision_consolidada(
        self,
        cursor,
        remision: Remision,
        num_factura: int
    ):
        """Actualizar remisión como consolidada en SAVRecC (Serie R)

        Nota: No se guarda el UUID en la remisión (TimbradoFolioFiscal)
        para coincidir con el comportamiento de producción.
        """

        fecha_actual = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

        query = f"""
            UPDATE {self.config.tabla_remisiones}
            SET
                Estatus = ?,
                Saldo = ?,
                Consolida = ?,
                ConsolidaSerie = ?,
                ConsolidaNumRec = ?,
                CancelacionFecha = ?,
                CancelacionCapturo = ?,
                CancelacionMotivo = ?,
                UltimoCambio = ?
            WHERE Serie = ?
              AND NumRec = ?
        """

        params = (
            'Consolidada',                     # Estatus
            Decimal('0'),                      # Saldo
            1,                                 # Consolida (bit: 1=true)
            self.SERIE_FACTURA,                # ConsolidaSerie
            num_factura,                       # ConsolidaNumRec
            fecha_actual,                      # CancelacionFecha
            self.USUARIO_SISTEMA,              # CancelacionCapturo
            'CONSOLIDACION',                   # CancelacionMotivo
            fecha_actual,                      # UltimoCambio
            self.SERIE_REMISION,               # Serie (WHERE)
            remision.numero_remision,          # NumRec (WHERE)
        )

        cursor.execute(query, params)
        logger.debug(f"Actualizada remisión R-{remision.numero_remision} -> F-{num_factura}")

    def verificar_remision_disponible(self, serie: str, num_rec: str) -> bool:
        """
        Verificar si una remisión está disponible para consolidar
        (no está ya consolidada)
        """
        query = f"""
            SELECT Estatus, Consolidacion
            FROM {self.config.tabla_remisiones}
            WHERE Serie = ? AND NumRec = ?
        """

        result = self.connector.execute_custom_query(query, (serie, num_rec))

        if not result:
            return False

        estatus = result[0].get('Estatus', '')
        consolidacion = result[0].get('Consolidacion', 0)

        # Disponible si no está consolidada
        return estatus != 'Consolidada' and consolidacion != 1

    def consolidar_lote(
        self,
        resultados_conciliacion: List[Tuple[Factura, ResultadoConciliacion]]
    ) -> List[ResultadoConsolidacion]:
        """
        Consolidar un lote de facturas con sus remisiones
        Solo procesa los matches al 100%

        Args:
            resultados_conciliacion: Lista de tuplas (Factura, ResultadoConciliacion)

        Returns:
            Lista de ResultadoConsolidacion
        """
        resultados = []

        for factura_sat, resultado in resultados_conciliacion:
            # Solo procesar matches al 100%
            if resultado.diferencia_porcentaje != 0:
                resultados.append(ResultadoConsolidacion(
                    exito=False,
                    mensaje=f"Omitido: Match no es 100% ({resultado.diferencia_porcentaje:.2f}%)"
                ))
                continue

            # Verificar que haya remisiones
            if not resultado.remisiones:
                resultados.append(ResultadoConsolidacion(
                    exito=False,
                    mensaje="Omitido: No hay remisiones vinculadas"
                ))
                continue

            # Verificar que las remisiones estén disponibles
            remisiones_no_disponibles = []
            for remision in resultado.remisiones:
                if not self.verificar_remision_disponible(
                    self.SERIE_REMISION,
                    remision.numero_remision
                ):
                    remisiones_no_disponibles.append(remision.id_remision)

            if remisiones_no_disponibles:
                resultados.append(ResultadoConsolidacion(
                    exito=False,
                    mensaje=f"Omitido: Remisiones ya consolidadas: {remisiones_no_disponibles}"
                ))
                continue

            # Consolidar
            resultado_consolidacion = self.consolidar(
                factura_sat,
                resultado.remisiones,
                resultado
            )
            resultados.append(resultado_consolidacion)

        # Resumen
        exitosos = sum(1 for r in resultados if r.exito)
        logger.info(f"Consolidación por lote: {exitosos}/{len(resultados)} exitosos")

        return resultados
