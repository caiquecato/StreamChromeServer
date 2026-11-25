from pathlib import Path
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from jellyfin_bridge import JellyfinBridge


class JellyfinLayoutTests(unittest.TestCase):
    def test_explicit_port_preserves_other_network_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bridge = JellyfinBridge(root, 'ffmpeg', root / 'link.json', root / 'settings.dat', port=19096)
            bridge.config_dir.mkdir()
            config = bridge.config_dir / 'network.xml'
            config.write_text('<NetworkConfiguration><InternalHttpPort>8096</InternalHttpPort><EnableIPv6>false</EnableIPv6></NetworkConfiguration>')
            bridge._configure_http_port()
            fields = ET.parse(config).getroot()
            self.assertEqual(fields.findtext('InternalHttpPort'), '19096')
            self.assertEqual(fields.findtext('PublicHttpPort'), '19096')
            self.assertEqual(fields.findtext('EnableIPv6'), 'false')
    def test_official_zip_layout_can_run_without_repacking(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'jellyfin.exe').write_bytes(b'demo')
            bridge = JellyfinBridge(root, 'ffmpeg', root / 'link.json', root / 'settings.dat')
            self.assertEqual(bridge.exe, root / 'jellyfin.exe')

    def test_standard_runtime_can_live_outside_binary_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bridge = JellyfinBridge(root / 'program', 'ffmpeg', root / 'link.json', root / 'settings.dat', runtime_dir=root / 'profile')
            self.assertEqual(bridge.data_dir, root / 'profile' / 'data')
            self.assertEqual(bridge.exe, root / 'program' / 'system' / 'jellyfin.exe')

    def test_portable_runtime_preserves_original_location(self):
        bridge = JellyfinBridge(Path('app/jellyfin'), 'ffmpeg', 'link.json', 'settings.dat')
        self.assertEqual(bridge.data_dir, Path('app/jellyfin/data'))
