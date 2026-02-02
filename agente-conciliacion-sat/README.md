# Agente de Conciliación SAT-ERP (SAV7)

Agente automatizado para conciliar facturas CFDI del SAT con remisiones del ERP SAV7.

## Características

- **Descarga automática del SAT** (opcional): Con FIEL configurada, descarga facturas automáticamente
- **Parseo de XMLs CFDI 4.0/3.3**: Extrae datos de facturas electrónicas mexicanas
- **Conexión a SQL Server**: Consulta remisiones directamente del ERP SAV7
- **Algoritmo de matching inteligente**: Vincula facturas con remisiones por:
  - RFC del proveedor
  - Fecha (con tolerancia configurable)
  - Monto total (con tolerancia del 2%)
  - Similitud de productos (matching difuso)
- **Sistema de alertas**: Detecta discrepancias automáticamente
- **Reportes Excel**: Genera reportes detallados con estadísticas
- **Múltiples modos de ejecución**: Manual, programado o servicio continuo

## Flujo de Operación

```
┌─────────────────────────────────────────────────────────────────────┐
│                    FLUJO DE CONCILIACIÓN                            │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────────────┐  │
│  │   PASO 1     │    │   PASO 2     │    │      PASO 3          │  │
│  │  Obtener     │───▶│  Conciliar   │───▶│  Generar Reporte     │  │
│  │  XMLs SAT    │    │  con SAV7    │    │  Excel con alertas   │  │
│  └──────────────┘    └──────────────┘    └──────────────────────┘  │
│        │                    │                      │                │
│   FIEL/Manual/        (Automático)           (Automático)          │
│   ElConta                                                          │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

## Instalación Rápida (Windows)

### Opción 1: Instalador automático
```
1. Ejecutar "instalar_servicio.bat" como administrador
2. Configurar el archivo .env con los datos de conexión
3. Listo - se ejecutará diariamente a la hora configurada
```

### Opción 2: Instalación manual

#### 1. Requisitos previos
- Python 3.9+
- ODBC Driver 17 for SQL Server
- Acceso a la base de datos SAV7

#### 2. Instalar dependencias
```bash
pip install -r requirements.txt
```

#### 3. Configurar conexión
```bash
copy .env.example .env
```

Editar `.env` con los datos de conexión:
```env
DB_SERVER=tu_servidor
DB_DATABASE=SAV7
DB_USERNAME=usuario
DB_PASSWORD=contraseña
```

## Modos de Ejecución

### 1. Manual (bajo demanda)
```bash
# Ejecutar una vez
python main.py

# O usar el archivo batch
ejecutar_ahora.bat
```

### 2. Tarea Programada (recomendado para servidor)
```bash
# Ejecutar una vez (para Programador de Tareas de Windows)
python scheduler.py --una-vez

# Configurar ejecución diaria a las 7:00 AM
python scheduler.py --hora 07:00
```

### 3. Servicio Continuo
```bash
# Ejecución diaria + cada hora
python scheduler.py --hora 07:00 --intervalo 60

# Monitoreo de carpeta (procesa cuando llegan nuevos XMLs)
python scheduler.py --monitorear
```

## Obtención de XMLs del SAT

### Opción A: Descarga Automática (requiere FIEL)

Configurar en `.env`:
```env
FIEL_CER_PATH=C:/ruta/certificado.cer
FIEL_KEY_PATH=C:/ruta/llave.key
FIEL_PASSWORD=tu_contraseña
```

### Opción B: ElConta Descarga Masiva
1. Usar ElConta para descargar los XMLs
2. Configurar el agente para leer de esa carpeta
3. Ejecutar el agente

### Opción C: Descarga Manual
1. Descargar XMLs desde https://portalcfdi.facturaelectronica.sat.gob.mx/
2. Colocar en la carpeta `data/input/`
3. Ejecutar `python main.py`

## Estructura del Proyecto

```
agente-conciliacion-sat/
├── config/
│   ├── settings.py          # Configuración general
│   └── database.py          # Conexión SQL Server
├── src/
│   ├── sat/
│   │   ├── xml_parser.py    # Parser de CFDI
│   │   ├── sat_downloader.py # Descarga automática SAT
│   │   └── models.py        # Modelos de facturas
│   ├── erp/
│   │   ├── sav7_connector.py # Conector BD
│   │   ├── remisiones.py    # Queries de remisiones
│   │   └── models.py        # Modelos de remisiones
│   ├── conciliacion/
│   │   ├── matcher.py       # Algoritmo de matching
│   │   ├── validator.py     # Validaciones
│   │   └── alerts.py        # Sistema de alertas
│   └── reports/
│       └── excel_generator.py # Generación de Excel
├── data/
│   ├── input/               # XMLs a procesar
│   ├── output/              # Reportes generados
│   └── processed/           # XMLs procesados
├── logs/                    # Archivos de log
├── main.py                  # Punto de entrada principal
├── scheduler.py             # Programador de tareas
├── instalar_servicio.bat    # Instalador Windows
├── ejecutar_ahora.bat       # Ejecución manual rápida
├── requirements.txt
└── .env                     # Configuración (crear desde .env.example)
```

## Comandos Disponibles

```bash
# Procesar XMLs en input/
python main.py

# Probar conexión a BD
python main.py --test-conexion

# Explorar estructura de BD (encontrar tablas de remisiones)
python main.py --explorar

# Procesar un archivo específico
python main.py --archivo ruta/factura.xml

# Modo verbose
python main.py --verbose

# Scheduler: ejecución diaria
python scheduler.py --hora 07:00

# Scheduler: ejecución periódica
python scheduler.py --intervalo 60

# Scheduler: monitoreo de carpeta
python scheduler.py --monitorear
```

## Reportes Generados

El agente genera reportes en `data/output/`:

### Excel (.xlsx)
| Hoja | Contenido |
|------|-----------|
| Resumen Ejecutivo | Estadísticas generales |
| Conciliaciones Exitosas | Facturas vinculadas correctamente |
| Con Diferencias | Facturas con discrepancias |
| Sin Remisión | Facturas sin remisión asociada |
| Alertas | Todas las alertas generadas |
| Detalle Completo | Información de cada factura |

### CSV
- Datos tabulares para análisis adicional

## Tipos de Alertas

| Tipo | Descripción | Acción |
|------|-------------|--------|
| CRÍTICA | Sin remisión asociada | Verificar recepción física |
| CRÍTICA | Diferencia >10% | Revisar urgente |
| ALTA | Diferencia 5-10% | Conciliar diferencia |
| ALTA | Remisión duplicada | Revisar asignaciones |
| MEDIA | Diferencia 2-5% | Verificar |
| MEDIA | Fecha desfasada >7 días | Revisar correspondencia |

## Configuración Avanzada

### Tolerancias (en .env)
```env
TOLERANCIA_MONTO=2.0        # Porcentaje aceptable de diferencia
DIAS_RANGO_BUSQUEDA=3       # Días ± para buscar remisiones
DIAS_ALERTA_DESFASE=7       # Días para alertar desfase
UMBRAL_SIMILITUD=80         # % mínimo para matching de texto
```

### Estructura de SAV7
Si tus tablas tienen nombres diferentes:
```env
SAV7_TABLA_REMISIONES=TU_TABLA_REMISIONES
SAV7_TABLA_DETALLE=TU_TABLA_DETALLE
SAV7_TABLA_PROVEEDORES=TU_TABLA_PROVEEDORES
```

## Solución de Problemas

### Error de conexión a BD
1. Verificar que ODBC Driver 17 esté instalado
2. Verificar credenciales en `.env`
3. Ejecutar `python main.py --test-conexion`

### No encuentra remisiones
1. Ejecutar `python main.py --explorar` para ver tablas disponibles
2. Ajustar nombres de tablas en `.env`
3. Verificar que el RFC del proveedor coincida

### XMLs no se parsean
1. Verificar que sean CFDI válidos (versión 3.3 o 4.0)
2. Revisar logs en `logs/`

### Descarga SAT no funciona
1. Verificar que cfdiclient esté instalado: `pip install cfdiclient`
2. Verificar rutas de archivos .cer y .key
3. Verificar contraseña de la FIEL

## Logs

Los logs se guardan en `logs/` con rotación automática:
- `conciliacion_YYYYMMDD.log` - Log del día
- Retención: 30 días
- Tamaño máximo: 10 MB por archivo

## Licencia

Proyecto interno - Carnicería María Cristina
