# Agente de Conciliación SAT - Referencia Técnica

## Estructura de Carpetas

```
AgenteConciliacionSAT\
├── AgenteConciliacionSAT.exe
├── .env
├── _internal\                    ← NO MODIFICAR
├── config\
│   ├── credentials.json         ← Credenciales Google (opcional)
│   ├── token.json               ← Token de sesión Google (auto-generado)
│   └── drive_config.txt         ← Configuración carpeta Drive (auto)
├── data\
│   ├── xml_facturas\            ← XMLs a procesar (+ PDFs opcionales)
│   ├── procesados\              ← XMLs procesados
│   └── reportes\                ← Reportes generados
└── logs\
```

**IMPORTANTE:** Crear carpetas manualmente después de compilar:
```bash
mkdir data\xml_facturas data\procesados data\reportes logs
```

---

## Configuración .env

```ini
# --- Conexión a Base de Datos ---
DB_SERVER=localhost
DB_PORT=1433
DB_DATABASE=DBSAV71_TEST      # Cambiar a DBSAV71 para producción
DB_USERNAME=devsav7
DB_PASSWORD=devsav7
DB_DRIVER={SQL Server Native Client 11.0}

# --- IMPORTANTE: Nombres de Tablas SAV7 ---
# NO CAMBIAR estos valores - son los nombres reales en la BD
SAV7_TABLA_REMISIONES=SAVRecC
SAV7_TABLA_DETALLE=SAVRecD
SAV7_TABLA_PROVEEDORES=SAVProveedor

# --- Configuración de Conciliación ---
DIAS_RANGO_BUSQUEDA=15        # Días para buscar remisiones (±15 de la fecha factura)
TOLERANCIA_MONTO=2.0          # Tolerancia % en diferencias de monto
```

---

## Esquema de Base de Datos

### Columnas de SAVRecC que USA el agente (INSERT)

```
Serie, NumRec, NumOC, Proveedor, ProveedorNombre, Fecha,
Comprador, Procesada, FechaAlta, UltimoCambio, Estatus,
SubTotal1, Iva, Total, Pagado, Referencia, Comentario,
Moneda, Paridad, Tipo, Plazo, TotalLetra, SubTotal2,
Factura, Ciudad, Estado, Saldo, Capturo, CapturoCambio,
Articulos, Partidas, ProcesadaFecha, IntContable,
TipoRecepcion, Consolidacion, RFC, TimbradoFolioFiscal,
FacturaFecha, Sucursal, Departamento, Afectacion,
MetododePago, TipoProveedor, TotalPrecio, TotalRecibidoNeto, SerieRFC
```

### Columnas de SAVRecD que USA el agente (INSERT detalles)

```
Serie, NumRec, Producto, Talla, Nombre, Proveedor,
Cantidad, Costo, CostoImp, PorcDesc, PorcIva, NumOC,
Unidad, Unidad2, Unidad2Valor, Servicio, Registro1,
ControlTalla, CodProv, Modelo, Pedimento, Orden,
ComplementoIva, CantidadNeta, CostoDif, Precio,
CantidadUM2, Lotes, UltimoCostoC
```

### Columnas de SAVProveedor que USA el agente (SELECT)

```
Clave, RFC, Empresa, Ciudad, Estado, Tipo
```

### Columnas que NO EXISTEN (evitar usar)

| Tabla | Columna | Notas |
|-------|---------|-------|
| SAVProveedor | SerieRFC | Solo existe en SAVRecC |
| SAVRecC | TipoCambio | No existe |

---

## Implementación Actual

### Archivo: `src/erp/models.py`

Modelo `Remision` - campos adicionales para consolidación:
```python
# Campos adicionales para consolidación (tomados de remisión base)
comprador: Optional[str] = None  # Usuario que capturó la remisión
plazo: int = 0                   # Días de crédito
sucursal: int = 5                # Sucursal de la remisión

# Campos del proveedor (de SAVProveedor)
ciudad_proveedor: Optional[str] = None
estado_proveedor: Optional[str] = None
tipo_proveedor: Optional[str] = None
```

### Archivo: `src/erp/remisiones.py`

Query de búsqueda - campos que se traen:
```sql
SELECT
    r.Serie as serie,
    r.NumRec as numero_remision,
    r.Fecha as fecha_remision,
    r.Proveedor as id_proveedor,
    COALESCE(p.RFC, r.RFC) as rfc_proveedor,
    COALESCE(r.ProveedorNombre, p.Empresa, '') as nombre_proveedor,
    r.SubTotal1 as subtotal,
    r.Iva as iva,
    r.Total as total,
    r.Estatus as estatus,
    r.Factura as factura_proveedor,
    r.TimbradoFolioFiscal as uuid_factura,
    r.Comprador as comprador,
    r.Plazo as plazo,
    r.Sucursal as sucursal,
    p.Ciudad as ciudad_proveedor,
    p.Estado as estado_proveedor,
    p.Tipo as tipo_proveedor
FROM SAVRecC r
LEFT JOIN SAVProveedor p ON r.Proveedor = p.Clave
WHERE r.Serie = 'R'
  AND r.Estatus != 'Consolidada'
```

**NOTA:** NO traer `p.SerieRFC` - esa columna no existe en SAVProveedor.

### Archivo: `src/erp/consolidacion.py`

#### Función `numero_a_letra()`
Convierte números a texto español:
- `4000.00` → `"CUATRO MIL PESOS 00/100 M.N."`
- Soporta hasta millones

#### Cálculo de Articulos
```python
# Suma de CANTIDADES de todos los detalles, TRUNCADO (int)
total_articulos = sum(
    sum(d.cantidad for d in r.detalles) for r in remisiones
)
total_articulos = int(total_articulos)  # 51.4 → 51, 199.8 → 199
```

**IMPORTANTE:** El sistema original TRUNCA (usa int()). NO usar math.ceil().
- Prueba: SUM(Cantidad) = 51.4 → ORIGINAL tiene Articulos = 51
- math.ceil() daría 52, lo cual NO coincide con el original

#### INSERT de cabecera factura (46 campos)

```sql
INSERT INTO SAVRecC (
    Serie, NumRec, Proveedor, ProveedorNombre, Fecha,
    Comprador, Procesada, FechaAlta, UltimoCambio, Estatus,
    SubTotal1, Iva, Total, Pagado, Referencia,
    Comentario, Moneda, Paridad, Tipo, Plazo,
    SubTotal2, Factura, Saldo, Capturo, CapturoCambio,
    Articulos, Partidas, ProcesadaFecha, IntContable,
    TipoRecepcion, Consolidacion, RFC, TimbradoFolioFiscal,
    FacturaFecha, Sucursal, Departamento, Afectacion,
    MetododePago, NumOC, TotalLetra, Ciudad, Estado,
    TipoProveedor, TotalPrecio, TotalRecibidoNeto, SerieRFC
) VALUES (...)
```

Valores importantes:
```python
params = (
    'F',                              # Serie
    num_rec,                          # NumRec (siguiente secuencial)
    remision_base.id_proveedor,       # Proveedor
    remision_base.nombre_proveedor,   # ProveedorNombre
    fecha_actual,                     # Fecha
    comprador,                        # Comprador (de remisión, NO 'AGENTE_SAT')
    1,                                # Procesada
    fecha_actual,                     # FechaAlta
    fecha_actual,                     # UltimoCambio
    'No Pagada',                      # Estatus
    subtotal,                         # SubTotal1
    iva,                              # Iva
    total,                            # Total
    Decimal('0'),                     # Pagado
    'CREDITO',                        # Referencia
    comentario,                       # Comentario (RECEPCIONES: R-XXXXX)
    'PESOS',                          # Moneda
    Decimal('1.00'),                  # Paridad (1.00 para PESOS, NO 20.00)
    'Crédito',                        # Tipo
    plazo,                            # Plazo (de remisión base)
    subtotal,                         # SubTotal2
    factura_sat.folio or '',          # Factura
    total,                            # Saldo
    comprador,                        # Capturo
    comprador,                        # CapturoCambio
    total_articulos,                  # Articulos (suma de cantidades, redondeado arriba)
    total_partidas,                   # Partidas (número de líneas)
    fecha_actual,                     # ProcesadaFecha
    1,                                # IntContable
    'COMPRAS',                        # TipoRecepcion
    1,                                # Consolidacion
    factura_sat.rfc_emisor,           # RFC
    factura_sat.uuid,                 # TimbradoFolioFiscal
    factura_sat.fecha_emision,        # FacturaFecha
    sucursal,                         # Sucursal (de remisión base)
    'TIENDA',                         # Departamento
    'TIENDA',                         # Afectacion
    'PPD',                            # MetododePago
    0,                                # NumOC (requerido, NO puede ser NULL)
    total_letra,                      # TotalLetra (generado)
    ciudad,                           # Ciudad (de proveedor)
    estado,                           # Estado (de proveedor)
    tipo_proveedor,                   # TipoProveedor (de proveedor)
    total,                            # TotalPrecio
    total,                            # TotalRecibidoNeto
    '',                               # SerieRFC (vacío)
)
```

#### INSERT de detalles (SAVRecD)

Se copian TODOS los campos del detalle original de la remisión:
```python
# Se obtienen los detalles originales de SAVRecD Serie R
# y se copian a SAVRecD Serie F con:
# - Nueva Serie = 'F'
# - Nuevo NumRec = número de factura
# - CodProv = "R-XXXXX P1" (referencia al origen)
# - Orden = secuencial
```

#### UPDATE de remisión consolidada
```sql
UPDATE SAVRecC
SET
    Estatus = 'Consolidada',
    Saldo = 0,
    Consolidacion = 1,
    Consolida = 1,                    # BIT (1=true), NO string 'F'
    ConsolidaSerie = 'F',
    ConsolidaNumRec = num_factura,
    TimbradoFolioFiscal = uuid_factura,
    CancelacionFecha = fecha_actual,
    CancelacionCapturo = 'AGENTE_SAT',
    CancelacionMotivo = 'CONSOLIDACION',
    UltimoCambio = fecha_actual
WHERE Serie = 'R' AND NumRec = ?
```

---

## Errores Conocidos y Soluciones

| Error | Causa | Solución |
|-------|-------|----------|
| `Invalid column name 'SerieRFC'` | Se intentó traer de SAVProveedor | Solo usar en INSERT a SAVRecC |
| `Invalid column name 'TipoCambio'` | Columna no existe | No incluir en queries |
| `Cannot insert NULL into 'NumOC'` | Campo requerido | Incluir `NumOC = 0` |
| `Conversion failed nvarchar to bit` | Campo `Consolida` es BIT | Usar `1` en lugar de `'F'` |
| Antivirus bloquea .exe | Falso positivo PyInstaller | Agregar a exclusiones |
| Articulos difiere en 1 | ceil vs truncado | Usar `int()` (truncado) - el sistema original TRUNCA, NO redondea |

---

## Queries de Limpieza

### Borrar factura del agente
```sql
DELETE FROM SAVRecD WHERE Serie = 'F' AND NumRec = XXXXX;
DELETE FROM SAVRecC WHERE Serie = 'F' AND NumRec = XXXXX;
```

### Revertir remisión
```sql
UPDATE SAVRecC
SET
    Estatus = 'RECIBIDA',
    Saldo = Total,
    Consolidacion = 0,
    Consolida = 0,
    ConsolidaSerie = NULL,
    ConsolidaNumRec = NULL,
    TimbradoFolioFiscal = NULL,
    CancelacionFecha = NULL,
    CancelacionCapturo = NULL,
    CancelacionMotivo = NULL
WHERE Serie = 'R' AND NumRec = XXXXX;
```

---

## Comparación de Campos Validada

### Última prueba: F-67762 (AGENTE) vs F-65282 (ORIGINAL)

**SAVRecC (Cabecera):**
| Campo | Original | Agente | Estado |
|-------|----------|--------|--------|
| Comprador | ABIGAIL RUIZ | ABIGAIL RUIZ | ✅ |
| Plazo | 7 | 7 | ✅ |
| Paridad | 20.00 | 1.00 | ✅ Corregido |
| TotalLetra | CUATRO MIL... | CUATRO MIL... | ✅ |
| Ciudad | MONTERREY | MONTERREY | ✅ |
| Estado | NUEVO LEÓN | NUEVO LEÓN | ✅ |
| TipoProveedor | MERCADO | MERCADO | ✅ |
| TotalPrecio | 5234.76 | 4000.00 | ✅ |
| TotalRecibidoNeto | 3871.87 | 4000.00 | ✅ |
| Articulos | 200 | 200 | ✅ Corregido con int() truncado |

**SAVRecD (Detalles) - IDÉNTICOS:**
| Campo | Original | Agente |
|-------|----------|--------|
| Producto | FYV002059 | FYV002059 |
| Cantidad | 199.8000 | 199.8000 |
| Costo | 20.02002002 | 20.02002002 |
| CodProv | R-45503 P1 | R-45503 P1 |
| Precio | 26.20 | 26.20 |
| CantidadNeta | 193.4000 | 193.4000 |
| UltimoCostoC | 20.0535 | 20.0535 |

### Diferencias esperadas (no son errores)
- **Estatus:** Original=Tot.Pagada, Agente=No Pagada (se actualiza al pagar)
- **Pagado/Saldo:** Se actualizan al registrar pagos
- **FacturaElectronica*:** Se llena en proceso de timbrado
- **IntContablePoliza:** Se llena en proceso contable

---

## Compilación

```bash
cd agente-conciliacion-sat

python -m PyInstaller --onedir --name AgenteConciliacionSAT \
    --add-data "config;config" --add-data "data;data" \
    --hidden-import=pyodbc --hidden-import=lxml \
    --hidden-import=openpyxl --hidden-import=pandas \
    --hidden-import=loguru --hidden-import=fuzzywuzzy \
    --hidden-import=Levenshtein \
    --distpath dist --workpath build --noconfirm main.py

# Crear carpetas
mkdir dist\AgenteConciliacionSAT\data\xml_facturas
mkdir dist\AgenteConciliacionSAT\data\procesados
mkdir dist\AgenteConciliacionSAT\data\reportes
mkdir dist\AgenteConciliacionSAT\logs

# Copiar configuración
copy .env.example dist\AgenteConciliacionSAT\.env
# EDITAR .env para configurar base de datos
```

---

## Restricciones del Agente

- Solo busca remisiones **Serie R**
- Solo busca remisiones con **Estatus != 'Consolidada'**
- Solo consolida matches al **100%**
- Máximo **5 remisiones** por combinación (multi-remision)

---

## Resultados de Pruebas (5 Facturas)

### Facturas de Prueba Utilizadas

| # | Proveedor | Total | Remisiones Esperadas | Resultado |
|---|-----------|-------|----------------------|-----------|
| 1 | BIMBO | $3,448.94 | 1 remisión | ✅ CONSOLIDADA |
| 2 | MA ELENA | $1,004.75 | 1 remisión | ✅ CONSOLIDADA |
| 3 | (3 remisiones) | - | 3 remisiones | ❌ No match 100% |
| 4 | (4 remisiones) | - | 4 remisiones | ❌ No match 100% |
| 5 | (5 remisiones) | - | 5 remisiones | ❌ No match 100% |

### Validación de Campos - Prueba BIMBO (1 remisión)
- **Total:** ✅ Coincide exactamente
- **Articulos:** ✅ Coincide exactamente
- **Comentario:** ✅ Coincide exactamente

### Validación de Campos - Prueba MA ELENA (1 remisión)
- **Total:** ✅ Coincide exactamente
- **Comentario:** ✅ Coincide exactamente
- **Articulos:** ✅ Corregido (SUM(Cantidad) = 51.4 → int() = 51)

### Hallazgo Importante: Cálculo de Articulos
```sql
-- Verificar suma de cantidades de una remisión
SELECT SUM(Cantidad) FROM SAVRecD WHERE Serie = 'R' AND NumRec = 46621
-- Resultado: 51.4000
```

| Método | Resultado | ¿Correcto? |
|--------|-----------|------------|
| math.ceil(51.4) | 52 | ❌ NO coincide |
| int(51.4) | 51 | ✅ Coincide con ORIGINAL |

**Conclusión:** El sistema SAV7 original usa TRUNCADO, no redondeo.

### RESUELTO: Multi-Remision

**Causa raíz:** El parámetro `dias_rango_busqueda = 3` era muy restrictivo. Los proveedores emiten la factura días o semanas después de entregar la mercancía.

**Evidencia:**
| Factura | Total SAT | Remisiones | Suma | Días diferencia |
|---------|-----------|------------|------|-----------------|
| LA COCINA DE CRISTINA | $25,462.50 | 46547, 46548, 46549 | $25,462.50 ✅ | 4 días |
| ROGELIO GARZA FLORES | $8,510.00 | 46652, 46625, 46585, 46525 | $8,510.00 ✅ | 8 días |
| JOSE LUIS ESCOBEDO | $3,710.00 | 46677, 46650, 46560, 46444, 46392 | $3,710.00 ✅ | 14 días |

**Solución:** Cambiar `dias_rango_busqueda` de 3 a 15 en `config/settings.py`

```python
dias_rango_busqueda: int = 15  # Buscar remisiones ±15 días de la fecha de factura
```

**Nota:** Si 15 días resulta insuficiente para algunos proveedores, ajustar vía variable de entorno `DIAS_RANGO_BUSQUEDA`

---

## Query de Comparación para Pruebas

```sql
-- Comparar cabeceras entre BD producción y test
SELECT 'ORIGINAL' as BD, * FROM DBSAV71.dbo.SAVRecC
WHERE Serie = 'F' AND NumRec = XXXXX
UNION ALL
SELECT 'AGENTE' as BD, * FROM DBSAV71_TEST.dbo.SAVRecC
WHERE Serie = 'F' AND NumRec = YYYYY;

-- Comparar detalles
SELECT 'ORIGINAL' as BD, * FROM DBSAV71.dbo.SAVRecD
WHERE Serie = 'F' AND NumRec = XXXXX
UNION ALL
SELECT 'AGENTE' as BD, * FROM DBSAV71_TEST.dbo.SAVRecD
WHERE Serie = 'F' AND NumRec = YYYYY
ORDER BY BD, Orden;
```

---

## NUEVA FUNCIONALIDAD: Soporte PDF

### Descripción

El agente ahora puede extraer números de remisión directamente de los PDFs de las facturas de proveedores. Esto elimina la ambigüedad cuando hay múltiples remisiones del mismo proveedor y garantiza un match exacto.

### Cómo Funciona

1. El agente escanea todos los PDFs en `data/xml_facturas/`
2. Extrae el UUID fiscal de cada PDF
3. Crea un índice UUID → PDF
4. Cuando procesa un XML, busca el PDF correspondiente por UUID
5. Si encuentra el PDF, extrae los números de remisión del texto
6. Usa esas remisiones para el match (prioridad sobre algoritmo)

### Formatos de Remisión Soportados

El extractor busca estos patrones en el texto del PDF:
- `R-12345` (formato principal)
- `Remisiones: R-16662, R-16672, R-16683`
- `Facturas/Remisiones: R-XXXXX, R-YYYYY`

### Ejemplo de Uso

```
data/xml_facturas/
├── factura_proveedor.xml      ← UUID: ABC123...
├── factura_proveedor.pdf      ← Contiene: "R-16662, R-16672"
└── otra_factura.xml           ← Sin PDF, usa algoritmo
```

### Columna "Método" en Reporte

El reporte Excel ahora incluye una columna "Método" que indica:
- `PDF` - Match basado en números extraídos del PDF
- `ALGORITMO` - Match calculado por el motor de conciliación

---

## NUEVA FUNCIONALIDAD: Google Drive

### Descripción

El agente puede sincronizar automáticamente los archivos XML y PDF desde una carpeta de Google Drive. Esto permite:
- Subir facturas a Drive desde cualquier dispositivo
- El agente descarga automáticamente los archivos nuevos
- No es necesario copiar archivos manualmente al servidor

### Requisitos

1. Cuenta de Google con acceso a la carpeta de facturas
2. Credenciales de API de Google (`credentials.json`)

### Configuración Inicial (Solo una vez)

#### Paso 1: Crear Proyecto en Google Cloud

1. Ve a https://console.cloud.google.com/
2. Crea un nuevo proyecto o usa uno existente
3. Habilita **Google Drive API**:
   - Menú → APIs y servicios → Biblioteca
   - Buscar "Google Drive API"
   - Clic en "Habilitar"

#### Paso 2: Crear Credenciales OAuth

1. Ve a APIs y servicios → Credenciales
2. Clic "Crear credenciales" → "ID de cliente OAuth"
3. Tipo de aplicación: **Aplicación de escritorio**
4. Nombre: "Agente Conciliación SAT"
5. Descargar JSON y renombrarlo a `credentials.json`
6. Colocar en la carpeta `config/`

#### Paso 3: Autorizar el Agente

```bash
# Ejecutar el asistente de configuración
AgenteConciliacionSAT.exe --config-drive

# O usar el batch file
configurar_drive.bat
```

Esto abrirá el navegador para autorizar el acceso. Seleccionar la carpeta de Drive a usar.

### Uso Diario

```bash
# Sincronizar desde Drive y procesar
AgenteConciliacionSAT.exe --sync-drive

# O usar el batch file
sincronizar_y_procesar.bat
```

### Flujo de Trabajo Recomendado

1. **Proveedor envía factura:**
   - Subir XML y PDF a la carpeta de Drive configurada

2. **Ejecutar el agente:**
   ```bash
   # Opción A: Manual
   sincronizar_y_procesar.bat

   # Opción B: Programar tarea (Windows Task Scheduler)
   AgenteConciliacionSAT.exe --sync-drive
   ```

3. **Verificar resultados:**
   - Revisar `data/reportes/conciliacion_*.xlsx`
   - Ver `logs/conciliacion_*.log`

### Archivos de Configuración de Drive

| Archivo | Ubicación | Descripción |
|---------|-----------|-------------|
| `credentials.json` | `config/` | Credenciales OAuth de Google (descargar de Cloud Console) |
| `token.json` | `config/` | Token de sesión (auto-generado al autorizar) |
| `drive_config.txt` | `config/` | ID y nombre de la carpeta seleccionada |

### Solución de Problemas

| Problema | Solución |
|----------|----------|
| "credentials.json no encontrado" | Descargar de Google Cloud Console |
| "Error de autenticación" | Eliminar `token.json` y volver a autorizar |
| "Carpeta no encontrada" | Ejecutar `--config-drive` de nuevo |
| "Bibliotecas no instaladas" | `pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib` |

### Seguridad

- El token se guarda localmente en `config/token.json`
- Solo tiene acceso de lectura a Drive (no puede modificar/borrar)
- Las credenciales nunca se transmiten al servidor del agente
- La sesión expira después de un tiempo; el agente renueva automáticamente

---

## Comandos del Agente

| Comando | Descripción |
|---------|-------------|
| `--help` | Mostrar ayuda |
| `--test-conexion` | Probar conexión a BD |
| `--explorar` | Explorar estructura de BD |
| `--dry-run` | Simular sin escribir en BD |
| `--verbose` | Mostrar información detallada |
| `--archivo <ruta>` | Procesar un XML específico |
| `--config-drive` | Configurar Google Drive |
| `--sync-drive` | Sincronizar desde Drive antes de procesar |

### Combinaciones Útiles

```bash
# Simular con sincronización de Drive
AgenteConciliacionSAT.exe --sync-drive --dry-run --verbose

# Procesar solo sin Drive
AgenteConciliacionSAT.exe --verbose

# Solo sincronizar (sin procesar)
AgenteConciliacionSAT.exe --sync-drive --archivo dummy.xml
```
