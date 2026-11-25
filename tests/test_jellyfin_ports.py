from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from remote_access import RemoteAccessService
from jellyfin_bridge import JellyfinBridge


class JellyfinPortTests(unittest.TestCase):
    def test_jellyfin_tunnel_first_uses_current_port(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            remote=RemoteAccessService(root/'settings.dat', cloudflared_bin=root/'missing.exe')
            remote.jellyfin_port=19096
            with self.assertRaisesRegex(ValueError, 'not found'):
                remote.start('jellyfin', jellyfin_password_ready=True)
            self.assertEqual(remote._slots['jellyfin'].target_url, 'http://127.0.0.1:19096')

    def test_returning_to_default_port_reconciles_existing_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            bridge=JellyfinBridge(root, 'ffmpeg', root/'link.json', root/'settings.dat', port=19096)
            bridge._configure_http_port()
            bridge.port=8096
            with patch.object(bridge, 'is_available', return_value=True), patch.object(bridge, 'is_running', return_value=False), patch('jellyfin_bridge.subprocess.Popen', side_effect=FileNotFoundError):
                with self.assertRaises(FileNotFoundError):
                    bridge.start()
            self.assertIn('<InternalHttpPort>8096</InternalHttpPort>', (bridge.config_dir/'network.xml').read_text())
