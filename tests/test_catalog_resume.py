from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from catalog_service import CatalogService


class ResumeTests(unittest.TestCase):
    def test_short_progress_and_every_title_in_carousel(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'Filmes').mkdir()
            (root / 'Series' / 'Show' / 'Season 01').mkdir(parents=True)
            for name in ('First', 'Second'):
                (root / 'Filmes' / (name + '.mkv')).write_bytes(b'test')
            for number in (1, 2):
                (root / 'Series' / 'Show' / 'Season 01' / f'Show.S01E{number:02}.mkv').write_bytes(b'test')
            cat = CatalogService(root, root / 'data', enable_online_metadata=False)
            with patch.object(cat, '_probe_video', return_value={'duration': 7200}):
                cat.scan(async_enrich=False)
            home = cat.get_home_catalog()
            cat.save_progress(home['movies'][0]['id'], 8, 7200)
            home = cat.get_home_catalog()
            self.assertEqual(len(home['continue_watching']), 1)
            self.assertEqual(len(home['hero_items']), 3)
            self.assertEqual(sum(i['kind'] == 'series' for i in home['hero_items']), 1)

    def test_series_frame_cache_does_not_block_official_metadata_poster(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / 'Series' / 'Show'
            folder.mkdir(parents=True)
            (folder / 'Show.S01E01.mkv').write_bytes(b'test')
            cat = CatalogService(root, root / 'data', enable_online_metadata=False)
            with patch.object(cat, '_probe_video', return_value={}), patch.object(cat, '_extract_still', return_value='thumb_frame.jpg'):
                cat.scan(async_enrich=False)
                cat._enrich_worker()
            (folder / 'Show.S01E02.mkv').write_bytes(b'test')
            with patch.object(cat, '_probe_video', return_value={}), patch.object(cat, '_extract_still', return_value='thumb_frame.jpg'), patch.object(cat, '_fetch_online_meta', return_value={'poster_file':'official.jpg', 'imdb_id':'tt1234567'}):
                cat.scan(async_enrich=False)
                cat._enrich_worker()
            posters = [i['poster_url'] for i in cat.get_home_catalog()['hero_items']]
            self.assertIn('/api/poster/official.jpg', posters)


if __name__ == '__main__': unittest.main()
