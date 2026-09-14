# Spotify Public Backend

Backend en FastAPI para metadatos musicales y búsqueda de videos relacionados.

Este servicio expone endpoints para buscar canciones, artistas, álbumes y playlists con SpotAPI. YouTube Data API se usa únicamente bajo demanda para encontrar el video relacionado con una canción seleccionada.

## Funcionalidad

- Búsqueda pública de música con SpotAPI
- Endpoints de búsqueda por tipo
- Búsqueda de videos con YouTube Data API
- Resolución de la URL oficial de YouTube a partir de título y artista
- Healthcheck y status
- Preparado para deploy en Railway

## Requisitos

- Python 3.10+
- pip

## Instalación local

```bash
cd spotify_backend
python -m venv .venv
. .venv/bin/activate   # Linux/macOS
# o .venv\Scripts\activate  # Windows
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

## Endpoints principales

- GET `/` → estado general
- GET `/health` → healthcheck
- GET `/buscar?q=coldplay&limit=10`
- GET `/buscar-canciones?q=coldplay&limit=10`
- GET `/buscar-artistas?q=coldplay&limit=10`
- GET `/buscar-albumes?q=coldplay&limit=10`
- GET `/buscar-playlists?q=coldplay&limit=10`
- GET `/buscar-todo?q=coldplay&limit=10`
- GET `/youtube/search?q=The%20Promise%20Deaimon&limit=5`
- GET `/youtube/url?title=The%20Promise&artist=Deaimon`
- GET `/debug/youtube?q=The%20Promise%20Deaimon`

## Variables de entorno

Copia el ejemplo:

```bash
cp .env.example .env
```

Opcional:

```env
PORT=8000
PYTHONUNBUFFERED=1
YOUTUBE_API_KEY=tu_clave_de_google_cloud
```

La clave debe configurarse en Railway en **Variables**, nunca dentro del código.

## Deploy en Railway

### Opción 1: usando Railway CLI

```bash
railway login
railway init
railway up
```

### Opción 2: usando el archivo de configuración

Este proyecto ya incluye `railway.json` y `Procfile` para arrancar la app con:

```bash
uvicorn main:app --host 0.0.0.0 --port $PORT
```

Si conectas el repositorio completo, configura `spotify_backend` como **Root Directory** del servicio en Railway. Después agrega estas variables en **Variables**:

```env
YOUTUBE_API_KEY=tu_clave_nueva
PYTHONUNBUFFERED=1
```

Railway asigna `PORT` automáticamente; no hace falta definirlo manualmente.

## Ejemplos de prueba

```bash
curl "http://localhost:8000/health"
curl "http://localhost:8000/buscar-canciones?q=deaimon&limit=10"
curl "http://localhost:8000/youtube/url?title=The%20Promise&artist=Deaimon"
```

## Nota importante

YouTube Data API devuelve metadatos y la URL oficial del video, no una URL MP3 ni un stream de audio directo. Para Railway, configura la API key y usa `/health` como healthcheck.

## Licencia

Usa la licencia del proyecto según tus necesidades.

## Descargas MP3 desde Android

Al tocar una canción en Flutter se inicia su reproducción y una descarga MP3.
Android guarda el archivo en la carpeta pública **Descargas**, muestra una
notificación y puede continuar la descarga fuera de la app. La interfaz muestra
el progreso mientras permanece abierta. Se permite una descarga supervisada a la
vez; si hay otra en curso se informa al usuario.

El cliente consulta `/youtube/url` con título y artista y descarga
`GET /youtube/mp3/{video_id}` del mismo backend. El servidor utiliza yt-dlp y
FFmpeg para convertir a MP3 a 192 kbps; los archivos temporales se limpian después
de enviarse o si la conversión falla. El endpoint no acepta URLs arbitrarias.

Se requieren `ffmpeg` y `ffprobe` en PATH, además de las dependencias de
`requirements.txt`. `nixpacks.toml` añade FFmpeg al despliegue Railway. Es necesario
publicar estos cambios del backend antes de usar la función contra Railway.
No basta con actualizar el APK. YouTube puede rechazar una descarga aunque exista
un resultado de búsqueda; en ese caso Android informa del fallo.

Comprobación local: `python -m unittest discover -s . -p test_mp3_download.py -v`.
Las pruebas convierten un tono generado localmente y no descargan canciones.

### JavaScript y bloqueos de YouTube

Railway instala Node.js 22 mediante Nixpacks y `yt-dlp[default]` instala los
componentes EJS. `youtube_config.py` habilita Node en todas las extracciones.
Estos cambios requieren reconstruir el despliegue.

Un error 429 indica que YouTube está limitando las solicitudes. El backend devuelve
429 con Retry-After de 300 segundos; esto no garantiza que el bloqueo termine en
ese plazo. La exigencia de verificar una sesión devuelve 503. Instalar Node no
elimina esos bloqueos.

Opcionalmente, YOUTUBE_COOKIES_FILE admite la ruta a un archivo privado de cookies
en formato Netscape de una sesión propia. No subas cookies a GitHub ni las pegues
en chats: permiten acceder a la sesión. Configura el archivo en almacenamiento
privado del servidor. Las cookies pueden caducar y no garantizan resolver un
bloqueo de la IP. No se extraen cookies automáticamente del navegador.
