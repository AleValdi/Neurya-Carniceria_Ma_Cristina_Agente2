# Configuración de Google Drive

El agente puede sincronizar XMLs y PDFs desde una carpeta de Google Drive.

## Archivos necesarios (no incluidos en el repo por seguridad)

| Archivo | Descripción | Cómo obtenerlo |
|---------|-------------|----------------|
| `credentials.json` | Credenciales OAuth de Google | Descargar de Google Cloud Console |
| `token.json` | Token de sesión | Se genera automáticamente al autorizar |
| `drive_config.txt` | ID de carpeta seleccionada | Se genera con `--config-drive` |

## Configuración inicial

### 1. Obtener credentials.json

1. Ve a https://console.cloud.google.com/
2. Crea un proyecto o usa uno existente
3. Habilita **Google Drive API** (APIs y servicios → Biblioteca)
4. Crea credenciales OAuth (APIs y servicios → Credenciales → Crear credenciales → ID de cliente OAuth)
5. Tipo: **Aplicación de escritorio**
6. Descarga el JSON y renómbralo a `credentials.json`
7. Colócalo en esta carpeta (`config/`)

### 2. Autorizar el agente

```bash
python main.py --config-drive
```

Esto abrirá el navegador para autorizar el acceso. Selecciona la carpeta de Drive donde subes las facturas.

### 3. Usar la sincronización

```bash
# Sincronizar y procesar
python main.py --sync-drive

# Solo sincronizar (sin procesar)
python main.py --sync-drive --dry-run
```

## Migrar a otra computadora (ej: Windows → Mac)

1. Copia `credentials.json` a la nueva máquina (mismo archivo)
2. Ejecuta `python main.py --config-drive` para re-autorizar
3. El `token.json` se generará automáticamente

**Nota**: El `token.json` de Windows puede funcionar en Mac si aún es válido, pero eventualmente expirará y tendrás que re-autorizar.

## Solución de problemas

| Problema | Solución |
|----------|----------|
| "credentials.json no encontrado" | Descargar de Google Cloud Console |
| "Error de autenticación" | Eliminar `token.json` y ejecutar `--config-drive` |
| "Carpeta no encontrada" | Ejecutar `--config-drive` para seleccionar otra carpeta |
| Token expirado | Eliminar `token.json` y re-autorizar |
