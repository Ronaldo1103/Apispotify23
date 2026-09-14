"""MP3 conversion endpoint. Temporary files live only for the response lifetime."""
import re
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory

import yt_dlp
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

if __package__:
    from .youtube_config import youtube_options, youtube_error
else:
    from youtube_config import youtube_options, youtube_error

router = APIRouter()


def _youtube_downloader_options(output_dir: Path | None = None) -> dict:
    options = {
        'format': 'bestaudio/best',
        'noplaylist': True,
        'quiet': True,
        'socket_timeout': 30,
        'retries': 2,
    }
    if output_dir is not None:
        options.update({
            'outtmpl': str(output_dir / 'audio.%(ext)s'),
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
        })
    return options


@router.get('/youtube/mp3/check/{video_id}')
def check_mp3(video_id: str):
    if not re.fullmatch(r'[A-Za-z0-9_-]{11}', video_id):
        raise HTTPException(400, 'Identificador de YouTube no válido')
    try:
        with yt_dlp.YoutubeDL({**_youtube_downloader_options(), **youtube_options()}) as downloader:
            info = downloader.extract_info(
                f'https://www.youtube.com/watch?v={video_id}',
                download=False,
            )
        if not info or not info.get('id'):
            raise RuntimeError('YouTube no devolvió información del video')
        return {
            'video_id': video_id,
            'available': True,
            'title': info.get('title') or '',
        }
    except Exception as exc:
        raise youtube_error(exc) from exc


@router.get('/youtube/mp3/{video_id}')
def download_mp3(video_id: str):
    if not re.fullmatch(r'[A-Za-z0-9_-]{11}', video_id):
        raise HTTPException(400, 'Identificador de YouTube no válido')
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        raise HTTPException(503, 'El servidor necesita FFmpeg para generar MP3')
    shared_options = youtube_options()
    temporary = TemporaryDirectory(prefix='spotify-mp3-')
    try:
        output = Path(temporary.name) / 'audio.mp3'
        options = _youtube_downloader_options(Path(temporary.name))
        with yt_dlp.YoutubeDL({**options, **shared_options}) as downloader:
            downloader.extract_info(f'https://www.youtube.com/watch?v={video_id}', download=True)
        if not output.is_file() or output.stat().st_size == 0:
            raise RuntimeError('No se generó el archivo MP3')
        return FileResponse(output, media_type='audio/mpeg', filename=f'{video_id}.mp3',
                            background=BackgroundTask(temporary.cleanup))
    except Exception as exc:
        temporary.cleanup()
        raise youtube_error(exc) from exc
