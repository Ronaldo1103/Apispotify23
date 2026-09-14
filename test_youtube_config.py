import unittest
import base64
from pathlib import Path
from unittest.mock import patch
from fastapi import HTTPException
from youtube_config import youtube_options, youtube_error

class YoutubeConfigTests(unittest.TestCase):
    def test_runtime_enabled(self):
        with patch.dict('os.environ', {'YOUTUBE_COOKIES_FILE': ''}):
            self.assertIn('node', youtube_options()['js_runtimes'])
    def test_missing_cookie_file(self):
        with patch.dict('os.environ', {'YOUTUBE_COOKIES_FILE': '/missing/session.txt'}), patch('youtube_config.Path.is_file', return_value=False):
            with self.assertRaises(HTTPException) as error:
                youtube_options()
            self.assertEqual(error.exception.status_code, 503)
    def test_rate_limit(self):
        error = youtube_error(Exception('HTTP Error 429: Too Many Requests'))
        self.assertEqual(error.status_code, 429)
        self.assertEqual(error.headers['Retry-After'], '300')
    def test_authentication_error(self):
        self.assertEqual(youtube_error(Exception('Sign in to confirm you are not a bot')).status_code, 503)


class Base64CookieTests(unittest.TestCase):
    def test_creates_valid_cookie_file_and_reuses_it(self):
        content = '# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t1893456000\tTEST\tfake-value\n'
        encoded = base64.b64encode(content.encode()).decode()
        with patch.dict('os.environ', {'YOUTUBE_COOKIES_BASE64': encoded, 'YOUTUBE_COOKIES_FILE': '/invalid/local/path'}):
            first = youtube_options()['cookiefile']
            self.assertEqual(Path(first).read_text(), content)
            self.assertEqual(youtube_options()['cookiefile'], first)
    def test_invalid_base64_does_not_expose_secret(self):
        with patch.dict('os.environ', {'YOUTUBE_COOKIES_BASE64': 'invalid-secret!!!'}):
            with self.assertRaises(HTTPException) as error:
                youtube_options()
            self.assertEqual(error.exception.status_code, 503)
            self.assertNotIn('invalid-secret', error.exception.detail)
    def test_rejects_non_cookie_file(self):
        encoded = base64.b64encode(b'not cookies').decode()
        with patch.dict('os.environ', {'YOUTUBE_COOKIES_BASE64': encoded}):
            with self.assertRaises(HTTPException):
                youtube_options()
    def test_rejects_empty_cookie_jar(self):
        encoded = base64.b64encode(b'# Netscape HTTP Cookie File\n').decode()
        with patch.dict('os.environ', {'YOUTUBE_COOKIES_BASE64': encoded}):
            with self.assertRaises(HTTPException):
                youtube_options()
