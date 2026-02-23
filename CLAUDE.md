# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Automated reconciliation agent that matches Mexican electronic invoices (CFDI from SAT) with shipment/receipt records (remisiones) from the SAV7 ERP system. The agent parses XML invoices, queries the SQL Server database, performs intelligent matching, and generates Excel reports with discrepancy alerts.

## Commands

```bash
# Install dependencies
pip install -r agente-conciliacion-sat/requirements.txt

# Run reconciliation (procesa todos los XML en data/xml_facturas/)
python agente-conciliacion-sat/main.py

# Test database connection
python agente-conciliacion-sat/main.py --test-conexion

# Explore database structure
python agente-conciliacion-sat/main.py --explorar

# Process specific XML file
python agente-conciliacion-sat/main.py --archivo path/to/factura.xml

# Dry run (no database writes)
python agente-conciliacion-sat/main.py --dry-run --verbose

# Sync from Google Drive and process
python agente-conciliacion-sat/main.py --sync-drive

# Configure Google Drive interactively
python agente-conciliacion-sat/main.py --config-drive

# Scheduled execution
python agente-conciliacion-sat/scheduler.py --hora 07:00

# Scheduler: run once and exit (ideal for Windows Task Scheduler)
python agente-conciliacion-sat/scheduler.py --una-vez

# Scheduler: run every N minutes
python agente-conciliacion-sat/scheduler.py --intervalo 30

# Scheduler: watch input folder for new XMLs
python agente-conciliacion-sat/scheduler.py --monitorear

# Run tests
python agente-conciliacion-sat/tests/test_xml_parser.py

# Build Windows executable
python agente-conciliacion-sat/build_exe.py
```

### Windows Scripts

- `ejecutar_ahora.bat` — Ejecutar el agente manualmente (doble clic)
- `instalar_servicio.bat` — Instalar dependencias, crear .env, registrar tarea programada de Windows (requiere admin)

## Architecture

```
agente-conciliacion-sat/
├── main.py                    # Entry point - orchestrates the reconciliation flow
├── scheduler.py               # AgentScheduler - scheduled/continuous execution with error tracking
├── build_exe.py               # PyInstaller build script for Windows .exe
├── ejecutar_ahora.bat         # Windows: run agent manually
├── instalar_servicio.bat      # Windows: install as scheduled task
├── config/
│   ├── settings.py            # Dataclass config (Settings, SAV7Config)
│   ├── database.py            # SQL Server connection (DatabaseConnection with context manager)
│   └── GOOGLE_DRIVE_SETUP.md  # Instrucciones para configurar Google Drive
└── src/
    ├── sat/                   # SAT/CFDI Processing
    │   ├── xml_parser.py      # CFDIParser - parses CFDI 4.0/3.3 XML
    │   ├── models.py          # Factura, Concepto, TipoComprobante, MetodoPago
    │   └── sat_downloader.py  # Download from SAT with FIEL credentials (cfdiclient)
    ├── erp/                   # SAV7 ERP Integration
    │   ├── sav7_connector.py  # SAV7Connector + SAV7Explorer for DB discovery
    │   ├── remisiones.py      # RemisionesRepository - 4 search strategies
    │   ├── models.py          # Remision, DetalleRemision, ResultadoConciliacion
    │   └── consolidacion.py   # ConsolidadorSAV7 - creates Serie='F', numero_a_letra()
    ├── conciliacion/          # Matching Engine
    │   ├── matcher.py         # ConciliacionMatcher - scoring + Hungarian algorithm
    │   ├── validator.py       # ConciliacionValidator - business rule validation
    │   └── alerts.py          # AlertManager - registro centralizado de alertas
    ├── cfdi/                  # CFDI Post-processing
    │   ├── attachment_manager.py  # AttachmentManager - copia XML/PDF al share de red SAV7
    │   └── pdf_generator.py       # PDFGenerator - genera PDF con satcfdi (Python 3.10+)
    ├── reports/
    │   └── excel_generator.py # ExcelReportGenerator - 6 sheets Excel + CSV
    ├── drive/
    │   └── sync.py            # DriveSync - Google Drive (Service Account + OAuth)
    └── pdf/
        ├── extractor.py       # PDFRemisionExtractor + PDFIndexer (PyMuPDF/pdfplumber)
        └── __init__.py
```

## Data Flow (Scheduler - Ejecucion Diaria)

0. **SAT Download**: SATDownloader descarga XMLs via FIEL (ultimos 3 dias, solo vigentes) a `data/xml_facturas/`
1. **Input**: XML invoices in `data/xml_facturas/` (with optional matching PDFs)
2. **Parse**: CFDIParser extracts UUID, RFC, amounts, line items from CFDI
3. **PDF Lookup**: PDFIndexer busca PDF companion por UUID; PDFRemisionExtractor extrae numeros de remision/OC
4. **Query**: RemisionesRepository finds matching Serie='R' records (prioridad: PDF -> numero directo -> heuristico)
5. **Match**: ConciliacionMatcher scores matches (100% = perfect, accounts for multi-remision up to 10)
6. **Validate**: ConciliacionValidator aplica reglas de negocio, AlertManager registra alertas
7. **Consolidate**: ConsolidadorSAV7 creates Serie='F' records, marks originals as 'Consolidada'
8. **Attach**: AttachmentManager copia XML/PDF al share de red SAV7, actualiza campos FacturaElectronica
9. **Report**: Excel con 6 sheets: Resumen, Exitosas, Diferencias, Sin Remision, Alertas, Detalle

## Key Database Tables (SAV7)

- **SAVRecC**: Receipt headers (Serie='R' for remisiones, Serie='F' for consolidated invoices)
- **SAVRecD**: Receipt line items
- **SAVProveedor**: Supplier catalog

Important fields:
- `TimbradoFolioFiscal`: UUID of the CFDI (solo en Serie F)
- `Estatus`: 'RECIBIDA'/'No Pagada' (pending) or 'Consolidada' (matched)
- `Consolida`: BIT flag (use 1, not 'F')
- `Consolidacion`: BIT flag (1 solo en Serie F, 0 en Serie R)
- `FacturaElectronica`: Nombre del archivo XML adjunto
- `FacturaElectronicaExiste`: BIT (1 si tiene adjunto)
- `FacturaElectronicaValida`: BIT (1 si el XML es valido)
- `FacturaElectronicaEstatus`: 'Vigente' cuando esta adjunto

## Configuration

Environment variables in `.env` (copy from `.env.example`):
```env
# --- Base de Datos SAV7 (SQL Server) ---
DB_SERVER=localhost
DB_PORT=1433
DB_DATABASE=DBSAV71A
DB_USERNAME=devsav7
DB_PASSWORD=devsav7
DB_DRIVER={SQL Server Native Client 11.0}
DB_TRUSTED_CONNECTION=false
DB_TIMEOUT=30

# --- Conciliacion ---
TOLERANCIA_MONTO=2.0          # % de tolerancia en montos
DIAS_RANGO_BUSQUEDA=3         # Dias +/- para buscar remisiones
DIAS_ALERTA_DESFASE=7         # Alertar si fecha difiere mas de N dias
UMBRAL_SIMILITUD=80           # % minimo para matching difuso de productos

# --- Logging ---
LOG_LEVEL=INFO

# --- Adjuntos CFDI ---
CFDI_ADJUNTOS_DIR=\\SERVERMC\Asesoft\SAV7-1\Recepciones CFDI
CFDI_ADJUNTOS_HABILITADO=true
CFDI_GENERAR_PDF=true         # Requiere satcfdi (Python 3.10+)

# --- Tablas SAV7 (override si difieren) ---
SAV7_TABLA_REMISIONES=SAVRecC
SAV7_TABLA_DETALLE=SAVRecD
SAV7_TABLA_PROVEEDORES=SAVProveedor

# --- Descarga automatica SAT (requiere FIEL) ---
FIEL_CER_PATH=C:/Tools/certs/fiel/certificado.cer
FIEL_KEY_PATH=C:/Tools/certs/fiel/llave_privada.key
FIEL_PASSWORD=contrasena
FIEL_RFC=DCM02072238A

# --- Descarga SAT ---
DIAS_DESCARGA_SAT=3           # Dias hacia atras para descargar del SAT
```

## Claude Code Setup

Para que otro desarrollador pueda usar Claude Code con este proyecto:

### 1. Configurar MCP SQL Server
```bash
cp .mcp.json.example .mcp.json
# Editar .mcp.json con las credenciales reales de SQL Server
```

### 2. Configurar permisos de Claude Code (opcional)
```bash
mkdir -p .claude
cp .claude/settings.local.json.example .claude/settings.local.json
```
Esto preaprueba permisos para MCP SQL, python, pip y git. Sin este archivo Claude Code pedira confirmacion manual en cada operacion.

### 3. Configurar variables de entorno
```bash
cp agente-conciliacion-sat/.env.example agente-conciliacion-sat/.env
# Editar .env con las credenciales de BD
```

### 4. Instalar dependencias
```bash
pip install -r agente-conciliacion-sat/requirements.txt
```

**Requisito**: Acceso via Tailscale al servidor `100.73.181.41` (SQL Server).

## MCP Database Access

The project includes MCP server configuration (`.mcp.json`) for direct SQL Server access:
- Server: `sqlserver-PRUEBAS`
- Database: `DBSAV71A` (test environment)
- Use MCP tools to query tables for debugging/verification

### Querying Production Database (DBSAV71)

The MCP connection defaults to `DBSAV71A`. To query the **production database** (`DBSAV71`), use fully qualified table names:

```sql
-- Query production database from MCP
SELECT * FROM DBSAV71.dbo.SAVRecC WHERE Serie = 'F' AND NumRec = 68627;

-- Query test database (default)
SELECT * FROM SAVRecC WHERE Serie = 'F' AND NumRec = 68627;
-- Or explicitly:
SELECT * FROM DBSAV71A.dbo.SAVRecC WHERE Serie = 'F' AND NumRec = 68627;
```

**Important:** Always use `DBSAV71.dbo.TableName` when you need to query or verify production data. The agent runs against production (`DBSAV71`) but MCP tools connect to test by default.

## Critical Implementation Details

- **Articulos calculation**: Uses `round()` to match production behavior (199.8 -> 200)
- **Paridad field**: Always 20.00 for MXN (as used in production)
- **NumOC**: Required field, use 0 as default
- **Multi-remision**: Single invoice can match up to 10 remisiones (sum must equal total, uses `itertools.combinations`)
- **Solo match exacto**: Diferencia de $0.00 para consolidar automaticamente (tolerancia solo para candidatos)
- **Draft detection**: Look for `drafts.` prefix on document IDs when needed
- **UUID in remision**: Do NOT store UUID in remision's TimbradoFolioFiscal field (only in factura F)
- **Consolidacion field**: Do NOT set Consolidacion=1 in remision (only set Consolida=1)
- **CodProv audit trail**: Al copiar detalles a Serie F, CodProv se marca como "R-XXXXX PN" para trazabilidad
- **Adjuntos no-bloqueantes**: Si AttachmentManager falla, la consolidacion sigue exitosa

### Matching Algorithm - Search Priority

1. **PDF companion**: Busca PDF por UUID index, extrae numeros de remision/OC con regex
2. **Numero directo**: Si el XML indica remision en CondicionesDePago o descripcion de conceptos
3. **Heuristico**: RFC + ventana de fecha + scoring (Monto 50%, Fecha 30%, Productos 20%)

### Matching Algorithm - Batch Optimization

1. Recolectar candidatos y scores por cada factura
2. Construir matriz de costo, resolver asignacion optima con algoritmo hungaro (`scipy.optimize.linear_sum_assignment`)
3. Aplicar asignaciones; fallback a matching individual para facturas sin asignar
4. Set `_remisiones_usadas` previene la misma remision asignada a multiples facturas

### AttachmentManager - Naming Convention

Archivos copiados al share de red SAV7:
```
{RFC_EMISOR}_REC_F{NUMREC:06d}_{YYYYMMDD}.xml
{RFC_EMISOR}_REC_F{NUMREC:06d}_{YYYYMMDD}.pdf
```
Ejemplo: `SIS850415B31_REC_F068590_20260206.xml`

### Dependencias Opcionales

Estas dependencias se degradan gracefully (el feature se deshabilita con warning):
- `satcfdi` - Generacion de PDF desde XML (requiere Python 3.10+)
- `cfdiclient>=1.6.0` - Descarga automatica de CFDI del SAT via SOAP (v1.6 usa `SolicitaDescargaRecibidos`)
- `pymupdf` / `pdfplumber` - Extraccion de texto de PDFs
- `google-api-python-client` - Sincronizacion con Google Drive

## Key Classes Reference

| Clase | Archivo | Responsabilidad |
|-------|---------|-----------------|
| `CFDIParser` | `src/sat/xml_parser.py` | Parsea CFDI 3.3/4.0 XML, extrae datos fiscales |
| `Factura` | `src/sat/models.py` | Modelo de factura SAT (UUID, RFC, montos, conceptos) |
| `RemisionesRepository` | `src/erp/remisiones.py` | Queries de lectura contra SAV7 (4 estrategias de busqueda) |
| `ConsolidadorSAV7` | `src/erp/consolidacion.py` | Escribe en BD: crea Serie F, copia detalles, marca Serie R |
| `ConciliacionMatcher` | `src/conciliacion/matcher.py` | Motor de matching con algoritmo hungaro para lotes |
| `ConciliacionValidator` | `src/conciliacion/validator.py` | Validacion de reglas de negocio post-matching |
| `AlertManager` | `src/conciliacion/alerts.py` | Registro centralizado de alertas (CRITICA/ALTA/MEDIA/BAJA/INFO) |
| `AttachmentManager` | `src/cfdi/attachment_manager.py` | Copia XML/PDF al share de red, actualiza campos FacturaElectronica |
| `PDFGenerator` | `src/cfdi/pdf_generator.py` | Genera PDF desde XML con satcfdi |
| `PDFRemisionExtractor` | `src/pdf/extractor.py` | Extrae numeros de remision/OC desde PDFs |
| `PDFIndexer` | `src/pdf/extractor.py` | Indice UUID->PDF para busqueda rapida |
| `ExcelReportGenerator` | `src/reports/excel_generator.py` | Reporte Excel 6 hojas + CSV |
| `DriveSync` | `src/drive/sync.py` | Sincronizacion Google Drive (Service Account + OAuth) |
| `AgentScheduler` | `scheduler.py` | Ejecucion programada con tracking de errores |
| `DatabaseConnection` | `config/database.py` | Wrapper pyodbc con context manager (auto commit/rollback) |
| `Settings` | `config/settings.py` | Configuracion global desde .env con auto-creacion de directorios |

## Ejecucion Programada (Produccion)

El agente se ejecuta diariamente a las 6:00 AM via Windows Task Scheduler en el servidor:

```
Programa: C:\Tools\Agente2\agente-conciliacion-satCorrecto\venv\Scripts\python.exe
Argumentos: scheduler.py --una-vez
Iniciar en: C:\Tools\Agente2\agente-conciliacion-satCorrecto
```

**Flujo automatico:**
1. Descarga XMLs del SAT (ultimos 3 dias, solo `EstadoComprobante='Vigente'`)
2. Procesa y concilia contra remisiones Serie R
3. Consolida matches exactos ($0.00 diferencia) creando Serie F
4. Genera reporte Excel
5. Termina

**Nota:** `main.py` NO descarga del SAT — solo procesa XMLs locales. La descarga solo ocurre via `scheduler.py`.

### Compatibilidad cfdiclient v1.6

La libreria `cfdiclient>=1.6.0` cambio su API respecto a versiones anteriores:
- `SolicitaDescarga` → `SolicitaDescargaRecibidos` (clase separada para recibidos)
- `solicitar_descarga()` acepta `datetime` directamente (no strings)
- `Fiel` no tiene atributo `.rfc` — el RFC se configura via `FIEL_RFC` en `.env`
- `estado_comprobante='Vigente'` es obligatorio (SAT ya no permite descargar cancelados)
- El SAT limita a 2 solicitudes con los mismos parametros antes de bloquear

### Certificados FIEL en servidor

```
C:\Tools\certs\fiel\
├── certificado.cer        # DER format (para cfdiclient)
├── certificado.pem        # PEM format (NO usar con cfdiclient)
├── llave_privada.key      # DER format (para cfdiclient)
└── llave_privada.pem      # PEM format (NO usar con cfdiclient)
```

**Importante:** cfdiclient requiere archivos en formato DER (`.cer`, `.key`), NO PEM (`.pem`).

## Additional Documentation

- `DEPLOYMENT.md` — Guia de despliegue en servidor Windows
- `QUERIES_REVERTIR_REMISIONES.md` — Templates SQL para revertir consolidaciones en BD de prueba
- `config/GOOGLE_DRIVE_SETUP.md` — Instrucciones para configurar Google Drive

## Testing & Reversal Queries

When testing consolidation, use these queries to revert changes in the test database (DBSAV71A).

### Revert a consolidation (delete factura F and restore remisión R)

```sql
-- ============================================
-- REVERT CONSOLIDATION - TEMPLATE
-- Replace {F_NUM} with factura number (e.g., 67766)
-- Replace {R_NUM} with remisión number (e.g., 45503)
-- ============================================

-- 1. Delete invoice details (SAVRecD Serie='F')
DELETE FROM SAVRecD
WHERE Serie = 'F' AND NumRec = {F_NUM};

-- 2. Delete invoice header (SAVRecC Serie='F')
DELETE FROM SAVRecC
WHERE Serie = 'F' AND NumRec = {F_NUM};

-- 3. Restore remisión to original state (SAVRecC Serie='R')
UPDATE SAVRecC
SET
    Estatus = 'No Pagada',
    Consolidacion = 0,
    Consolida = 0,
    ConsolidaSerie = '',
    ConsolidaNumRec = 0,
    TimbradoFolioFiscal = '',
    CancelacionFecha = NULL,
    CancelacionCapturo = '',
    CancelacionMotivo = ''
WHERE Serie = 'R' AND NumRec = {R_NUM};

-- 4. Verify cleanup
SELECT 'Factura F-{F_NUM}' as Verificacion, COUNT(*) as Registros
FROM SAVRecC WHERE Serie = 'F' AND NumRec = {F_NUM}
UNION ALL
SELECT 'Detalles F-{F_NUM}', COUNT(*)
FROM SAVRecD WHERE Serie = 'F' AND NumRec = {F_NUM}
UNION ALL
SELECT 'Remisión R-{R_NUM} Estatus', 1
FROM SAVRecC WHERE Serie = 'R' AND NumRec = {R_NUM} AND Estatus = 'No Pagada';
```

### Compare test vs production

```sql
-- Compare factura in TEST vs PRODUCTION
-- Replace {UUID} with the CFDI UUID

-- Factura header comparison
SELECT 'TEST' as DB, Serie, NumRec, Total, Articulos, TimbradoFolioFiscal
FROM DBSAV71A.dbo.SAVRecC
WHERE TimbradoFolioFiscal = '{UUID}' AND Serie = 'F'
UNION ALL
SELECT 'PROD', Serie, NumRec, Total, Articulos, TimbradoFolioFiscal
FROM DBSAV71.dbo.SAVRecC
WHERE TimbradoFolioFiscal = '{UUID}' AND Serie = 'F';

-- Remisión comparison
SELECT 'TEST' as DB, Serie, NumRec, Estatus, Consolida, Consolidacion, TimbradoFolioFiscal
FROM DBSAV71A.dbo.SAVRecC
WHERE Serie = 'R' AND NumRec = {R_NUM}
UNION ALL
SELECT 'PROD', Serie, NumRec, Estatus, Consolida, Consolidacion, TimbradoFolioFiscal
FROM DBSAV71.dbo.SAVRecC
WHERE Serie = 'R' AND NumRec = {R_NUM};
```

### Expected differences between TEST and PRODUCTION

After consolidation, these fields should match production:

| Table | Field | Expected Value |
|-------|-------|----------------|
| SAVRecC (F) | TimbradoFolioFiscal | UUID ✓ |
| SAVRecC (F) | Articulos | round(cantidad) ✓ |
| SAVRecC (R) | Estatus | 'Consolidada' ✓ |
| SAVRecC (R) | Consolida | 1 (true) ✓ |
| SAVRecC (R) | Consolidacion | 0 (false) ✓ |
| SAVRecC (R) | TimbradoFolioFiscal | NULL/empty ✓ |
