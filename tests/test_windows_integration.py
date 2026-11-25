import json
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from windows_integration import WindowsIntegration, poll_registered


class FakeWindows:
    frozen = True
    def __init__(self, root):
        self.root = root
        self.host_dir = root / 'host'
        self.executable = root / 'disk' / 'Stream' / 'StreamChromeServer.exe'
        self.executable.parent.mkdir(parents=True)
        self.executable.write_bytes(b'trusted executable')
        (self.executable.parent / 'app/data').mkdir(parents=True)
        self.volumes = [(root / 'disk', '1234')]
        self.started = []
        self.run = None
    def volume_for(self, path):
        return self.volumes[0]
    def drives(self):
        return self.volumes
    def set_run(self, command):
        self.run = command
    def delete_run(self):
        self.run = None
    def start(self, executable, arguments=()):
        self.started.append((Path(executable), list(arguments)))


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.api = FakeWindows(self.root)
        self.icon = self.api.executable.parent / 'app.ico'
        self.icon.write_bytes(b'icon')
        self.integration = WindowsIntegration(self.api.executable.parent,
            self.api.executable.parent / 'app/data', self.icon, api=self.api)

    def test_register_tracks_volume_after_letter_change_and_removes_startup(self):
        self.assertFalse(self.integration.status()['autorun'])
        self.integration.configure({'autorun': True})
        self.assertIn('--watch-portable', self.api.run)
        new_root = self.root / 'new-letter'
        (self.root / 'disk').rename(new_root)
        self.api.volumes = [(new_root, '1234')]
        seen = poll_registered(self.api, set())
        self.assertEqual(self.api.started[-1][0], new_root / 'Stream/StreamChromeServer.exe')
        count = len(self.api.started)
        poll_registered(self.api, seen)
        self.assertEqual(len(self.api.started), count)
        self.api.executable = new_root / 'Stream/StreamChromeServer.exe'
        self.integration = WindowsIntegration(self.api.executable.parent,
            self.api.executable.parent / 'app/data', new_root / 'Stream/app.ico', api=self.api)
        self.integration.configure({'autorun': False})
        self.assertIsNone(self.api.run)
        self.assertEqual(json.loads((self.api.host_dir / 'registrations.json').read_text()), [])

    def test_icon_preserves_original_and_refuses_overwrite_after_external_edit(self):
        autorun = self.root / 'disk/autorun.inf'
        original = b'[autorun]\r\nlabel=My disk\r\n'
        autorun.write_bytes(original)
        self.integration.configure({'drive_icon': True})
        self.assertIn('icon=Stream/app.ico', autorun.read_text())
        self.integration.configure({'drive_icon': False})
        self.assertEqual(autorun.read_bytes(), original)
        self.integration.configure({'drive_icon': True})
        autorun.write_text('[autorun]\nlabel=changed')
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.integration.configure({'drive_icon': False})
        self.assertEqual(autorun.read_text(), '[autorun]\nlabel=changed')

    def test_existing_icon_is_conflict_and_source_mode_is_rejected(self):
        (self.root / 'disk/autorun.inf').write_text('[autorun]\nicon=other.ico\n')
        with self.assertRaisesRegex(ValueError, 'icon'):
            self.integration.configure({'drive_icon': True})
        self.api.frozen = False
        with self.assertRaisesRegex(ValueError, 'compiled'):
            self.integration.configure({'autorun': True})

    def test_replaced_executable_and_traversal_never_launch(self):
        self.integration.configure({'autorun': True})
        self.api.executable.write_bytes(b'replaced')
        count = len(self.api.started)
        poll_registered(self.api, set())
        self.assertEqual(len(self.api.started), count)
        path = self.api.host_dir / 'registrations.json'
        path.write_text(json.dumps([{'serial': '1234', 'relative_exe': '../outside.exe', 'sha256': ''}]))
        poll_registered(self.api, set())
        self.assertEqual(len(self.api.started), count)

    def test_standard_layout_cannot_register_or_modify_drive(self):
        standard = WindowsIntegration(self.api.executable.parent, self.root / 'profile', self.icon, api=self.api)
        self.assertFalse(standard.status()['available'])
        with self.assertRaisesRegex(ValueError, 'portable'):
            standard.configure({'autorun': True})
        self.assertFalse(self.api.host_dir.exists())
        self.assertFalse((self.root / 'disk/autorun.inf').exists())

    def test_unknown_volume_and_unregistered_executable_never_launch(self):
        self.integration.configure({'autorun': True})
        self.api.volumes = [(self.root / 'disk', 'not-registered')]
        count = len(self.api.started)
        self.assertEqual(poll_registered(self.api, set()), set())
        self.assertEqual(len(self.api.started), count)

    def test_refresh_never_enables_unregistered_portable_app(self):
        self.integration.refresh_registration()
        self.assertFalse(self.api.host_dir.exists())
        self.assertFalse(self.integration.status()['autorun'])
        self.assertEqual(self.api.started, [])
        self.assertIsNone(self.api.run)

    def test_refresh_leaves_unchanged_registration_and_helper_alone(self):
        self.integration.configure({'autorun': True})
        path = self.api.host_dir / 'registrations.json'
        original = path.read_bytes()
        count = len(self.api.started)
        command = self.api.run
        self.integration.refresh_registration()
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(len(self.api.started), count)
        self.assertEqual(self.api.run, command)

    def test_refresh_updates_registered_executable_hash_after_upgrade(self):
        self.integration.configure({'autorun': True})
        old_command = self.api.run
        self.api.executable.write_bytes(b'updated trusted executable')
        self.integration.refresh_registration()
        self.assertTrue(self.integration.status()['autorun'])
        self.assertNotEqual(self.api.run, old_command)
        self.assertEqual(len(self.api.started), 2)
        poll_registered(self.api, set())
        self.assertEqual(self.api.started[-1][0], self.api.executable)
