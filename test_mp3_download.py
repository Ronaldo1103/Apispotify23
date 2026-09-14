import asyncio
import shutil
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from mp3_download import download_mp3


class Mp3DownloadTests(unittest.TestCase):
    def test_rejects_invalid_id(self):
        with self.assertRaises(HTTPException) as error:
            download_mp3('../not-a-video')
        self.assertEqual(error.exception.status_code, 400)

    def test_missing_ffmpeg(self):
        with patch('mp3_download.shutil.which', return_value=None):
            with self.assertRaises(HTTPException) as error:
                download_mp3('abcdefghijk')
        self.assertEqual(error.exception.status_code, 503)

    def test_conversion_failure_cleans_temporary_directory(self):
        paths = []
        class Downloader:
            def __init__(self, options): paths.append(Path(options['outtmpl']).parent)
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def extract_info(self, *args, **kwargs): raise RuntimeError('conversion failed')
        with patch('mp3_download.shutil.which', return_value='ffmpeg'), patch('mp3_download.yt_dlp.YoutubeDL', Downloader):
            with self.assertRaises(HTTPException) as error:
                download_mp3('abcdefghijk')
        self.assertEqual(error.exception.status_code, 502)
        self.assertFalse(paths[0].exists())

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg required')
    def test_returns_real_mp3_and_cleans_after_response(self):
        class Downloader:
            def __init__(self, options): self.options = options
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def extract_info(self, url, download):
                # Generate original test audio; conversion uses the actual yt-dlp postprocessor.
                import yt_dlp
                from yt_dlp.postprocessor.ffmpeg import FFmpegExtractAudioPP
                wav = Path(self.options['outtmpl']).parent / 'audio.wav'
                subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=0.2', str(wav)], check=True)
                with yt_dlp.YoutubeDL.__wrapped__({'quiet': True}) as ydl:
                    FFmpegExtractAudioPP(ydl, preferredcodec='mp3', preferredquality='192').run({'filepath': str(wav), 'ext': 'wav'})
                return {}
        import yt_dlp
        original = yt_dlp.YoutubeDL
        Downloader.__wrapped__ = original
        with patch('mp3_download.yt_dlp.YoutubeDL', Downloader):
            response = download_mp3('abcdefghijk')
        path = Path(response.path)
        self.assertTrue(path.is_file())
        self.assertEqual(response.media_type, 'audio/mpeg')
        codec = subprocess.check_output(['ffprobe', '-v', 'error', '-show_entries', 'stream=codec_name', '-of', 'default=noprint_wrappers=1:nokey=1', str(path)], text=True)
        self.assertEqual(codec.strip(), 'mp3')
        asyncio.run(response.background())
        self.assertFalse(path.parent.exists())


if __name__ == '__main__':
    unittest.main()
