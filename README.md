# Spotify Public Backend

Backend minimalista en FastAPI que usa SpotAPI para buscar canciones públicas.

## Ejecutar localmente

```bash
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000
```

## Endpoints

- GET `/` -> status
- GET `/health` -> healthcheck
- GET `/buscar?q=weezer&limit=10` -> devuelve resultados de búsqueda

## Railway

Usar como comando de arranque:

```bash
uvicorn main:app --host 0.0.0.0 --port $PORT
```
