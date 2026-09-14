"""Shared yt-dlp configuration for metadata, playback and downloads."""
import os
from pathlib import Path
from fastapi import HTTPException


def youtube_options():
    options = {
        'js_runtimes': {'node': {}, 'deno': {}},
        'socket_timeout': 30,
        'retries': 1,
        'fragment_retries': 1,
    }
    cookie_file = os.getenv('YOUTUBE_COOKIES_FILE', '').strip()
    if cookie_file:
        if not Path(cookie_file).is_file():
            raise HTTPException(503, 'No se encuentra el archivo de cookies configurado')
        options['cookiefile'] = cookie_file
    return options


def youtube_error(exc):
    message = str(exc).lower()
    if '429' in message or 'too many requests' in message:
        return HTTPException(429, 'YouTube limita las solicitudes de este servidor. Intenta más tarde.',
                             headers={'Retry-After': '300'})
    if 'not a bot' in message or 'sign in to confirm' in message:
        return HTTPException(503, 'YouTube exige verificar la sesión del servidor; no se puede descargar este audio ahora.')
    return HTTPException(502, 'No se pudo obtener el audio de YouTube')
