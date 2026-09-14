import unittest
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
