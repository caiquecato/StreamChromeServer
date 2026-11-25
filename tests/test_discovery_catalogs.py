from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import stremio_imdb as imdb


class DiscoveryCatalogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.manager = imdb.StremioAddonManager(Path(self.tmp.name) / 'settings.dat')
        self.addon = {'name': 'Provider lists', 'url': 'https://addon.invalid/private-token/manifest.json', 'base_url': 'https://addon.invalid/private-token'}
        self.manifest = {'catalogs': [
            {'id': 'top', 'type': 'movie', 'name': 'Popular', 'extra': [{'name': 'genre', 'options': ['Drama', 'Sci-Fi & Fantasy']}, {'name': 'search'}]},
            {'id': 'year', 'type': 'movie', 'name': 'New'},
        ]}
        self.addon_manifest = {'catalogs': [
            {'id': 'provider/new', 'type': 'series', 'name': 'Provider new', 'extra': [{'name': 'genre', 'options': ['Drama']}]},
            {'id': 'anime', 'type': 'anime', 'name': 'Anime'},
            {'id': 'needs', 'type': 'other', 'name': 'Custom', 'extra': [{'name': 'region', 'isRequired': True}]},
        ]}

    def fetch(self, url, **kwargs):
        if url == imdb.CINEMETA_BASE + '/manifest.json':
            return self.manifest
        if url == self.addon['url']:
            return self.addon_manifest
        if '/catalog/series/' in url:
            self.assertEqual(url, 'https://addon.invalid/private-token/catalog/series/provider%2Fnew/genre=Drama.json')
            return {'metas': [{'id': 'tt1234567', 'type': 'series', 'name': 'A show'}]}
        if '/catalog/anime/' in url:
            return {'metas': [{'id': 'kitsu:123', 'type': 'anime', 'name': 'Anime title'}]}
        raise AssertionError(url)

    def test_lists_only_real_manifest_catalogs_without_disclosing_configuration(self):
        with patch.object(self.manager, 'list_addons', return_value=[self.addon]), patch.object(imdb, '_http_json', side_effect=self.fetch):
            catalogs = self.manager.discovery_catalogs()
        self.assertEqual([c['name'] for c in catalogs], ['Popular', 'New', 'Provider new', 'Anime', 'Custom'])
        self.assertEqual(catalogs[0]['genres'], ['Drama', 'Sci-Fi & Fantasy'])
        self.assertNotIn('private-token', str(catalogs))
        self.assertNotIn('https://', str(catalogs))
        self.assertNotIn('imdbRating', str(catalogs))
        self.assertEqual(catalogs[-1]['required_filters'], ['region'])

    def test_catalog_uses_real_type_and_encodes_addon_catalog_id(self):
        with patch.object(self.manager, 'list_addons', return_value=[self.addon]), patch.object(imdb, '_http_json', side_effect=self.fetch):
            catalog = next(c for c in self.manager.discovery_catalogs() if c['name'] == 'Provider new')
            result = self.manager.discover_catalog(catalog['key'], 'series', genre='Drama')
            self.assertEqual(result[0]['kind'], 'series')
            self.assertEqual(result[0]['imdb_id'], 'tt1234567')
            self.assertTrue(result[0]['playable'])
            with self.assertRaises(ValueError):
                self.manager.discover_catalog(catalog['key'], 'movie')
            with self.assertRaises(ValueError):
                self.manager.discover_catalog(catalog['key'], 'series', search='query')

    def test_non_imdb_types_remain_external_and_are_not_relabeled_movie(self):
        with patch.object(self.manager, 'list_addons', return_value=[self.addon]), patch.object(imdb, '_http_json', side_effect=self.fetch):
            catalog = next(c for c in self.manager.discovery_catalogs() if c['kind'] == 'anime')
            result = self.manager.discover_catalog(catalog['key'], 'anime')
        self.assertEqual(result[0]['kind'], 'anime')
        self.assertEqual(result[0]['imdb_id'], '')
        self.assertFalse(result[0]['playable'])
        self.assertEqual(result[0]['stremio_url'], 'stremio:///detail/anime/kitsu%3A123')

    def test_invalid_and_required_filters_fail_clearly(self):
        with patch.object(self.manager, 'list_addons', return_value=[self.addon]), patch.object(imdb, '_http_json', side_effect=self.fetch):
            catalog = next(c for c in self.manager.discovery_catalogs() if c['kind'] == 'other')
            with self.assertRaisesRegex(ValueError, 'region'):
                self.manager.discover_catalog(catalog['key'], 'other')
            with self.assertRaises(ValueError):
                self.manager.discover_catalog('unknown', 'movie')

if __name__ == '__main__':
    unittest.main()
