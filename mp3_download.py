"""MP3 conversion endpoint. Temporary files live only for the response lifetime."""
import re
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory

import yt_dlp
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

router = APIRouter()


@router.get('/youtube/mp3/{video_id}')
def download_mp3(video_id: str):
    if not re.fullmatch(r'[A-Za-z0-9_-]{11}', video_id):
        raise HTTPException(400, 'Identificador de YouTube no válido')
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        raise HTTPException(503, 'El servidor necesita FFmpeg para generar MP3')
    temporary = TemporaryDirectory(prefix='spotify-mp3-')
    try:
        output = Path(temporary.name) / 'audio.mp3'
        options = {
            'format': 'bestaudio/best',
            'noplaylist': True,
            'quiet': True,
            'socket_timeout': 30,
            'retries': 2,
            'outtmpl': str(Path(temporary.name) / 'audio.%(ext)s'),
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
        }
        with yt_dlp.YoutubeDL(options) as downloader:
            downloader.extract_info(f'https://www.youtube.com/watch?v={video_id}', download=True)
        if not output.is_file() or output.stat().st_size == 0:
            raise RuntimeError('No se generó el archivo MP3')
        return FileResponse(output, media_type='audio/mpeg', filename=f'{video_id}.mp3',
                            background=BackgroundTask(temporary.cleanup))
    except Exception as exc:
        temporary.cleanup()
        raise HTTPException(502, 'No se pudo descargar o convertir este video a MP3') from exc
