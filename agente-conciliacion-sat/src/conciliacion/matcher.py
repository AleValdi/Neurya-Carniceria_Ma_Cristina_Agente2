"""
Algoritmo de matching para conciliación de facturas con remisiones
Soporta matching 1:1 y 1:N (una factura a múltiples remisiones)
Soporta matching por PDF de proveedor (cuando incluye números de remisión)
"""
from typing import List, Optional, Tuple
from datetime import datetime, timedelta
from decimal import Decimal
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path
from fuzzywuzzy import fuzz
from loguru import logger

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
        if score.score_total >= self.SCORE_MINIMO_ACEPTABLE:
            if abs(score.diferencia_porcentaje) <= settings.tolerancia_monto_porcentaje:
                resultado.conciliacion_exitosa = True
                logger.info(
                    f"Conciliación exitosa: Factura {factura.uuid} -> "
                    f"Remisión {remision.id_remision} (Score: {score.score_total:.2f})"
                )
            else:
                resultado.alertas.append(
                    f"DIFERENCIA_MONTO: Diferencia de {score.diferencia_porcentaje:.2f}% "
                    f"(${score.diferencia_monto:,.2f})"
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
        if score.score_total >= self.SCORE_MINIMO_ACEPTABLE:
            if abs(score.diferencia_porcentaje) <= settings.tolerancia_monto_porcentaje:
                resultado.conciliacion_exitosa = True
                nums = ', '.join(r.id_remision for r in score.remisiones)
                logger.info(
                    f"Conciliación MULTI exitosa: Factura {factura.uuid} -> "
                    f"Remisiones [{nums}] (Score: {score.score_total:.2f})"
                )
            else:
                resultado.alertas.append(
                    f"DIFERENCIA_MONTO: Diferencia de {score.diferencia_porcentaje:.2f}% "
                    f"(${score.diferencia_monto:,.2f})"
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
        remisiones: List[Remision]
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
        # Aumentado a 15 para permitir encontrar combinaciones de hasta 10 remisiones
        candidatas = candidatas[:min(len(candidatas), 15)]

        # Contar productos del XML
        productos_xml = len(factura.conceptos)
        logger.debug(f"Productos en XML: {productos_xml}")

        mejor_combinacion: Optional[MatchScore] = None
        mejor_con_productos_exactos: Optional[MatchScore] = None
        tolerancia_decimal = factura.total * self.tolerancia_monto

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

                    # PRIORIDAD 1: Si diferencia es exacta (0 o casi 0), retornar inmediatamente
                    if diferencia <= Decimal('0.50'):  # Tolerancia de 50 centavos
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
        Conciliar un lote de facturas

        Args:
            facturas: Lista de facturas a conciliar

        Returns:
            Lista de resultados de conciliación
        """
        resultados = []
        total = len(facturas)

        logger.info(f"Iniciando conciliación de {total} facturas")

        for i, factura in enumerate(facturas, 1):
            logger.info(f"Procesando factura {i}/{total}: {factura.identificador}")
            resultado = self.conciliar_factura(factura)
            resultados.append(resultado)

        # Resumen
        exitosos = sum(1 for r in resultados if r.conciliacion_exitosa)
        con_diferencias = sum(1 for r in resultados if r.remision and not r.conciliacion_exitosa)
        sin_remision = sum(1 for r in resultados if not r.remision)

        logger.info(
            f"Conciliación completada: {exitosos} exitosos, "
            f"{con_diferencias} con diferencias, {sin_remision} sin remisión"
        )

        return resultados

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
