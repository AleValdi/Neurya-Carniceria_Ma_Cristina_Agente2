# Queries para Revertir Remisiones y Facturas (Pruebas)

Este documento contiene las queries necesarias para preparar datos de prueba en `DBSAV71_TEST` para el Agente de Conciliación SAT-ERP.

---

## Estructura de Datos en SAVRecC

La tabla `SAVRecC` almacena tanto **remisiones** (Serie 'R') como **facturas** (Serie 'F').

| Campo | Descripción |
|-------|-------------|
| `Serie` | 'R' = Remisión, 'F' = Factura consolidada |
| `NumRec` | Número único del registro |
| `Factura` | Número de Orden de Compra del proveedor (en remisiones) |
| `RFC` | RFC del proveedor |
| `Total` | Monto total |
| `Estatus` | 'No Pagada', 'Consolidada', etc. |
| `TimbradoFolioFiscal` | **UUID de la factura SAT** (clave para matching) |
| `Fecha` | Fecha del registro |
| `ProveedorNombre` | Nombre del proveedor |

---

## 1. Buscar Factura Serie F por UUID

El campo `TimbradoFolioFiscal` contiene el UUID de la factura SAT. Es la forma más precisa de encontrar una factura.

```sql
USE DBSAV71_TEST;

-- Buscar factura Serie F por UUID
SELECT Serie, NumRec, Fecha, Total, Estatus, Factura, RFC, ProveedorNombre, TimbradoFolioFiscal
FROM SAVRecC
WHERE Serie = 'F'
AND TimbradoFolioFiscal = 'b2ef43e7-f7b9-4a07-b438-2bf2fdafd853';  -- UUID del XML
```

---

## 2. Buscar Remisiones Asociadas a una Factura

Las remisiones asociadas a una factura comparten el mismo `TimbradoFolioFiscal`.

```sql
USE DBSAV71_TEST;

-- Buscar todas las remisiones asociadas a una factura por UUID
SELECT Serie, NumRec, Fecha, Total, Estatus, Factura, RFC, ProveedorNombre, TimbradoFolioFiscal
FROM SAVRecC
WHERE TimbradoFolioFiscal = 'b2ef43e7-f7b9-4a07-b438-2bf2fdafd853'
ORDER BY Serie, NumRec;
```

---

## 3. Buscar Remisión por Número de Orden de Compra

El campo `Factura` en remisiones contiene el número de Orden de Compra del proveedor.

```sql
USE DBSAV71_TEST;

-- Buscar remisión por número de OC (viene en el XML como referencia)
SELECT Serie, NumRec, Fecha, Total, Estatus, Factura, RFC, ProveedorNombre, TimbradoFolioFiscal
FROM SAVRecC
WHERE Serie = 'R'
AND Factura = '47716';  -- Número de Orden de Compra del XML
```

---

## 4. Buscar Remisiones por RFC del Proveedor

```sql
USE DBSAV71_TEST;

-- Buscar remisiones de un proveedor específico
SELECT Serie, NumRec, Fecha, Total, Estatus, Factura, RFC, ProveedorNombre, TimbradoFolioFiscal
FROM SAVRecC
WHERE RFC = 'HERL930529FB0'  -- RFC del emisor del XML
AND Serie = 'R'
ORDER BY Fecha DESC;
```

---

## 5. Buscar Remisiones por Monto Aproximado

```sql
USE DBSAV71_TEST;

-- Buscar remisiones con monto cercano al de la factura
SELECT Serie, NumRec, Fecha, Total, Estatus, Factura, RFC, ProveedorNombre
FROM SAVRecC
WHERE Serie = 'R'
AND Total BETWEEN 3800 AND 4200  -- Ajustar rango según el total de la factura
AND Estatus = 'Consolidada'
ORDER BY Fecha DESC;
```

---

## 6. Revertir Remisión y Eliminar Factura (Procedimiento Completo)

### Paso 1: Encontrar la factura y remisiones por UUID

```sql
USE DBSAV71_TEST;

-- Ver todos los registros relacionados con el UUID
SELECT Serie, NumRec, Fecha, Total, Estatus, Factura, RFC, ProveedorNombre
FROM SAVRecC
WHERE TimbradoFolioFiscal = 'b2ef43e7-f7b9-4a07-b438-2bf2fdafd853';
```

### Paso 2: Revertir la(s) remisión(es)

```sql
USE DBSAV71_TEST;

-- Revertir remisión(es) a estado "No Pagada"
UPDATE SAVRecC
SET Estatus = 'No Pagada',
    Factura = NULL,
    TimbradoFolioFiscal = NULL
WHERE Serie = 'R'
AND NumRec = 45503;  -- Número de remisión a revertir
```

### Paso 3: Eliminar la factura Serie F

```sql
USE DBSAV71_TEST;

-- Eliminar la factura consolidada
DELETE FROM SAVRecC
WHERE Serie = 'F'
AND NumRec = 67762;  -- Número de la factura Serie F
```

### Paso 4: Verificar que quedó limpio

```sql
USE DBSAV71_TEST;

-- Verificar que no queden registros con ese UUID
SELECT Serie, NumRec, Total, Estatus, Factura, TimbradoFolioFiscal
FROM SAVRecC
WHERE TimbradoFolioFiscal = 'b2ef43e7-f7b9-4a07-b438-2bf2fdafd853'
   OR NumRec IN (45503, 67762);
```

---

## 7. Revertir Múltiples Remisiones

```sql
USE DBSAV71_TEST;

-- Revertir múltiples remisiones a la vez
UPDATE SAVRecC
SET Estatus = 'No Pagada',
    Factura = NULL,
    TimbradoFolioFiscal = NULL
WHERE Serie = 'R'
AND NumRec IN (16662, 16672, 16679, 16683, 16689);  -- Lista de números de remisión

-- Verificar los cambios
SELECT Serie, NumRec, Fecha, Total, Estatus, Factura, TimbradoFolioFiscal
FROM SAVRecC
WHERE Serie = 'R'
AND NumRec IN (16662, 16672, 16679, 16683, 16689);
```

---

## 8. Restaurar Después de Prueba

```sql
USE DBSAV71_TEST;

-- Restaurar remisión a estado original
UPDATE SAVRecC
SET Estatus = 'Consolidada',
    Factura = '47716',
    TimbradoFolioFiscal = 'b2ef43e7-f7b9-4a07-b438-2bf2fdafd853'
WHERE Serie = 'R'
AND NumRec = 45503;

-- Nota: La factura Serie F debe recrearse manualmente o ejecutando el agente
```

---

## Ejemplo Completo de Prueba

### Datos de Factura XML
- **UUID:** b2ef43e7-f7b9-4a07-b438-2bf2fdafd853
- **Serie/Folio:** A-23120
- **Orden de Compra:** 47716
- **Total:** $4,000.00
- **RFC Emisor:** HERL930529FB0
- **Fecha:** 2025-11-06

### Registros en BD
- **Remisión:** Serie R, NumRec 45503
- **Factura:** Serie F, NumRec 67762

### Query Completo para Preparar Prueba

```sql
USE DBSAV71_TEST;

-- 1. Revertir la remisión
UPDATE SAVRecC
SET Estatus = 'No Pagada',
    Factura = NULL,
    TimbradoFolioFiscal = NULL
WHERE Serie = 'R' AND NumRec = 45503;

-- 2. Eliminar la factura Serie F
DELETE FROM SAVRecC
WHERE Serie = 'F' AND NumRec = 67762;

-- 3. Verificar
SELECT Serie, NumRec, Total, Estatus, Factura, TimbradoFolioFiscal
FROM SAVRecC
WHERE NumRec IN (45503, 67762)
   OR TimbradoFolioFiscal = 'b2ef43e7-f7b9-4a07-b438-2bf2fdafd853';
```

---

## Resumen de Flujo

```
1. Obtener UUID del XML de factura
           ↓
2. Buscar en SAVRecC por TimbradoFolioFiscal
           ↓
3. Identificar:
   - Remisión(es) Serie R → Revertir a "No Pagada"
   - Factura Serie F → Eliminar
           ↓
4. Ejecutar el agente de conciliación
           ↓
5. Verificar que se creó nueva factura Serie F
```
