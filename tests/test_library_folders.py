from pathlib import Path
import hashlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from catalog_service import CatalogService
from library_downloader import LibraryDownloadService


class LibraryFolderTests(unittest.TestCase):
    def test_catalog_scans_movies_and_preserves_legacy_paths_and_progress(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            for category, title in (('Movies', 'New'), ('Filmes', 'Legacy')):
                (root / category).mkdir()
                (root / category / (title + '.mkv')).write_bytes(b'video')
            cat = CatalogService(root, root / 'data', enable_online_metadata=False)
            legacy_id = hashlib.sha256(b'Filmes/Legacy.mkv').hexdigest()[:16]
            self.assertEqual(cat.scan(async_enrich=False), 2)
            cat.save_progress(legacy_id, 60, 3600)
            cat.scan(async_enrich=False)
            home = cat.get_home_catalog()
            self.assertEqual(home['library_paths']['movies'], str(root / 'Movies'))
            self.assertEqual({item['rel_path'] for item in cat.all_playable_entries()}, {'Movies/New.mkv', 'Filmes/Legacy.mkv'})
            record = cat.get_item_record(legacy_id)
            self.assertEqual(record['position'], 60)
            self.assertEqual(record['full_path'], str(root / 'Filmes' / 'Legacy.mkv'))
            self.assertEqual((root / 'Filmes' / 'Legacy.mkv').read_bytes(), b'video')

    def test_fresh_catalog_creates_english_library_folders(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            cat = CatalogService(root, root / 'data', enable_online_metadata=False)
            cat.scan(async_enrich=False)
            self.assertTrue((root / 'Movies').is_dir())
            self.assertTrue((root / 'Series').is_dir())
            self.assertFalse((root / 'Filmes').exists())

    def test_direct_download_targets_movies_and_series(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            service = LibraryDownloadService(root)
            try:
                movie, _ = service._build_target_path('movie', 'Paprika', 2006, None, None, '', 'Paprika.mkv', 'https://example.org/video')
                self.assertEqual(movie, root / 'Movies' / 'Paprika (2006)' / 'Paprika.mkv')
                episode, _ = service._build_target_path('series', 'Show', 2022, 1, 2, '', 'Show.S01E02.mkv', 'https://example.org/video')
                self.assertEqual(episode, root / 'Series' / 'Show (2022)' / 'Season 01' / 'Show.S01E02.mkv')
            finally:
                service.shutdown()
