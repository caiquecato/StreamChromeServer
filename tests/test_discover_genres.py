from pathlib import Path
import sys
import unittest
import tempfile
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import stremio_imdb as imdb


class GenreTests(unittest.TestCase):
    def test_addon_configuration_tokens_are_never_public_identifiers(self):
        url = 'https://example.invalid/private-token/config/manifest.json'
        self.assertNotIn('private-token', imdb.mask_manifest_url(url))
        self.assertNotIn('password', imdb.mask_manifest_url('https://user:password@example.invalid/a/b/manifest.json'))
        with tempfile.TemporaryDirectory() as tmp:
            manager = imdb.StremioAddonManager(Path(tmp) / 'settings.dat')
            with patch.object(imdb, '_http_json', return_value={'name': 'Test addon', 'resources': ['stream']}):
                result = manager.add_addon(url)
            self.assertNotIn('private-token', str(result))
            with patch.object(manager, 'list_addons', return_value=[{'url': url, 'id': url}]):
                self.assertNotIn('private-token', str(manager.public_addons()))

    def test_source_identity_survives_token_refresh_without_exposing_url(self):
        item = {'url': 'https://example.invalid/private-source', 'name': '1080p'}
        first = imdb._parse_addon_stream('Test', item, 'series', 'tt0386676:1:1')
        second = imdb._parse_addon_stream('Test', item, 'series', 'tt0386676:1:1')
        other = imdb._parse_addon_stream('Test', {**item, 'url': item['url'] + '-other'}, 'series', 'tt0386676:1:1')
        self.assertEqual(first['source_key'], second['source_key'])
        self.assertNotEqual(first['stream_token'], second['stream_token'])
        self.assertNotEqual(first['source_key'], other['source_key'])
        self.assertNotIn('private-source', first['source_key'])

    def test_genres_come_from_catalog_manifest(self):
        manifest = {'catalogs': [{'type': 'series', 'id': 'top', 'extra': [
            {'name': 'genre', 'options': ['Drama', 'Comedy']}
        ]}]}
        with patch.object(imdb, '_http_json', return_value=manifest):
            self.assertEqual(imdb.get_genres('series'), ['Drama', 'Comedy'])

    def test_genre_uses_trending_catalog_and_encoded_path(self):
        with patch.object(imdb, '_http_json', return_value={'metas': []}) as fetch:
            self.assertEqual(imdb.discover('movie', 'top', '', 'Sci-Fi & Fantasy'), [])
            self.assertTrue(fetch.call_args.args[0].endswith('/top/genre=Sci-Fi%20%26%20Fantasy.json'))


if __name__ == '__main__':
    unittest.main()
