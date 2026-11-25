from pathlib import Path
import ssl
import sys
import unittest
import urllib.error
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import stremio_imdb
import catalog_service
import subtitles


class TLSTests(unittest.TestCase):
    def test_subtitle_login_is_never_retried_without_certificate_validation(self):
        with patch('subtitles.urllib.request.urlopen', side_effect=self.certificate_error()) as request:
            with self.assertRaises(subtitles.ProviderError):
                subtitles.http_bytes('https://example.invalid/login', headers={'Api-Key': 'dummy'}, payload={'password': 'dummy'})
            self.assertEqual(request.call_count, 1)

    def certificate_error(self):
        error = ssl.SSLCertVerificationError('certificate expired')
        error.verify_code = 10
        return urllib.error.URLError(error)

    def test_expired_addon_certificate_is_never_retried_without_verification(self):
        with patch('stremio_imdb.urllib.request.urlopen', side_effect=self.certificate_error()) as request:
            with self.assertRaises(urllib.error.URLError):
                stremio_imdb.urlopen_resilient('https://example.invalid/private-token')
            self.assertEqual(request.call_count, 1)

    def test_expired_poster_certificate_is_never_retried_without_verification(self):
        with patch('catalog_service.urllib.request.urlopen', side_effect=self.certificate_error()) as request:
            with self.assertRaises(urllib.error.URLError):
                catalog_service._http_get_bytes('https://example.invalid/poster.jpg')
            self.assertEqual(request.call_count, 1)
