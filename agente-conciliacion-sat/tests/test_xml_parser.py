"""
Tests para el parser de XMLs CFDI
"""
import sys
from pathlib import Path

# Configurar encoding para Windows
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

# Agregar el directorio raíz al path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.sat.xml_parser import CFDIParser
from src.sat.models import Factura, TipoComprobante


# XML de prueba (CFDI 4.0 simplificado)
XML_PRUEBA = """<?xml version="1.0" encoding="UTF-8"?>
<cfdi:Comprobante xmlns:cfdi="http://www.sat.gob.mx/cfd/4"
    xmlns:tfd="http://www.sat.gob.mx/TimbreFiscalDigital"
    xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
    Version="4.0"
    Serie="A"
    Folio="12345"
    Fecha="2024-01-15T10:30:00"
    FormaPago="03"
    SubTotal="10000.00"
    Descuento="0.00"
    Moneda="MXN"
    Total="11600.00"
    TipoDeComprobante="I"
    MetodoPago="PUE"
    LugarExpedicion="06600">

    <cfdi:Emisor
        Rfc="XAXX010101000"
        Nombre="PROVEEDOR DE PRUEBA SA DE CV"
        RegimenFiscal="601"/>

    <cfdi:Receptor
        Rfc="CACX7605101P8"
        Nombre="CARNICERIA MARIA CRISTINA"
        UsoCFDI="G03"
        DomicilioFiscalReceptor="06600"
        RegimenFiscalReceptor="612"/>

    <cfdi:Conceptos>
        <cfdi:Concepto
            ClaveProdServ="10101500"
            Cantidad="100"
            ClaveUnidad="KGM"
            Unidad="Kilogramo"
            Descripcion="CARNE DE RES PRIMERA"
            ValorUnitario="100.00"
            Importe="10000.00"
            ObjetoImp="02">
            <cfdi:Impuestos>
                <cfdi:Traslados>
                    <cfdi:Traslado
                        Base="10000.00"
                        Impuesto="002"
                        TipoFactor="Tasa"
                        TasaOCuota="0.160000"
                        Importe="1600.00"/>
                </cfdi:Traslados>
            </cfdi:Impuestos>
        </cfdi:Concepto>
    </cfdi:Conceptos>

    <cfdi:Impuestos TotalImpuestosTrasladados="1600.00">
        <cfdi:Traslados>
            <cfdi:Traslado
                Base="10000.00"
                Impuesto="002"
                TipoFactor="Tasa"
                TasaOCuota="0.160000"
                Importe="1600.00"/>
        </cfdi:Traslados>
    </cfdi:Impuestos>

    <cfdi:Complemento>
        <tfd:TimbreFiscalDigital
            Version="1.1"
            UUID="ABC12345-6789-0ABC-DEF0-123456789ABC"
            FechaTimbrado="2024-01-15T10:35:00"
            SelloCFD="..."
            NoCertificadoSAT="..."
            SelloSAT="..."/>
    </cfdi:Complemento>

</cfdi:Comprobante>
"""


def test_parser_string():
    """Probar parseo de XML como string"""
    parser = CFDIParser()
    factura = parser.parse_string(XML_PRUEBA)

    assert factura is not None, "Deberia parsear el XML correctamente"
    print("[OK] XML parseado correctamente")

    # Verificar UUID
    assert factura.uuid == "ABC12345-6789-0ABC-DEF0-123456789ABC"
    print(f"[OK] UUID: {factura.uuid}")

    # Verificar serie y folio
    assert factura.serie == "A"
    assert factura.folio == "12345"
    print(f"[OK] Serie/Folio: {factura.identificador}")

    # Verificar emisor
    assert factura.rfc_emisor == "XAXX010101000"
    assert "PROVEEDOR" in factura.nombre_emisor
    print(f"[OK] Emisor: {factura.nombre_emisor} ({factura.rfc_emisor})")

    # Verificar receptor
    assert factura.rfc_receptor == "CACX7605101P8"
    print(f"[OK] Receptor: {factura.nombre_receptor}")

    # Verificar montos
    assert float(factura.subtotal) == 10000.00
    assert float(factura.total) == 11600.00
    assert float(factura.iva_trasladado) == 1600.00
    print(f"[OK] Montos: Subtotal=${factura.subtotal}, IVA=${factura.iva_trasladado}, Total=${factura.total}")

    # Verificar conceptos
    assert len(factura.conceptos) == 1
    concepto = factura.conceptos[0]
    assert "CARNE" in concepto.descripcion.upper()
    assert float(concepto.cantidad) == 100
    print(f"[OK] Conceptos: {len(factura.conceptos)} - {concepto.descripcion}")

    # Verificar tipo de comprobante
    assert factura.tipo_comprobante == TipoComprobante.INGRESO
    print(f"[OK] Tipo: {factura.tipo_comprobante.value}")

    print("\n=== TODAS LAS PRUEBAS PASARON ===")
    return factura


def test_conversion_dict():
    """Probar conversion a diccionario"""
    parser = CFDIParser()
    factura = parser.parse_string(XML_PRUEBA)

    data = factura.to_dict()

    assert 'uuid' in data
    assert 'conceptos' in data
    assert len(data['conceptos']) == 1

    print("[OK] Conversion a diccionario correcta")
    print(f"  Campos: {list(data.keys())}")


if __name__ == "__main__":
    print("=" * 60)
    print("TESTS DEL PARSER DE XML CFDI")
    print("=" * 60)
    print()

    try:
        factura = test_parser_string()
        print()
        test_conversion_dict()
        print()
        print("=" * 60)
        print("=== TODOS LOS TESTS PASARON EXITOSAMENTE ===")
        print("=" * 60)
    except AssertionError as e:
        print(f"\n[FALLO] TEST FALLIDO: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n[ERROR] ERROR: {e}")
        sys.exit(1)
