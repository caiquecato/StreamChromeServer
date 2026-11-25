import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import subtitles as subs

SRT = b'1\n00:00:01,000 --> 00:00:03,500\nOla!\n'

class SubtitleTests(unittest.TestCase):
    def test_provider_keys_stay_in_the_selected_app_data_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings_path = Path(tmp) / 'subtitle_settings.dat'
            shared_path = Path(tmp) / 'server_settings.dat'
            shared_path.write_bytes(b'other-services-must-stay-intact')
            service = subs.SubtitleService({}, settings_path=settings_path)
            self.assertEqual(service.configure({'subdl_key': 'test-key-not-secret'})['subdl'], True)
            self.assertEqual(subs.load_settings(settings_path)['subdl_key'], 'test-key-not-secret')
            self.assertEqual(shared_path.read_bytes(), b'other-services-must-stay-intact')

    def test_srt_and_existing_vtt_have_one_header(self):
        self.assertIn('00:00:01.000 --> 00:00:03.500', subs.to_vtt(SRT))
        self.assertEqual(subs.to_vtt(b'\xef\xbb\xbfWEBVTT\n\n00:01.000 --> 00:03.000\nHi'), 'WEBVTT\n\n00:01.000 --> 00:03.000\nHi\n')
        self.assertIn('a\u00e7\u00e3o', subs.to_vtt(SRT.replace(b'Ola!', 'a\u00e7\u00e3o'.encode('cp1252'))))

    def test_zip_selects_requested_episode_without_extracting(self):
        data = io.BytesIO()
        with zipfile.ZipFile(data, 'w') as archive:
            archive.writestr('Show.S01E01.srt', SRT.replace(b'Ola', b'Wrong'))
            archive.writestr('../../Show.S01E02.srt', SRT.replace(b'Ola', b'Correct'))
        self.assertIn('Correct', subs.to_vtt(data.getvalue(), season=1, episode=2))
        with self.assertRaises(ValueError):
            subs.to_vtt(data.getvalue(), season=1, episode=3)

    def test_rejects_non_subtitle_and_ambiguous_zip(self):
        with self.assertRaises(ValueError):
            subs.to_vtt(b'<html>error</html>')
        data = io.BytesIO()
        with zipfile.ZipFile(data, 'w') as archive:
            archive.writestr('one.srt', SRT)
            archive.writestr('two.srt', SRT)
        with self.assertRaises(ValueError):
            subs.to_vtt(data.getvalue())

    def test_local_filename_suggests_series_and_year(self):
        self.assertEqual(subs.guess_search('Breaking.Bad.S05E14.1080p.WEB-DL.mkv'),
                         {'title': 'Breaking Bad', 'type': 'series', 'season': 5, 'episode': 14, 'year': ''})
        self.assertEqual(subs.guess_search('Dune.Part.Two.2024.2160p.mkv')['year'], '2024')

    def test_missing_credentials_is_actionable(self):
        result = subs.SubtitleService({}).search({'title': 'Dune', 'type': 'movie', 'language': 'pt-BR'})
        self.assertEqual(result['subtitles'], [])
        self.assertEqual(len(result['warnings']), 2)
        self.assertIn('SubDL', ' '.join(result['warnings']))

    def test_providers_receive_episode_and_language_and_failure_is_isolated(self):
        def reply(url, headers=None, payload=None):
            if 'subdl.com' in url:
                self.assertIn('season=5', url)
                self.assertIn('episode=14', url)
                self.assertIn('languages=BR_PT', url)
                raise subs.ProviderError('SubDL temporariamente indisponivel')
            self.assertIn('season_number=5', url)
            self.assertIn('episode_number=14', url)
            self.assertIn('languages=pt-br', url)
            return {'data': [{'attributes': {'language': 'pt-br', 'release': 'Breaking.Bad.S05E14', 'feature_details': {'title': 'Breaking Bad', 'season_number': 5, 'episode_number': 14}, 'files': [{'file_id': 12, 'file_name': 'Show.srt'}]}}]}
        service = subs.SubtitleService({'subdl_key': 'test', 'opensubtitles_key': 'test'})
        with patch.object(subs, 'http_json', side_effect=reply):
            result = service.search({'title': 'Breaking Bad', 'type': 'series', 'season': 5, 'episode': 14, 'language': 'pt-BR'})
        self.assertEqual(len(result['subtitles']), 1)
        self.assertEqual(result['subtitles'][0]['provider'], 'OpenSubtitles')
        self.assertEqual(len(result['warnings']), 1)

    def test_subdl_skips_wrong_episode_in_unpacked_season(self):
        response = {'subtitles': [{'release_name': 'Season', 'full_season': True, 'unpack_files': [
            {'season': 1, 'episode': 1, 'url': '/subtitle/a/one', 'name': 'S01E01.srt'},
            {'season': 1, 'episode': 2, 'url': '/subtitle/a/two', 'name': 'S01E02.srt'}]}]}
        service = subs.SubtitleService({'subdl_key': 'test'})
        with patch.object(subs, 'http_json', return_value=response):
            result = service.search({'title': 'Show', 'type': 'series', 'season': 1, 'episode': 2, 'language': 'en'})
        self.assertEqual(len(result['subtitles']), 1)
        self.assertIn('S01E02', result['subtitles'][0]['label'])

    def test_download_cache_avoids_spending_quota_twice(self):
        service = subs.SubtitleService({'opensubtitles_key': 'test'})
        with patch.object(subs, 'http_json', return_value={'data': [{'attributes': {'language': 'en', 'files': [{'file_id': 12, 'file_name': 'Dune.srt'}]}}]}):
            result = service.search({'title': 'Dune', 'type': 'movie', 'language': 'en'})
        token = result['subtitles'][0]['id']
        with patch.object(subs, 'http_json', return_value={'link': 'https://dl.opensubtitles.com/test.srt'}) as download, patch.object(subs, 'http_bytes', return_value=SRT):
            self.assertEqual(service.download(token), service.download(token))
            self.assertEqual(download.call_count, 1)

    def test_invalid_episode_rejected_before_network(self):
        with self.assertRaises(ValueError):
            subs.SubtitleService({}).search({'title': 'Show', 'type': 'series', 'season': 1, 'episode': 0, 'language': 'en'})

    def test_windows_settings_are_encrypted_and_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory:
            location = Path(directory) / 'settings.dat'
            subs.save_settings({'subdl_key': 'secret-example'}, location)
            self.assertNotIn(b'secret-example', location.read_bytes())
            self.assertEqual(subs.load_settings(location)['subdl_key'], 'secret-example')

if __name__ == '__main__':
    unittest.main()
