"""
Módulo de sincronización con Google Drive.

Descarga automáticamente archivos XML y PDF de facturas desde una carpeta
de Google Drive a la carpeta local de entrada del agente.

Soporta dos tipos de autenticación:
1. Service Account (recomendado para servidores)
2. OAuth 2.0 Desktop App (para usuarios individuales)

Requiere:
- google-api-python-client
- google-auth-httplib2
- google-auth-oauthlib

Configuración Service Account:
- Crear proyecto en Google Cloud Console
- Habilitar Google Drive API
- Crear cuenta de servicio y descargar JSON
- Renombrar a credentials.json y colocar en config/
- Compartir carpeta de Drive con el email de la cuenta de servicio
"""

import os
import io
import json
from pathlib import Path
from typing import List, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime
from loguru import logger

try:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google.oauth2 import service_account
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaIoBaseDownload
    from googleapiclient.errors import HttpError
    DRIVE_SUPPORT = True
except ImportError:
    DRIVE_SUPPORT = False
    logger.warning(
        "Bibliotecas de Google Drive no instaladas. "
        "Ejecute: pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib"
    )


# Permisos requeridos (solo lectura de archivos)
SCOPES = ['https://www.googleapis.com/auth/drive.readonly']

# Extensiones de archivo a sincronizar
EXTENSIONES_PERMITIDAS = {'.xml', '.pdf', '.XML', '.PDF'}


@dataclass
class DriveSyncResult:
    """Resultado de una sincronización con Google Drive"""
    archivos_nuevos: List[str]
    archivos_actualizados: List[str]
    archivos_omitidos: List[str]
    errores: List[str]
    exito: bool
    mensaje: str
    carpeta_origen: str
    carpeta_destino: str
    fecha_sincronizacion: datetime

    @property
    def total_sincronizados(self) -> int:
        return len(self.archivos_nuevos) + len(self.archivos_actualizados)

    def resumen(self) -> str:
        return (
            f"Sincronización {'exitosa' if self.exito else 'con errores'}: "
            f"{len(self.archivos_nuevos)} nuevos, "
            f"{len(self.archivos_actualizados)} actualizados, "
            f"{len(self.archivos_omitidos)} omitidos, "
            f"{len(self.errores)} errores"
        )


class DriveSync:
    """
    Sincronizador de archivos desde Google Drive.

    Soporta Service Account y OAuth 2.0.

    Uso con Service Account (recomendado):
        sync = DriveSync()
        sync.autenticar()  # Auto-detecta tipo de credenciales
        sync.configurar_carpeta(carpeta_id="ID_DE_LA_CARPETA")
        resultado = sync.sincronizar(carpeta_destino)

    Uso con OAuth:
        sync = DriveSync()
        sync.autenticar()  # Abre navegador para autorizar
        resultado = sync.sincronizar(carpeta_destino)
    """

    def __init__(self, config_dir: Optional[Path] = None):
        """
        Inicializar sincronizador.

        Args:
            config_dir: Directorio con credentials.json y token.json
        """
        if not DRIVE_SUPPORT:
            raise ImportError(
                "Bibliotecas de Google Drive no instaladas. "
                "Ejecute: pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib"
            )

        self.config_dir = config_dir or Path(__file__).parent.parent.parent / "config"
        self.credentials_file = self.config_dir / "credentials.json"
        self.token_file = self.config_dir / "token.json"
        self.creds = None
        self.service = None
        self._carpeta_id: Optional[str] = None
        self._carpeta_nombre: Optional[str] = None
        self._es_service_account: bool = False
        self._service_account_email: Optional[str] = None

    def verificar_configuracion(self) -> Tuple[bool, str]:
        """
        Verificar que exista el archivo de credenciales.

        Returns:
            Tupla (exito, mensaje)
        """
        if not self.credentials_file.exists():
            return False, (
                f"No se encontró credentials.json en {self.config_dir}\n"
                "Para configurar Google Drive:\n"
                "1. Ve a https://console.cloud.google.com/\n"
                "2. Crea un proyecto o usa uno existente\n"
                "3. Habilita 'Google Drive API'\n"
                "4. Crea una cuenta de servicio (Service Account)\n"
                "5. Descarga el archivo JSON y renómbralo a 'credentials.json'\n"
                "6. Colócalo en: " + str(self.config_dir)
            )
        return True, "Credenciales encontradas"

    def _detectar_tipo_credenciales(self) -> str:
        """
        Detectar si las credenciales son Service Account u OAuth.

        Returns:
            'service_account' o 'oauth'
        """
        try:
            with open(self.credentials_file, 'r') as f:
                creds_data = json.load(f)

            if creds_data.get('type') == 'service_account':
                self._service_account_email = creds_data.get('client_email', '')
                return 'service_account'
            elif 'installed' in creds_data or 'web' in creds_data:
                return 'oauth'
            else:
                return 'unknown'
        except Exception as e:
            logger.error(f"Error leyendo credenciales: {e}")
            return 'unknown'

    def autenticar(self) -> bool:
        """
        Autenticar con Google Drive.

        Auto-detecta el tipo de credenciales:
        - Service Account: autenticación directa (sin navegador)
        - OAuth: abre navegador para autorizar (primera vez)

        Returns:
            True si la autenticación fue exitosa
        """
        # Verificar credenciales
        ok, msg = self.verificar_configuracion()
        if not ok:
            logger.error(msg)
            return False

        # Detectar tipo de credenciales
        tipo = self._detectar_tipo_credenciales()

        if tipo == 'service_account':
            return self._autenticar_service_account()
        elif tipo == 'oauth':
            return self._autenticar_oauth()
        else:
            logger.error("Tipo de credenciales no reconocido en credentials.json")
            return False

    def _autenticar_service_account(self) -> bool:
        """Autenticar usando Service Account."""
        try:
            logger.info("Usando autenticación Service Account...")
            self.creds = service_account.Credentials.from_service_account_file(
                str(self.credentials_file),
                scopes=SCOPES
            )
            self._es_service_account = True

            # Construir servicio
            self.service = build('drive', 'v3', credentials=self.creds)
            logger.info(f"Autenticado como: {self._service_account_email}")
            return True

        except Exception as e:
            logger.error(f"Error autenticando con Service Account: {e}")
            return False

    def _autenticar_oauth(self) -> bool:
        """Autenticar usando OAuth 2.0 (flujo interactivo)."""
        try:
            # Intentar cargar token existente
            if self.token_file.exists():
                self.creds = Credentials.from_authorized_user_file(
                    str(self.token_file), SCOPES
                )

            # Si no hay credenciales válidas, autenticar
            if not self.creds or not self.creds.valid:
                if self.creds and self.creds.expired and self.creds.refresh_token:
                    logger.info("Renovando token de acceso...")
                    self.creds.refresh(Request())
                else:
                    logger.info("Iniciando flujo de autorización (se abrirá el navegador)...")
                    flow = InstalledAppFlow.from_client_secrets_file(
                        str(self.credentials_file), SCOPES
                    )
                    self.creds = flow.run_local_server(port=0)

                # Guardar token para futuras ejecuciones
                with open(self.token_file, 'w') as token:
                    token.write(self.creds.to_json())
                logger.info(f"Token guardado en: {self.token_file}")

            # Construir servicio
            self.service = build('drive', 'v3', credentials=self.creds)
            logger.info("Autenticación OAuth exitosa")
            return True

        except Exception as e:
            logger.error(f"Error autenticando con OAuth: {e}")
            return False

    def listar_carpetas(self) -> List[dict]:
        """
        Listar carpetas disponibles en Drive.

        Returns:
            Lista de diccionarios con id y nombre de cada carpeta
        """
        if not self.service:
            raise RuntimeError("No autenticado. Llame a autenticar() primero.")

        try:
            results = self.service.files().list(
                q="mimeType='application/vnd.google-apps.folder' and trashed=false",
                spaces='drive',
                fields='files(id, name)',
                pageSize=100
            ).execute()

            return results.get('files', [])
        except HttpError as e:
            logger.error(f"Error listando carpetas: {e}")
            return []

    def configurar_carpeta(self, carpeta_id: str = None, carpeta_nombre: str = None) -> bool:
        """
        Configurar la carpeta de Drive a sincronizar.

        Se puede especificar por ID o por nombre.

        Args:
            carpeta_id: ID de la carpeta de Google Drive
            carpeta_nombre: Nombre de la carpeta a buscar

        Returns:
            True si se encontró y configuró la carpeta
        """
        if not self.service:
            raise RuntimeError("No autenticado. Llame a autenticar() primero.")

        if carpeta_id:
            self._carpeta_id = carpeta_id
            # Obtener nombre
            try:
                folder = self.service.files().get(fileId=carpeta_id, fields='name').execute()
                self._carpeta_nombre = folder.get('name', carpeta_id)
                logger.info(f"Carpeta configurada: {self._carpeta_nombre} ({carpeta_id})")
                return True
            except HttpError as e:
                logger.error(f"No se encontró la carpeta con ID {carpeta_id}: {e}")
                return False

        if carpeta_nombre:
            carpetas = self.listar_carpetas()
            for carpeta in carpetas:
                if carpeta['name'].lower() == carpeta_nombre.lower():
                    self._carpeta_id = carpeta['id']
                    self._carpeta_nombre = carpeta['name']
                    logger.info(f"Carpeta encontrada: {self._carpeta_nombre} ({self._carpeta_id})")
                    return True

            logger.error(f"No se encontró carpeta con nombre: {carpeta_nombre}")
            logger.info("Carpetas disponibles:")
            for c in carpetas[:10]:
                logger.info(f"  - {c['name']}")
            return False

        logger.error("Debe especificar carpeta_id o carpeta_nombre")
        return False

    def listar_archivos(self, incluir_subcarpetas: bool = False) -> List[dict]:
        """
        Listar archivos XML y PDF en la carpeta configurada.

        Args:
            incluir_subcarpetas: Si True, incluye archivos en subcarpetas

        Returns:
            Lista de diccionarios con información de cada archivo
        """
        if not self.service or not self._carpeta_id:
            raise RuntimeError("No configurado. Llame a autenticar() y configurar_carpeta() primero.")

        try:
            # Query para archivos en la carpeta
            if incluir_subcarpetas:
                # Buscar en toda la carpeta y subcarpetas
                query = f"'{self._carpeta_id}' in parents and trashed=false"
            else:
                query = f"'{self._carpeta_id}' in parents and trashed=false"

            results = self.service.files().list(
                q=query,
                spaces='drive',
                fields='files(id, name, mimeType, modifiedTime, size)',
                pageSize=1000
            ).execute()

            archivos = results.get('files', [])

            # Filtrar por extensión
            archivos_filtrados = []
            for archivo in archivos:
                nombre = archivo.get('name', '')
                extension = Path(nombre).suffix.lower()
                if extension in {'.xml', '.pdf'}:
                    archivos_filtrados.append(archivo)

            logger.info(f"Encontrados {len(archivos_filtrados)} archivos XML/PDF en Drive")
            return archivos_filtrados

        except HttpError as e:
            logger.error(f"Error listando archivos: {e}")
            return []

    def descargar_archivo(self, archivo_id: str, destino: Path) -> bool:
        """
        Descargar un archivo de Drive.

        Args:
            archivo_id: ID del archivo en Drive
            destino: Ruta local donde guardar el archivo

        Returns:
            True si la descarga fue exitosa
        """
        if not self.service:
            raise RuntimeError("No autenticado. Llame a autenticar() primero.")

        try:
            request = self.service.files().get_media(fileId=archivo_id)

            # Crear directorio destino si no existe
            destino.parent.mkdir(parents=True, exist_ok=True)

            # Descargar
            fh = io.BytesIO()
            downloader = MediaIoBaseDownload(fh, request)

            done = False
            while not done:
                status, done = downloader.next_chunk()

            # Guardar archivo
            with open(destino, 'wb') as f:
                f.write(fh.getvalue())

            return True

        except HttpError as e:
            logger.error(f"Error descargando archivo: {e}")
            return False

    def sincronizar(
        self,
        carpeta_destino: Path,
        forzar_descarga: bool = False
    ) -> DriveSyncResult:
        """
        Sincronizar archivos desde Drive a carpeta local.

        Solo descarga archivos nuevos o modificados (basado en fecha).

        Args:
            carpeta_destino: Carpeta local donde guardar los archivos
            forzar_descarga: Si True, descarga todos los archivos aunque ya existan

        Returns:
            DriveSyncResult con el resultado de la sincronización
        """
        if not self.service or not self._carpeta_id:
            return DriveSyncResult(
                archivos_nuevos=[],
                archivos_actualizados=[],
                archivos_omitidos=[],
                errores=["No configurado. Llame a autenticar() y configurar_carpeta() primero."],
                exito=False,
                mensaje="Error de configuración",
                carpeta_origen=self._carpeta_nombre or "N/A",
                carpeta_destino=str(carpeta_destino),
                fecha_sincronizacion=datetime.now()
            )

        archivos_nuevos = []
        archivos_actualizados = []
        archivos_omitidos = []
        errores = []

        # Asegurar que existe la carpeta destino
        carpeta_destino.mkdir(parents=True, exist_ok=True)

        # Listar archivos en Drive
        archivos_drive = self.listar_archivos()

        for archivo in archivos_drive:
            nombre = archivo['name']
            archivo_id = archivo['id']
            destino = carpeta_destino / nombre

            try:
                # Verificar si ya existe y comparar fechas
                debe_descargar = forzar_descarga
                es_nuevo = not destino.exists()

                if not forzar_descarga:
                    if es_nuevo:
                        debe_descargar = True
                    else:
                        # Comparar fecha de modificación
                        fecha_drive = datetime.fromisoformat(
                            archivo['modifiedTime'].replace('Z', '+00:00')
                        )
                        fecha_local = datetime.fromtimestamp(
                            destino.stat().st_mtime
                        ).astimezone()

                        if fecha_drive > fecha_local:
                            debe_descargar = True

                if debe_descargar:
                    logger.debug(f"Descargando: {nombre}")
                    if self.descargar_archivo(archivo_id, destino):
                        if es_nuevo:
                            archivos_nuevos.append(nombre)
                        else:
                            archivos_actualizados.append(nombre)
                    else:
                        errores.append(f"Error descargando: {nombre}")
                else:
                    archivos_omitidos.append(nombre)

            except Exception as e:
                errores.append(f"Error con {nombre}: {str(e)}")
                logger.error(f"Error procesando {nombre}: {e}")

        # Resultado
        exito = len(errores) == 0
        resultado = DriveSyncResult(
            archivos_nuevos=archivos_nuevos,
            archivos_actualizados=archivos_actualizados,
            archivos_omitidos=archivos_omitidos,
            errores=errores,
            exito=exito,
            mensaje=f"Sincronizados {len(archivos_nuevos) + len(archivos_actualizados)} archivos",
            carpeta_origen=self._carpeta_nombre or self._carpeta_id,
            carpeta_destino=str(carpeta_destino),
            fecha_sincronizacion=datetime.now()
        )

        logger.info(resultado.resumen())
        return resultado

    def obtener_info_cuenta(self) -> dict:
        """
        Obtener información de la cuenta de Google conectada.

        Returns:
            Diccionario con email y otros datos de la cuenta
        """
        if not self.service:
            return {"error": "No autenticado"}

        # Para Service Account, retornar email directamente
        if self._es_service_account:
            return {
                "emailAddress": self._service_account_email,
                "tipo": "Service Account"
            }

        try:
            about = self.service.about().get(fields='user').execute()
            return about.get('user', {})
        except HttpError as e:
            return {"error": str(e)}

    def es_service_account(self) -> bool:
        """Retorna True si está usando Service Account."""
        return self._es_service_account

    def get_service_account_email(self) -> Optional[str]:
        """Retorna el email de la cuenta de servicio."""
        return self._service_account_email


# ============================================================
# Funciones de conveniencia
# ============================================================

_sync_instance: Optional[DriveSync] = None


def configurar_drive(
    credentials_path: Optional[Path] = None,
    carpeta_id: Optional[str] = None,
    carpeta_nombre: Optional[str] = None
) -> Tuple[bool, str]:
    """
    Configurar la conexión a Google Drive.

    Args:
        credentials_path: Ruta al archivo credentials.json
        carpeta_id: ID de la carpeta de Drive a sincronizar
        carpeta_nombre: Nombre de la carpeta de Drive a sincronizar

    Returns:
        Tupla (exito, mensaje)
    """
    global _sync_instance

    if not DRIVE_SUPPORT:
        return False, "Soporte de Google Drive no disponible. Instale las dependencias."

    try:
        config_dir = credentials_path.parent if credentials_path else None
        _sync_instance = DriveSync(config_dir)

        # Autenticar
        if not _sync_instance.autenticar():
            return False, "Error de autenticación con Google Drive"

        # Configurar carpeta
        if carpeta_id or carpeta_nombre:
            if not _sync_instance.configurar_carpeta(carpeta_id, carpeta_nombre):
                return False, f"No se encontró la carpeta especificada"

        # Obtener info de cuenta
        info = _sync_instance.obtener_info_cuenta()
        email = info.get('emailAddress', 'desconocido')

        return True, f"Conectado a Google Drive como: {email}"

    except Exception as e:
        logger.error(f"Error configurando Google Drive: {e}")
        return False, str(e)


def sincronizar_archivos(carpeta_destino: Path) -> DriveSyncResult:
    """
    Sincronizar archivos desde Google Drive a carpeta local.

    Args:
        carpeta_destino: Carpeta donde guardar los archivos

    Returns:
        DriveSyncResult con el resultado
    """
    global _sync_instance

    if not _sync_instance:
        return DriveSyncResult(
            archivos_nuevos=[],
            archivos_actualizados=[],
            archivos_omitidos=[],
            errores=["Google Drive no configurado. Llame a configurar_drive() primero."],
            exito=False,
            mensaje="No configurado",
            carpeta_origen="N/A",
            carpeta_destino=str(carpeta_destino),
            fecha_sincronizacion=datetime.now()
        )

    return _sync_instance.sincronizar(carpeta_destino)


def verificar_credenciales() -> Tuple[bool, str]:
    """
    Verificar si existen las credenciales de Google Drive.

    Returns:
        Tupla (existen, mensaje)
    """
    if not DRIVE_SUPPORT:
        return False, "Bibliotecas de Google Drive no instaladas"

    sync = DriveSync()
    return sync.verificar_configuracion()
