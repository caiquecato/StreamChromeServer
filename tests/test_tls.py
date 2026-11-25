from pathlib import Path
import ssl
import sys
import unittest
import urllib.error
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import stremio_imdb
import catalog_service
import library_downloader
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

    def test_media_download_ignores_only_expiry_while_keeping_ca_and_hostname_checks(self):
        calls = []
        fake_resp = object()

        def fake_urlopen(req, timeout=30, context=None):
            calls.append(context)
            if context is None:
                raise self.certificate_error()
            return fake_resp

        with patch('library_downloader.urlopen_resilient', side_effect=self.certificate_error()), \
             patch('library_downloader.urllib.request.urlopen', side_effect=fake_urlopen):
            res = library_downloader._open_media_stream('https://cdn.example.invalid/movie.mkv', timeout=30)
        self.assertIs(res, fake_resp)
        self.assertEqual(len(calls), 1)
        ctx = calls[0]
        self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(ctx.check_hostname)
        self.assertTrue(ctx.verify_flags & getattr(ssl, 'X509_V_FLAG_NO_CHECK_TIME', 0x200000))

    def test_media_download_still_rejects_untrusted_or_wrong_host_certificates(self):
        bad = ssl.SSLCertVerificationError('self-signed certificate')
        bad.verify_code = 18
        with patch('library_downloader.urlopen_resilient', side_effect=urllib.error.URLError(bad)), \
             patch('library_downloader.urllib.request.urlopen') as fallback:
            with self.assertRaises(urllib.error.URLError):
                library_downloader._open_media_stream('https://cdn.example.invalid/movie.mkv', timeout=30)
            fallback.assert_not_called()
