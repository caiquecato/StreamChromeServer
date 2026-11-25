from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import server_pro as app


class RuntimeLayoutTests(unittest.TestCase):
    def test_official_ffmpeg_zip_is_preferred_over_system_installations(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            binary = root / 'ffmpeg/ffmpeg-9.0.2-essentials_build/bin/ffmpeg.exe'
            binary.parent.mkdir(parents=True)
            binary.write_bytes(b'demo')
            with patch.object(app, 'ENGINE_DIR', root), patch.object(app, 'APP_DIRECTORY', root):
                self.assertEqual(Path(app.find_media_tool('ffmpeg')), binary)
