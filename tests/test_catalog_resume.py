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


if __name__ == '__main__': unittest.main()
