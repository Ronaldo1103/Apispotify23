# Spotify Public Backend

Backend en FastAPI para búsquedas musicales y reproducción con fallback de audio público.

Este servicio expone endpoints para buscar canciones, artistas, álbumes, playlists y también ofrece una capa con Piped y un fallback a iTunes para devolver URLs de preview/audio reales cuando Piped está caído.

## Funcionalidad

- Búsqueda pública de música con SpotAPI
- Endpoints de búsqueda por tipo
- Búsqueda con Piped
- Fallback a Apple iTunes cuando Piped falla
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
- GET `/piped/search?q=coldplay&limit=10`
- GET `/piped/track/{video_id}`

## Variables de entorno

Copia el ejemplo:

```bash
cp .env.example .env
```

Opcional:

```env
PORT=8000
PYTHONUNBUFFERED=1
```

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

## Ejemplo de prueba

```bash
curl "http://localhost:8000/piped/search?q=coldplay&limit=3"
```

## Nota importante

La fuente Piped puede caer o devolver errores de SSL/502. Por eso el backend tiene un fallback a iTunes para devolver previews de audio reales. Esto permite seguir teniendo resultados reproducibles en desarrollo y en despliegues temporales.

## Licencia

Usa la licencia del proyecto según tus necesidades.
