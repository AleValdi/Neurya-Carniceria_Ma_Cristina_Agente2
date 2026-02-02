# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Automated reconciliation agent that matches Mexican electronic invoices (CFDI from SAT) with shipment/receipt records (remisiones) from the SAV7 ERP system. The agent parses XML invoices, queries the SQL Server database, performs intelligent matching, and generates Excel reports with discrepancy alerts.

## Commands

```bash
# Install dependencies
pip install -r agente-conciliacion-sat/requirements.txt

# Run reconciliation
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

# Scheduled execution
python agente-conciliacion-sat/scheduler.py --hora 07:00

# Run tests
python agente-conciliacion-sat/tests/test_xml_parser.py

# Build Windows executable
python agente-conciliacion-sat/build_exe.py
```

## Architecture

```
agente-conciliacion-sat/
├── main.py                    # Entry point - orchestrates the reconciliation flow
├── scheduler.py               # Task scheduler for automated runs
├── config/
│   ├── settings.py            # Dataclass config (Settings, SAV7Config)
│   └── database.py            # SQL Server connection (pyodbc)
└── src/
    ├── sat/                   # SAT/CFDI Processing
    │   ├── xml_parser.py      # CFDIParser class - parses CFDI 4.0/3.3 XML
    │   ├── models.py          # Factura, Concepto, TipoComprobante
    │   └── sat_downloader.py  # Download from SAT with FIEL credentials
    ├── erp/                   # SAV7 ERP Integration
    │   ├── sav7_connector.py  # Database connection management
    │   ├── remisiones.py      # RemisionesRepository - queries SAVRecC/SAVRecD
    │   ├── models.py          # Remision, DetalleRemision
    │   └── consolidacion.py   # Creates Serie='F' records (consolidated invoices)
    ├── conciliacion/          # Matching Engine
    │   ├── matcher.py         # ConciliacionMatcher - scoring algorithm
    │   ├── validator.py       # Data validation
    │   └── alerts.py          # AlertManager - CRÍTICA/ALTA/MEDIA alerts
    ├── reports/
    │   └── excel_generator.py # Multi-sheet Excel reports with openpyxl
    ├── drive/
    │   └── sync.py            # Google Drive sync
    └── pdf/
        └── extractor.py       # PDF text extraction for remision numbers
```

## Data Flow

1. **Input**: XML invoices in `data/xml_facturas/` (with optional matching PDFs)
2. **Parse**: CFDIParser extracts UUID, RFC, amounts, line items from CFDI
3. **Query**: RemisionesRepository finds matching Serie='R' records by RFC/date/amount
4. **Match**: ConciliacionMatcher scores matches (100% = perfect, accounts for multi-remision)
5. **Consolidate**: Creates Serie='F' records in SAVRecC, marks originals as 'Consolidada'
6. **Report**: Excel with sheets: Resumen, Exitosas, Diferencias, Sin Remisión, Alertas

## Key Database Tables (SAV7)

- **SAVRecC**: Receipt headers (Serie='R' for remisiones, Serie='F' for consolidated invoices)
- **SAVRecD**: Receipt line items
- **SAVProveedor**: Supplier catalog

Important fields:
- `TimbradoFolioFiscal`: UUID of the CFDI
- `Estatus`: 'RECIBIDA' (pending) or 'Consolidada' (matched)
- `Consolida`: BIT flag (use 1, not 'F')

## Configuration

Environment variables in `.env` (copy from `.env.example`):
```env
DB_SERVER=localhost
DB_DATABASE=DBSAV71
DB_USERNAME=user
DB_PASSWORD=pass
DIAS_RANGO_BUSQUEDA=15     # Days ± for matching (default: 15)
TOLERANCIA_MONTO=2.0       # Acceptable % difference (default: 2%)
```

## MCP Database Access

The project includes MCP server configuration (`.mcp.json`) for direct SQL Server access:
- Server: `sqlserver-PRUEBAS`
- Database: `DBSAV71_TEST` (test environment)
- Use MCP tools to query tables for debugging/verification

## Critical Implementation Details

- **Articulos calculation**: Uses `int()` truncation, NOT `math.ceil()` - SAV7 original behavior
- **Paridad field**: Always 1.00 for MXN (not 20.00)
- **NumOC**: Required field, use 0 as default
- **Multi-remision**: Single invoice can match up to 5 remisiones (sum must equal total)
- **Draft detection**: Look for `drafts.` prefix on document IDs when needed
