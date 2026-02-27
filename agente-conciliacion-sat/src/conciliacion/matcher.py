"""
Algoritmo de matching para conciliación de facturas con remisiones
Soporta matching 1:1 y 1:N (una factura a múltiples remisiones)
Soporta matching por PDF de proveedor (cuando incluye números de remisión)
"""
from typing import List, Optional, Tuple, Set, Dict
from datetime import datetime, timedelta
from decimal import Decimal
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path
from fuzzywuzzy import fuzz
from loguru import logger
import numpy as np
from scipy.optimize import linear_sum_assignment

from config.settings import settings
from src.sat.models import Factura
from src.erp.models import Remision, ResultadoConciliacion
from src.erp.remisiones import RemisionesRepository

# Soporte PDF (opcional)
try:
    from src.pdf import PDF_SUPPORT, buscar_pdf_para_xml, PDFRemisionExtractor
except ImportError:
    PDF_SUPPORT = False


@dataclass
class MatchScore:
    """Puntuación de matching entre factura y remisión(es)"""
    score_total: float
    score_monto: float
    score_fecha: float
    score_productos: float
    diferencia_monto: Decimal
    diferencia_porcentaje: float
    dias_diferencia: int
    detalles: List[str]
    es_multi_remision: bool = False
    remisiones: List[Remision] = field(default_factory=list)
    metodo_match: str = "algoritmo"  # "algoritmo", "pdf", "directo"


class ConciliacionMatcher:
    """Algoritmo de conciliación factura-remisión (soporta 1:1 y 1:N)"""

    # Pesos para el cálculo del score
    PESO_MONTO = 0.50      # 50% del score
    PESO_FECHA = 0.30      # 30% del score
    PESO_PRODUCTOS = 0.20  # 20% del score

    # Umbrales
    SCORE_MINIMO_ACEPTABLE = 0.70  # 70% para considerar match válido
    MAX_REMISIONES_COMBINACION = 10  # Máximo de remisiones a combinar (aumentado de 5 a 10)

    def __init__(self, repository: Optional[RemisionesRepository] = None):
        self.repository = repository or RemisionesRepository()
        self.tolerancia_monto = Decimal(str(settings.tolerancia_monto_porcentaje / 100))
        self.dias_rango = settings.dias_rango_busqueda
        self.umbral_similitud = settings.umbral_similitud_texto
        # Registro de remisiones ya asignadas (prevención de duplicados)
        self._remisiones_usadas: Set[str] = set()

    def conciliar_factura(self, factura: Factura) -> ResultadoConciliacion:
        """
        Buscar y conciliar una factura con remisiones del ERP
        Soporta matching 1:1 y 1:N (una factura a múltiples remisiones)

        Prioridad de búsqueda:
        1. Si la factura indica número de remisión -> match directo
        2. Búsqueda por RFC + fecha + monto (algoritmo heurístico)

        Args:
            factura: Factura a conciliar

        Returns:
            Resultado de la conciliación
        """
        logger.info(f"Iniciando conciliación de factura {factura.uuid}")

        # Crear resultado base
        resultado = ResultadoConciliacion(
            uuid_factura=factura.uuid,
            identificador_factura=factura.identificador,
            rfc_emisor=factura.rfc_emisor,
            nombre_emisor=factura.nombre_emisor,
            fecha_factura=factura.fecha_emision,
            total_factura=factura.total,
        )

        # PRIORIDAD 1: Buscar PDF correspondiente con números de remisión
        if PDF_SUPPORT and hasattr(factura, 'archivo_xml') and factura.archivo_xml:
            resultado_pdf = self._buscar_por_pdf(factura, resultado)
            if resultado_pdf:
                return resultado_pdf

        # PRIORIDAD 2: Si la factura indica número de remisión, buscar directamente
        if hasattr(factura, 'numero_remision_indicado') and factura.numero_remision_indicado:
            logger.info(f"Factura indica número de remisión: {factura.numero_remision_indicado}")
            resultado_directo = self._buscar_por_numero_directo(factura, resultado)
            if resultado_directo:
                return resultado_directo
            # Si no encontró, continuar con búsqueda heurística
            logger.warning(f"No se encontró remisión {factura.numero_remision_indicado}, intentando búsqueda heurística")

        # PRIORIDAD 3: Buscar remisiones candidatas con algoritmo heurístico
        remisiones = self.repository.buscar_para_conciliacion(
            rfc_proveedor=factura.rfc_emisor,
            fecha_factura=factura.fecha_emision,
            monto_total=factura.total,
            dias_rango=self.dias_rango
        )

        # Filtrar remisiones ya usadas en esta ejecución (prevención de duplicados)
        if self._remisiones_usadas:
            remisiones_filtradas = [r for r in remisiones if r.id_remision not in self._remisiones_usadas]
            if len(remisiones_filtradas) < len(remisiones):
                excluidas = len(remisiones) - len(remisiones_filtradas)
                logger.debug(f"Excluidas {excluidas} remisiones ya asignadas a otras facturas")
            remisiones = remisiones_filtradas

        if not remisiones:
            logger.warning(f"No se encontraron remisiones para factura {factura.uuid}")
            resultado.alertas.append("SIN_REMISION: No se encontró remisión asociada")
            return resultado

        # 1. Primero intentar match 1:1 (una remisión)
        mejor_match_simple: Optional[Tuple[Remision, MatchScore]] = None
        for remision in remisiones:
            score = self._calcular_score(factura, remision)
            if mejor_match_simple is None or score.score_total > mejor_match_simple[1].score_total:
                mejor_match_simple = (remision, score)

        # 2. SIEMPRE intentar combinaciones múltiples
        # Puede existir una combinación exacta (100%) aunque el mejor match simple
        # esté dentro de tolerancia (ej: R-47031 $8,360 vs combinación exacta $8,510)
        mejor_match_multi: Optional[MatchScore] = None
        mejor_match_multi = self._buscar_combinacion_remisiones(factura, remisiones)

        # Segundo pase: si no encontró match exacto, expandir rango de fechas y candidatas
        # Cubre casos PPD donde remisiones son de semanas anteriores
        if mejor_match_multi is None or mejor_match_multi.diferencia_monto != Decimal('0'):
            DIAS_RANGO_SEGUNDO_PASE = 30
            logger.info(
                f"Segundo pase: expandiendo búsqueda a {DIAS_RANGO_SEGUNDO_PASE} días "
                f"y 30 candidatas para {factura.uuid}"
            )
            remisiones_ampliadas = self.repository.buscar_para_conciliacion(
                rfc_proveedor=factura.rfc_emisor,
                fecha_factura=factura.fecha_emision,
                monto_total=factura.total,
                dias_rango=DIAS_RANGO_SEGUNDO_PASE
            )
            # Filtrar remisiones ya usadas
            if self._remisiones_usadas:
                remisiones_ampliadas = [r for r in remisiones_ampliadas if r.id_remision not in self._remisiones_usadas]

            if len(remisiones_ampliadas) > len(remisiones):
                logger.info(
                    f"Segundo pase encontró {len(remisiones_ampliadas)} remisiones "
                    f"(vs {len(remisiones)} en primer pase)"
                )
                segundo_pase = self._buscar_combinacion_remisiones(
                    factura, remisiones_ampliadas, limite_candidatas=30
                )
                if segundo_pase is not None:
                    if mejor_match_multi is None or segundo_pase.score_total > mejor_match_multi.score_total:
                        mejor_match_multi = segundo_pase

        # 3. Determinar cuál es el mejor resultado
        usar_multi = False
        if mejor_match_multi and mejor_match_simple:
            _, score_simple = mejor_match_simple
            # Usar multi si tiene mejor score o menor diferencia de monto
            if (mejor_match_multi.score_total > score_simple.score_total or
                abs(mejor_match_multi.diferencia_porcentaje) < abs(score_simple.diferencia_porcentaje)):
                usar_multi = True
        elif mejor_match_multi:
            # Solo hay resultado multi (no hubo match simple)
            usar_multi = True

        if usar_multi and mejor_match_multi:
            # Usar resultado multi-remisión
            self._aplicar_resultado_multi(resultado, mejor_match_multi, factura)
        elif mejor_match_simple:
            # Usar resultado simple (1:1)
            self._aplicar_resultado_simple(resultado, mejor_match_simple, factura)

        return resultado

    def _buscar_por_pdf(
        self,
        factura: Factura,
        resultado: ResultadoConciliacion
    ) -> Optional[ResultadoConciliacion]:
        """
        Buscar remisiones usando PDF del proveedor que lista los números.

        Args:
            factura: Factura con archivo_origen (path al XML)
            resultado: Resultado base a completar

        Returns:
            ResultadoConciliacion completado si encontró remisiones en PDF, None si no
        """
        try:
            xml_path = Path(factura.archivo_xml)
            pdf_path = buscar_pdf_para_xml(xml_path, uuid_factura=factura.uuid)

            if not pdf_path:
                logger.debug(f"No se encontró PDF para {xml_path.name}")
                return None

            logger.info(f"PDF encontrado: {pdf_path.name}")

            # Extraer números de remisión del PDF
            extractor = PDFRemisionExtractor()
            pdf_result = extractor.extraer_remisiones(pdf_path)

            # Continuar si hay remisiones R-XXXXX O hay Orden de Compra
            if not pdf_result.exito:
                logger.debug(f"Error al procesar PDF {pdf_path.name}")
                return None

            # Si no hay remisiones R-XXXXX ni Orden de Compra, no hay nada que buscar
            if not pdf_result.remisiones_encontradas and not pdf_result.orden_compra:
                logger.debug(f"No se encontraron remisiones ni OC en PDF {pdf_path.name}")
                return None

            logger.info(
                f"PDF {pdf_path.name}: {len(pdf_result.remisiones_encontradas)} remisiones "
                f"-> {pdf_result.remisiones_encontradas}"
                f"{f', OC: {pdf_result.orden_compra}' if pdf_result.orden_compra else ''}"
            )

            # Los números del PDF (R-XXXXX o Orden de Compra) se buscan en el campo Factura de SAVRecC
            # porque ese campo contiene la referencia del proveedor, no NumRec
            remisiones_encontradas = []

            # Buscar cada remisión R-XXXXX del PDF en el campo Factura
            for num_remision in pdf_result.remisiones_encontradas:
                num_str = str(num_remision)
                logger.info(f"Buscando remisión con Factura='{num_str}' en BD...")
                remisiones = self.repository.buscar_por_orden_compra(
                    num_str,
                    factura.rfc_emisor
                )
                if remisiones:
                    remisiones_encontradas.extend(remisiones)
                    logger.info(f"Encontrada remisión con Factura='{num_str}'")
                else:
                    # Intentar sin RFC
                    remisiones = self.repository.buscar_por_orden_compra(num_str)
                    if remisiones:
                        remisiones_encontradas.extend(remisiones)
                        logger.info(f"Encontrada remisión con Factura='{num_str}' (sin filtro RFC)")
                    else:
                        logger.warning(f"Remisión con Factura='{num_str}' no encontrada en BD")

            # Si el PDF tiene Orden de Compra explícita y no encontró por R-XXXXX
            if not remisiones_encontradas and pdf_result.orden_compra:
                logger.info(f"Buscando por Orden de Compra explícita: {pdf_result.orden_compra}")
                remisiones = self.repository.buscar_por_orden_compra(
                    pdf_result.orden_compra,
                    factura.rfc_emisor
                )
                if remisiones:
                    remisiones_encontradas.extend(remisiones)
                    logger.info(f"Encontradas {len(remisiones)} remisiones por Orden de Compra")
                else:
                    # Intentar sin RFC
                    remisiones = self.repository.buscar_por_orden_compra(pdf_result.orden_compra)
                    if remisiones:
                        remisiones_encontradas.extend(remisiones)
                        logger.info(f"Encontradas {len(remisiones)} remisiones por Orden de Compra (sin filtro RFC)")

            if not remisiones_encontradas:
                logger.warning(f"Ninguna remisión del PDF encontrada en BD")
                return None

            # Filtrar remisiones ya usadas (prevención de duplicados)
            if self._remisiones_usadas:
                remisiones_encontradas = [r for r in remisiones_encontradas
                                          if r.id_remision not in self._remisiones_usadas]
                if not remisiones_encontradas:
                    logger.warning(f"Remisiones del PDF ya fueron asignadas a otras facturas")
                    return None

            # Calcular totales
            total_remisiones = sum(r.total for r in remisiones_encontradas)
            diferencia = abs(factura.total - total_remisiones)
            diferencia_pct = float(diferencia / factura.total * 100) if factura.total else 0

            # Crear score para resultado por PDF
            score = MatchScore(
                score_total=1.0 if diferencia_pct < 1 else (1 - diferencia_pct/100),
                score_monto=1.0 if diferencia_pct < 1 else (1 - diferencia_pct/100),
                score_fecha=1.0,  # No aplica para PDF
                score_productos=1.0,  # No aplica para PDF
                diferencia_monto=Decimal(str(diferencia)),
                diferencia_porcentaje=diferencia_pct,
                dias_diferencia=0,
                detalles=[f"Match por PDF: {pdf_path.name}"],
                es_multi_remision=len(remisiones_encontradas) > 1,
                remisiones=remisiones_encontradas,
                metodo_match="pdf"
            )

            # Llenar resultado
            if len(remisiones_encontradas) == 1:
                resultado.remision = remisiones_encontradas[0]
                resultado.numero_remision = remisiones_encontradas[0].id_remision
                resultado.fecha_remision = remisiones_encontradas[0].fecha_remision
            else:
                resultado.numero_remision = ", ".join(
                    f"R-{r.id_remision}" for r in remisiones_encontradas
                )
                resultado.fecha_remision = max(r.fecha_remision for r in remisiones_encontradas)

            resultado.remisiones = remisiones_encontradas
            resultado.total_remision = total_remisiones
            resultado.diferencia_monto = diferencia
            resultado.diferencia_porcentaje = diferencia_pct
            resultado.score_matching = score.score_total
            resultado.metodo_matching = "pdf"
            resultado.alertas.append(f"MATCH_PDF: {len(remisiones_encontradas)} remisiones desde {pdf_path.name}")

            if diferencia_pct < settings.tolerancia_monto_porcentaje:
                resultado.conciliacion_exitosa = True
                resultado.es_multi_remision = len(remisiones_encontradas) > 1
                logger.info(
                    f"Conciliación PDF exitosa: Factura {factura.uuid} -> "
                    f"{len(remisiones_encontradas)} remisiones (Score: {score.score_total:.2f})"
                )
            else:
                resultado.alertas.append(
                    f"DIFERENCIA_MONTO: PDF indica remisiones pero hay diferencia de {diferencia_pct:.2f}%"
                )

            return resultado

        except Exception as e:
            logger.error(f"Error buscando por PDF: {e}")
            return None

    def _buscar_por_numero_directo(
        self,
        factura: Factura,
        resultado: ResultadoConciliacion
    ) -> Optional[ResultadoConciliacion]:
        """
        Buscar remisión directamente por número indicado en la factura

        Args:
            factura: Factura con numero_remision_indicado
            resultado: Resultado base a completar

        Returns:
            ResultadoConciliacion completado si encontró, None si no
        """
        numero = factura.numero_remision_indicado
        remisiones = self.repository.buscar_por_numero(numero, factura.rfc_emisor)

        if not remisiones:
            # Intentar sin filtro de RFC por si hay diferencias
            remisiones = self.repository.buscar_por_numero(numero)

        if not remisiones:
            return None

        # Filtrar remisiones ya usadas (prevención de duplicados)
        if self._remisiones_usadas:
            remisiones = [r for r in remisiones if r.id_remision not in self._remisiones_usadas]
            if not remisiones:
                logger.warning(f"Remisión {numero} ya fue asignada a otra factura")
                return None

        # Si encontró exactamente una, usarla
        if len(remisiones) == 1:
            remision = remisiones[0]
            score = self._calcular_score(factura, remision)

            resultado.remision = remision
            resultado.remisiones = [remision]
            resultado.numero_remision = remision.id_remision
            resultado.fecha_remision = remision.fecha_remision
            resultado.total_remision = remision.total
            resultado.score_matching = score.score_total
            resultado.diferencia_monto = score.diferencia_monto
            resultado.diferencia_porcentaje = score.diferencia_porcentaje
            resultado.metodo_matching = "numero_directo"

            # Determinar éxito
            if abs(score.diferencia_porcentaje) <= settings.tolerancia_monto_porcentaje:
                resultado.conciliacion_exitosa = True
                logger.info(
                    f"Conciliación DIRECTA exitosa: Factura {factura.uuid} -> "
                    f"Remisión {remision.id_remision} (indicada en factura)"
                )
            else:
                resultado.alertas.append(
                    f"DIFERENCIA_MONTO: Diferencia de {score.diferencia_porcentaje:.2f}% "
                    f"(${score.diferencia_monto:,.2f})"
                )

            resultado.alertas.insert(0, f"MATCH_DIRECTO: Remisión {numero} indicada en factura")
            resultado.alertas.extend(score.detalles)
            return resultado

        # Si encontró múltiples, usar la que mejor coincida con RFC
        for remision in remisiones:
            if remision.rfc_proveedor == factura.rfc_emisor:
                score = self._calcular_score(factura, remision)
                resultado.remision = remision
                resultado.remisiones = [remision]
                resultado.numero_remision = remision.id_remision
                resultado.fecha_remision = remision.fecha_remision
                resultado.total_remision = remision.total
                resultado.score_matching = score.score_total
                resultado.diferencia_monto = score.diferencia_monto
                resultado.diferencia_porcentaje = score.diferencia_porcentaje
                resultado.metodo_matching = "numero_directo"

                if abs(score.diferencia_porcentaje) <= settings.tolerancia_monto_porcentaje:
                    resultado.conciliacion_exitosa = True

                resultado.alertas.insert(0, f"MATCH_DIRECTO: Remisión {numero} indicada en factura")
                resultado.alertas.extend(score.detalles)
                return resultado

        return None

    def _aplicar_resultado_simple(
        self,
        resultado: ResultadoConciliacion,
        match: Tuple[Remision, MatchScore],
        factura: Factura
    ):
        """Aplicar resultado de matching simple (1 factura = 1 remisión)"""
        remision, score = match
        resultado.remision = remision
        resultado.remisiones = [remision]
        resultado.numero_remision = remision.id_remision
        resultado.fecha_remision = remision.fecha_remision
        resultado.total_remision = remision.total
        resultado.score_matching = score.score_total
        resultado.diferencia_monto = score.diferencia_monto
        resultado.diferencia_porcentaje = score.diferencia_porcentaje
        resultado.es_multi_remision = False

        # Determinar si la conciliación es exitosa
        # Solo aceptar diferencia exacta ($0.00)
        if score.score_total >= self.SCORE_MINIMO_ACEPTABLE:
            if abs(score.diferencia_monto) == Decimal('0.00'):
                resultado.conciliacion_exitosa = True
                logger.info(
                    f"Conciliación exitosa: Factura {factura.uuid} -> "
                    f"Remisión {remision.id_remision} (Score: {score.score_total:.2f}, Diff: ${score.diferencia_monto:.2f})"
                )
            else:
                resultado.alertas.append(
                    f"DIFERENCIA_MONTO: Diferencia de ${score.diferencia_monto:,.2f} "
                    f"({score.diferencia_porcentaje:.2f}%) - Requiere match exacto"
                )
        else:
            resultado.alertas.append(
                f"MATCH_BAJO: Score de matching bajo ({score.score_total:.2f})"
            )

        resultado.alertas.extend(score.detalles)

    def _aplicar_resultado_multi(
        self,
        resultado: ResultadoConciliacion,
        score: MatchScore,
        factura: Factura
    ):
        """Aplicar resultado de matching múltiple (1 factura = N remisiones)"""
        resultado.remisiones = score.remisiones
        resultado.remision = score.remisiones[0] if score.remisiones else None
        resultado.numero_remision = ', '.join(r.id_remision for r in score.remisiones)
        resultado.fecha_remision = min(r.fecha_remision for r in score.remisiones) if score.remisiones else None
        resultado.total_remision = sum(r.total for r in score.remisiones)
        resultado.score_matching = score.score_total
        resultado.diferencia_monto = score.diferencia_monto
        resultado.diferencia_porcentaje = score.diferencia_porcentaje
        resultado.es_multi_remision = True
        resultado.metodo_matching = "multi_remision"

        # Determinar si la conciliación es exitosa
        # Para multi-remisión: solo aceptar diferencia exacta ($0.00)
        if score.score_total >= self.SCORE_MINIMO_ACEPTABLE:
            if abs(score.diferencia_monto) == Decimal('0.00'):
                resultado.conciliacion_exitosa = True
                nums = ', '.join(r.id_remision for r in score.remisiones)
                logger.info(
                    f"Conciliación MULTI exitosa: Factura {factura.uuid} -> "
                    f"Remisiones [{nums}] (Score: {score.score_total:.2f}, Diff: ${score.diferencia_monto:.2f})"
                )
            else:
                resultado.alertas.append(
                    f"DIFERENCIA_MONTO: Diferencia de ${score.diferencia_monto:,.2f} "
                    f"({score.diferencia_porcentaje:.2f}%) - Multi-remisión requiere match exacto"
                )
        else:
            resultado.alertas.append(
                f"MATCH_BAJO: Score de matching bajo ({score.score_total:.2f})"
            )

        # Agregar nota de multi-remisión
        resultado.alertas.insert(0, f"MULTI_REMISION: Factura corresponde a {len(score.remisiones)} remisiones")
        resultado.alertas.extend(score.detalles)

    def _buscar_combinacion_remisiones(
        self,
        factura: Factura,
        remisiones: List[Remision],
        limite_candidatas: int = 15
    ) -> Optional[MatchScore]:
        """
        Buscar combinación de remisiones que sumen el total de la factura

        Args:
            factura: Factura a conciliar
            remisiones: Lista de remisiones candidatas

        Returns:
            MatchScore si encuentra una buena combinación, None si no
        """
        logger.debug(f"Buscando combinación de remisiones para factura {factura.uuid}")

        # Filtrar remisiones que no estén ya facturadas y sean menores al total
        candidatas = [r for r in remisiones if r.total <= factura.total and not r.esta_facturada]

        if len(candidatas) < 2:
            return None

        # Limitar cantidad para evitar explosión combinatoria
        candidatas = candidatas[:min(len(candidatas), limite_candidatas)]

        # Contar productos del XML
        productos_xml = len(factura.conceptos)
        logger.debug(f"Productos en XML: {productos_xml}")

        mejor_combinacion: Optional[MatchScore] = None
        mejor_con_productos_exactos: Optional[MatchScore] = None
        # Para multi-remisión: solo buscar combinaciones exactas ($0.00 de diferencia)
        tolerancia_decimal = Decimal('0.00')

        # Probar combinaciones de 2 a MAX_REMISIONES_COMBINACION remisiones
        for num_remisiones in range(2, min(len(candidatas) + 1, self.MAX_REMISIONES_COMBINACION + 1)):
            for combo in combinations(candidatas, num_remisiones):
                total_combo = sum(r.total for r in combo)
                diferencia = abs(factura.total - total_combo)

                # Si la suma está dentro de la tolerancia
                if diferencia <= tolerancia_decimal:
                    # Contar productos de la combinación de remisiones
                    productos_combo = sum(len(r.detalles) for r in combo)

                    score = self._calcular_score_multi(factura, list(combo))

                    # PRIORIDAD 1: Si diferencia es exacta (0), retornar inmediatamente
                    if diferencia == Decimal('0.00'):  # Solo match exacto
                        logger.info(
                            f"Encontrada combinación EXACTA: {len(combo)} remisiones, "
                            f"diferencia ${diferencia:.2f}, remisiones: {[r.id_remision for r in combo]}"
                        )
                        return score

                    # PRIORIDAD 2: Guardar mejor combinación con productos exactos
                    if productos_combo == productos_xml:
                        logger.debug(
                            f"Match por productos: XML={productos_xml}, Combo={productos_combo} "
                            f"(remisiones: {[r.id_remision for r in combo]})"
                        )
                        if mejor_con_productos_exactos is None or score.score_total > mejor_con_productos_exactos.score_total:
                            mejor_con_productos_exactos = score

                            # Si además es match perfecto por monto, retornar inmediatamente
                            if score.diferencia_porcentaje <= 0.5:
                                logger.info(
                                    f"Encontrada combinación perfecta: {len(combo)} remisiones, "
                                    f"{productos_combo} productos, diferencia {score.diferencia_porcentaje:.2f}%"
                                )
                                return mejor_con_productos_exactos

                    # PRIORIDAD 3: Mantener mejor combinación por menor diferencia de monto
                    if mejor_combinacion is None:
                        mejor_combinacion = score
                    elif score.diferencia_porcentaje < mejor_combinacion.diferencia_porcentaje:
                        # Priorizar menor diferencia de monto
                        mejor_combinacion = score
                    elif (score.diferencia_porcentaje == mejor_combinacion.diferencia_porcentaje
                          and score.score_total > mejor_combinacion.score_total):
                        # Si misma diferencia, usar score total como desempate
                        mejor_combinacion = score

        # Preferir combinación con productos exactos si existe
        if mejor_con_productos_exactos:
            logger.info(
                f"Mejor combinación (por productos): {len(mejor_con_productos_exactos.remisiones)} remisiones, "
                f"{productos_xml} productos, diferencia {mejor_con_productos_exactos.diferencia_porcentaje:.2f}%"
            )
            return mejor_con_productos_exactos

        if mejor_combinacion:
            logger.info(
                f"Mejor combinación encontrada: {len(mejor_combinacion.remisiones)} remisiones, "
                f"diferencia {mejor_combinacion.diferencia_porcentaje:.2f}%"
            )

        return mejor_combinacion

    def _calcular_score_multi(self, factura: Factura, remisiones: List[Remision]) -> MatchScore:
        """
        Calcular score para una combinación de múltiples remisiones

        Args:
            factura: Factura a evaluar
            remisiones: Lista de remisiones a combinar

        Returns:
            MatchScore con los detalles de la evaluación
        """
        detalles = []

        # Calcular totales combinados
        total_remisiones = sum(r.total for r in remisiones)

        # 1. Score por monto
        diferencia_monto = abs(factura.total - total_remisiones)
        if factura.total > 0:
            diferencia_porcentaje = float((diferencia_monto / factura.total) * 100)
        else:
            diferencia_porcentaje = 100.0

        if diferencia_porcentaje <= settings.tolerancia_monto_porcentaje:
            score_monto = 1.0
        elif diferencia_porcentaje <= settings.tolerancia_monto_porcentaje * 2:
            score_monto = 0.8
        elif diferencia_porcentaje <= 10:
            score_monto = 0.6
        else:
            score_monto = max(0, 1 - (diferencia_porcentaje / 50))

        # 2. Score por fecha (usar promedio de días de diferencia)
        dias_diferencias = [abs((factura.fecha_emision - r.fecha_remision).days) for r in remisiones]
        dias_diferencia = int(sum(dias_diferencias) / len(dias_diferencias))
        max_dias = max(dias_diferencias)

        if max_dias <= 3:
            score_fecha = 1.0
        elif max_dias <= 7:
            score_fecha = 0.8
        elif max_dias <= 14:
            score_fecha = 0.6
        else:
            score_fecha = max(0, 1 - (max_dias / 30))

        # 3. Score por productos - En multi-remisión usamos score neutral (1.0)
        # porque es común que la factura tenga productos agregados mientras
        # las remisiones tienen productos individuales de cada entrega
        score_productos = 1.0

        # Penalización pequeña por usar múltiples remisiones (preferir menos remisiones)
        penalizacion_multi = 0.02 * (len(remisiones) - 1)

        # Calcular score total
        score_total = (
            score_monto * self.PESO_MONTO +
            score_fecha * self.PESO_FECHA +
            score_productos * self.PESO_PRODUCTOS
        ) - penalizacion_multi

        # Agregar detalles
        nums = ', '.join(r.id_remision for r in remisiones)
        detalles.append(f"COMBINACION: {len(remisiones)} remisiones ({nums})")

        if diferencia_porcentaje > settings.tolerancia_monto_porcentaje:
            detalles.append(
                f"ALERTA_MONTO: Diferencia combinada ${diferencia_monto:,.2f} ({diferencia_porcentaje:.2f}%)"
            )

        if max_dias > settings.dias_alerta_desfase:
            detalles.append(
                f"FECHA_DESFASADA: Rango de {max_dias} días entre remisiones y factura"
            )

        return MatchScore(
            score_total=max(0, score_total),
            score_monto=score_monto,
            score_fecha=score_fecha,
            score_productos=score_productos,
            diferencia_monto=diferencia_monto,
            diferencia_porcentaje=diferencia_porcentaje,
            dias_diferencia=dias_diferencia,
            detalles=detalles,
            es_multi_remision=True,
            remisiones=remisiones,
        )

    def _calcular_score_productos_lista(self, factura: Factura, detalles: list) -> float:
        """Calcular score de productos usando lista de detalles"""
        if not factura.conceptos or not detalles:
            return 0.5

        matches_encontrados = 0
        total_conceptos = len(factura.conceptos)

        for concepto in factura.conceptos:
            mejor_match = 0
            for detalle in detalles:
                ratio = fuzz.token_sort_ratio(
                    concepto.descripcion.lower(),
                    detalle.descripcion_producto.lower()
                )
                mejor_match = max(mejor_match, ratio)

            if mejor_match >= self.umbral_similitud:
                matches_encontrados += 1

        return matches_encontrados / total_conceptos if total_conceptos > 0 else 0.5

    def conciliar_lote(self, facturas: List[Factura]) -> List[ResultadoConciliacion]:
        """
        Conciliar un lote de facturas usando asignación óptima.

        Usa el algoritmo húngaro para encontrar la asignación global óptima
        entre facturas y remisiones, evitando el problema de "primer llegado".

        Args:
            facturas: Lista de facturas a conciliar

        Returns:
            Lista de resultados de conciliación
        """
        # Resetear registro de remisiones usadas al inicio del lote
        self._remisiones_usadas.clear()

        total = len(facturas)
        logger.info(f"Iniciando conciliación ÓPTIMA de {total} facturas")

        # FASE 1: Recolectar todas las candidatas para cada factura
        logger.info("Fase 1: Recolectando remisiones candidatas...")
        candidatas_por_factura: Dict[str, List[Tuple[Remision, MatchScore]]] = {}
        todas_remisiones: Dict[str, List[Factura]] = {}  # remision_id -> facturas que la quieren

        for factura in facturas:
            # Buscar remisiones candidatas
            remisiones = self.repository.buscar_para_conciliacion(
                rfc_proveedor=factura.rfc_emisor,
                fecha_factura=factura.fecha_emision,
                monto_total=factura.total,
                dias_rango=self.dias_rango
            )

            if not remisiones:
                continue

            # Calcular scores para cada remisión candidata
            candidatas = []
            for remision in remisiones:
                score = self._calcular_score(factura, remision)
                # Solo considerar si tiene monto exacto
                if score.diferencia_monto == Decimal('0.00'):
                    candidatas.append((remision, score))
                    # Registrar qué facturas quieren esta remisión
                    if remision.id_remision not in todas_remisiones:
                        todas_remisiones[remision.id_remision] = []
                    todas_remisiones[remision.id_remision].append(factura)

            # También buscar combinaciones multi-remisión
            match_multi = self._buscar_combinacion_remisiones(factura, remisiones)
            if match_multi and match_multi.diferencia_monto == Decimal('0.00'):
                candidatas.append((match_multi.remisiones, match_multi))

            if candidatas:
                candidatas_por_factura[factura.uuid] = candidatas

        facturas_con_candidatas = [f for f in facturas if f.uuid in candidatas_por_factura]
        facturas_sin_candidatas = [f for f in facturas if f.uuid not in candidatas_por_factura]

        # Identificar remisiones en conflicto (más de una factura las quiere)
        remisiones_en_conflicto = {rid: fs for rid, fs in todas_remisiones.items() if len(fs) > 1}
        if remisiones_en_conflicto:
            logger.info(f"  - Remisiones en conflicto: {len(remisiones_en_conflicto)} "
                       f"(afectan {sum(len(fs) for fs in remisiones_en_conflicto.values())} facturas)")

        logger.info(f"  - {len(facturas_con_candidatas)} facturas con candidatas exactas")
        logger.info(f"  - {len(facturas_sin_candidatas)} facturas sin candidatas exactas")

        # FASE 2: Resolver asignación óptima usando algoritmo húngaro
        logger.info("Fase 2: Resolviendo asignación óptima...")
        asignaciones = self._resolver_asignacion_optima(facturas_con_candidatas, candidatas_por_factura)

        # Registrar remisiones asignadas
        for factura_uuid, (remision_o_lista, _) in asignaciones.items():
            if isinstance(remision_o_lista, list):
                for rem in remision_o_lista:
                    self._remisiones_usadas.add(rem.id_remision)
            else:
                self._remisiones_usadas.add(remision_o_lista.id_remision)

        logger.info(f"  - Asignaciones óptimas: {len(asignaciones)}")
        logger.info(f"  - Remisiones reservadas: {len(self._remisiones_usadas)}")

        # FASE 3: Aplicar asignaciones y generar resultados
        logger.info("Fase 3: Aplicando asignaciones...")
        resultados = []

        # Procesar facturas con asignación óptima
        for factura in facturas_con_candidatas:
            resultado = ResultadoConciliacion(
                uuid_factura=factura.uuid,
                identificador_factura=factura.identificador,
                rfc_emisor=factura.rfc_emisor,
                nombre_emisor=factura.nombre_emisor,
                fecha_factura=factura.fecha_emision,
                total_factura=factura.total,
            )

            if factura.uuid in asignaciones:
                remision_o_lista, score = asignaciones[factura.uuid]

                if isinstance(remision_o_lista, list):
                    self._aplicar_resultado_multi(resultado, score, factura)
                else:
                    remision = remision_o_lista
                    resultado.remision = remision
                    resultado.remisiones = [remision]
                    resultado.numero_remision = remision.id_remision
                    resultado.fecha_remision = remision.fecha_remision
                    resultado.total_remision = remision.total
                    resultado.score_matching = score.score_total
                    resultado.diferencia_monto = score.diferencia_monto
                    resultado.diferencia_porcentaje = score.diferencia_porcentaje
                    resultado.es_multi_remision = False
                    resultado.conciliacion_exitosa = True
            else:
                # No recibió asignación óptima: las remisiones fueron asignadas a otras facturas
                # Intentar búsqueda normal que puede encontrar otras opciones
                resultado_fallback = self.conciliar_factura(factura)
                if resultado_fallback.conciliacion_exitosa:
                    resultado = resultado_fallback
                else:
                    # No hubo match alternativo
                    resultado.alertas.append(
                        "SIN_REMISION_DISPONIBLE: Las remisiones exactas fueron asignadas a otras facturas"
                    )
                    if resultado_fallback.remision:
                        # Hay una remisión pero con diferencia
                        resultado = resultado_fallback

            resultados.append(resultado)

        # Procesar facturas sin candidatas exactas
        for factura in facturas_sin_candidatas:
            resultado = self.conciliar_factura(factura)
            resultados.append(resultado)

        # Resumen
        exitosos = sum(1 for r in resultados if r.conciliacion_exitosa)
        con_diferencias = sum(1 for r in resultados if r.remision and not r.conciliacion_exitosa)
        sin_remision = sum(1 for r in resultados if not r.remision and not r.remisiones)

        logger.info(
            f"Conciliación ÓPTIMA completada: {exitosos} exitosos, "
            f"{con_diferencias} con diferencias, {sin_remision} sin remisión"
        )

        return resultados

    def _resolver_asignacion_optima(
        self,
        facturas: List[Factura],
        candidatas_por_factura: Dict[str, List[Tuple[Remision, MatchScore]]]
    ) -> Dict[str, Tuple[any, MatchScore]]:
        """
        Resolver el problema de asignación óptima usando el algoritmo húngaro.

        Cada remisión individual tiene una columna única en la matriz.
        Para remisiones repetidas con mismo monto, cada una es una columna separada.
        Esto permite que múltiples facturas de $2,112 se asignen a diferentes
        remisiones de $2,112.

        Args:
            facturas: Lista de facturas a asignar
            candidatas_por_factura: Diccionario de candidatas por UUID de factura

        Returns:
            Diccionario de asignaciones {uuid_factura: (remision_o_lista, score)}
        """
        if not facturas:
            return {}

        # Recolectar TODAS las remisiones (cada una como columna separada)
        # Usar índice único para cada remisión individual
        columnas = []  # Lista de (id_unico, remision_o_lista, score_base)

        # Primero recolectar todas las remisiones individuales únicas
        remisiones_vistas: Set[str] = set()

        for factura in facturas:
            if factura.uuid not in candidatas_por_factura:
                continue
            for remision_o_lista, score in candidatas_por_factura[factura.uuid]:
                if isinstance(remision_o_lista, list):
                    # Multi-remisión: crear ID único basado en sus componentes
                    rem_id = "MULTI_" + "_".join(sorted(r.id_remision for r in remision_o_lista))
                    if rem_id not in remisiones_vistas:
                        remisiones_vistas.add(rem_id)
                        columnas.append((rem_id, remision_o_lista, score))
                else:
                    # Remisión simple: cada una es única
                    rem_id = remision_o_lista.id_remision
                    if rem_id not in remisiones_vistas:
                        remisiones_vistas.add(rem_id)
                        columnas.append((rem_id, remision_o_lista, score))

        if not columnas:
            return {}

        n_facturas = len(facturas)
        n_columnas = len(columnas)

        logger.debug(f"Matriz de asignación: {n_facturas} facturas x {n_columnas} remisiones")

        # Crear matriz de costos (facturas x columnas)
        COSTO_ALTO = 1000000
        matriz_costos = np.full((n_facturas, n_columnas), COSTO_ALTO, dtype=float)

        # Mapeo de facturas a índices
        factura_idx = {f.uuid: i for i, f in enumerate(facturas)}

        # Mapeo de IDs de columna a índices
        columna_idx = {col[0]: i for i, col in enumerate(columnas)}

        # Llenar costos: para cada factura, marcar sus candidatas
        for factura in facturas:
            if factura.uuid not in candidatas_por_factura:
                continue
            f_idx = factura_idx[factura.uuid]

            for remision_o_lista, score in candidatas_por_factura[factura.uuid]:
                if isinstance(remision_o_lista, list):
                    rem_id = "MULTI_" + "_".join(sorted(r.id_remision for r in remision_o_lista))
                else:
                    rem_id = remision_o_lista.id_remision

                if rem_id in columna_idx:
                    c_idx = columna_idx[rem_id]
                    # Costo = -(score) + penalización por días
                    # Menor costo = mejor match
                    costo = -score.score_total + (score.dias_diferencia * 0.001)
                    matriz_costos[f_idx, c_idx] = costo

        # Resolver asignación óptima con algoritmo húngaro
        try:
            filas_asignadas, cols_asignadas = linear_sum_assignment(matriz_costos)
        except Exception as e:
            logger.error(f"Error en asignación óptima: {e}")
            return {}

        # Construir diccionario de asignaciones
        asignaciones = {}
        remisiones_usadas_en_asignacion: Set[str] = set()

        for f_idx, c_idx in zip(filas_asignadas, cols_asignadas):
            # Verificar que el costo no sea el alto (significa que sí hay match válido)
            if matriz_costos[f_idx, c_idx] >= COSTO_ALTO - 1:
                continue

            factura = facturas[f_idx]
            rem_id, remision_o_lista, _ = columnas[c_idx]

            # Verificar que las remisiones no estén ya usadas
            # (esto es importante para multi-remisiones que comparten componentes)
            if rem_id.startswith("MULTI_"):
                rem_ids_individuales = set(rem_id.replace("MULTI_", "").split("_"))
                if rem_ids_individuales & remisiones_usadas_en_asignacion:
                    # Hay solapamiento, saltar
                    continue
                remisiones_usadas_en_asignacion.update(rem_ids_individuales)
            else:
                if rem_id in remisiones_usadas_en_asignacion:
                    continue
                remisiones_usadas_en_asignacion.add(rem_id)

            # Buscar el score específico para esta factura
            for rem_o_lista, score in candidatas_por_factura[factura.uuid]:
                if isinstance(rem_o_lista, list):
                    check_id = "MULTI_" + "_".join(sorted(r.id_remision for r in rem_o_lista))
                else:
                    check_id = rem_o_lista.id_remision

                if check_id == rem_id:
                    asignaciones[factura.uuid] = (remision_o_lista, score)
                    break

        logger.info(f"  - Asignaciones óptimas encontradas: {len(asignaciones)}")
        return asignaciones

    def _calcular_score(self, factura: Factura, remision: Remision) -> MatchScore:
        """
        Calcular puntuación de coincidencia entre factura y remisión

        Args:
            factura: Factura a evaluar
            remision: Remisión candidata

        Returns:
            MatchScore con los detalles de la evaluación
        """
        detalles = []

        # 1. Score por monto
        diferencia_monto = abs(factura.total - remision.total)
        if factura.total > 0:
            diferencia_porcentaje = float((diferencia_monto / factura.total) * 100)
        else:
            diferencia_porcentaje = 100.0

        if diferencia_porcentaje <= settings.tolerancia_monto_porcentaje:
            score_monto = 1.0
        elif diferencia_porcentaje <= settings.tolerancia_monto_porcentaje * 2:
            score_monto = 0.7
        elif diferencia_porcentaje <= 10:
            score_monto = 0.5
        else:
            score_monto = max(0, 1 - (diferencia_porcentaje / 50))

        if diferencia_porcentaje > settings.tolerancia_monto_porcentaje:
            detalles.append(
                f"ALERTA_MONTO: Diferencia de ${diferencia_monto:,.2f} ({diferencia_porcentaje:.2f}%)"
            )

        # 2. Score por fecha
        dias_diferencia = abs((factura.fecha_emision - remision.fecha_remision).days)

        if dias_diferencia <= 1:
            score_fecha = 1.0
        elif dias_diferencia <= 3:
            score_fecha = 0.9
        elif dias_diferencia <= 7:
            score_fecha = 0.7
        elif dias_diferencia <= 14:
            score_fecha = 0.5
        else:
            score_fecha = max(0, 1 - (dias_diferencia / 30))

        if dias_diferencia > settings.dias_alerta_desfase:
            detalles.append(
                f"FECHA_DESFASADA: {dias_diferencia} días de diferencia entre factura y remisión"
            )

        # 3. Score por productos (matching difuso)
        score_productos = self._calcular_score_productos(factura, remision)

        # Calcular score total ponderado
        score_total = (
            score_monto * self.PESO_MONTO +
            score_fecha * self.PESO_FECHA +
            score_productos * self.PESO_PRODUCTOS
        )

        return MatchScore(
            score_total=score_total,
            score_monto=score_monto,
            score_fecha=score_fecha,
            score_productos=score_productos,
            diferencia_monto=diferencia_monto,
            diferencia_porcentaje=diferencia_porcentaje,
            dias_diferencia=dias_diferencia,
            detalles=detalles,
        )

    def _calcular_score_productos(self, factura: Factura, remision: Remision) -> float:
        """
        Calcular score de similitud entre productos de factura y remisión

        Args:
            factura: Factura con conceptos
            remision: Remisión con detalles

        Returns:
            Score de similitud de productos (0-1)
        """
        if not factura.conceptos or not remision.detalles:
            return 0.5  # Score neutro si no hay detalles

        # Comparar descripciones usando fuzzy matching
        matches_encontrados = 0
        total_conceptos = len(factura.conceptos)

        for concepto in factura.conceptos:
            mejor_match = 0
            for detalle in remision.detalles:
                ratio = fuzz.token_sort_ratio(
                    concepto.descripcion.lower(),
                    detalle.descripcion_producto.lower()
                )
                mejor_match = max(mejor_match, ratio)

            if mejor_match >= self.umbral_similitud:
                matches_encontrados += 1

        if total_conceptos > 0:
            return matches_encontrados / total_conceptos

        return 0.5

    def detectar_remisiones_duplicadas(
        self,
        resultados: List[ResultadoConciliacion]
    ) -> List[str]:
        """
        Detectar si una misma remisión está vinculada a múltiples facturas

        Args:
            resultados: Lista de resultados de conciliación

        Returns:
            Lista de alertas de duplicados
        """
        alertas = []
        remisiones_usadas = {}

        for resultado in resultados:
            if resultado.numero_remision:
                if resultado.numero_remision in remisiones_usadas:
                    alertas.append(
                        f"REMISION_DUPLICADA: Remisión {resultado.numero_remision} "
                        f"vinculada a facturas {remisiones_usadas[resultado.numero_remision]} "
                        f"y {resultado.uuid_factura}"
                    )
                else:
                    remisiones_usadas[resultado.numero_remision] = resultado.uuid_factura

        return alertas
