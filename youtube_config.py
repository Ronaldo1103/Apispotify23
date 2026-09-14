"""Shared yt-dlp configuration for metadata, playback and downloads."""
import os
import base64
import binascii
import hashlib
import tempfile
import threading
from http.cookiejar import MozillaCookieJar, LoadError
from pathlib import Path
from fastapi import HTTPException
from dotenv import load_dotenv

load_dotenv(Path(__file__).with_name(".env"), override=False)


_cookie_lock = threading.Lock()
_cookie_directory = None
_cookie_cache = {}


def _cookies_from_base64(value):
    """Create private process-local files; never log cookie contents."""
    global _cookie_directory
    try:
        data = base64.b64decode(''.join(value.split()), validate=True)
        text = data.decode('utf-8-sig')
        if not text.startswith(('# Netscape HTTP Cookie File', '# HTTP Cookie File')):
            raise ValueError('Invalid cookie header')
    except (ValueError, UnicodeError, binascii.Error):
        raise HTTPException(503, 'YOUTUBE_COOKIES_BASE64 no contiene un archivo Netscape válido') from None
    digest = hashlib.sha256(data).hexdigest()
    with _cookie_lock:
        if digest in _cookie_cache and Path(_cookie_cache[digest]).is_file():
            return _cookie_cache[digest]
        if _cookie_directory is None:
            _cookie_directory = tempfile.TemporaryDirectory(prefix='youtube-private-')
        path = Path(_cookie_directory.name) / (digest + '.txt')
        try:
            with path.open('x', encoding='utf-8', newline='\n') as output:
                os.chmod(path, 0o600)
                output.write(text)
            jar = MozillaCookieJar(str(path))
            jar.load(ignore_discard=True, ignore_expires=True)
            if not any(c.domain.lstrip('.') == 'youtube.com' or c.domain.endswith('.youtube.com') for c in jar):
                raise ValueError('Missing YouTube cookies')
        except (OSError, LoadError, ValueError):
            path.unlink(missing_ok=True)
            raise HTTPException(503, 'No se pudo preparar el archivo privado de cookies de YouTube') from None
        _cookie_cache[digest] = str(path)
        return str(path)


def youtube_options():
    options = {
        'js_runtimes': {'node': {}, 'deno': {}},
        'socket_timeout': 30,
        'retries': 1,
        'fragment_retries': 1,
    }
    encoded_cookies = os.getenv('YOUTUBE_COOKIES_BASE64', '').strip()
    cookie_file = (_cookies_from_base64(encoded_cookies) if encoded_cookies
                   else os.getenv('YOUTUBE_COOKIES_FILE', '').strip())
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
