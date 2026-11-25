import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import sys

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'build_release.py'


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(SCRIPT.is_file(), 'Release staging script must exist')
        spec = importlib.util.spec_from_file_location('build_release', SCRIPT)
        self.release = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.release)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.engines = self.root / 'engines'
        self.exe = self.root / 'StreamChromeServer.exe'
        self.exe.write_bytes(b'app executable')
        for relative in ('ffmpeg/ffmpeg.exe', 'ffmpeg/ffprobe.exe', 'yt-dlp/yt-dlp.exe',
                         'cloudflared/cloudflared.exe', 'jellyfin/system/jellyfin.exe',
                         'jellyfin/system/jellyfin.deps.json', 'jellyfin/system/jellyfin-web/index.html'):
            path = self.engines / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'distribution asset')
        self.notice = self.root / 'notice.md'
        self.notice.write_text('Third party notices', encoding='utf-8')

    def stage(self, destination):
        return self.release.stage_release(self.exe, self.engines, destination, self.notice)

    def test_only_distributed_binaries_are_staged_and_private_data_stays_out(self):
        for relative in ('data/server_settings.dat', 'ffmpeg/cookies.txt', 'yt-dlp/config.json',
                         'jellyfin/config/system.xml', 'jellyfin/data/users.db',
                         'jellyfin/logs/log.txt', 'jellyfin/theme/private.css'):
            path = self.engines / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('SECRET', encoding='utf-8')
        out = self.root / 'release'
        self.stage(out)
        self.assertEqual({p.relative_to(out).as_posix() for p in out.rglob('*') if p.is_file()}, {
            'StreamChromeServer.exe', 'app.ico', 'THIRD_PARTY_LICENSES.md', 'app/ffmpeg/ffmpeg.exe',
            'app/ffmpeg/ffprobe.exe', 'app/yt-dlp/yt-dlp.exe', 'app/cloudflared/cloudflared.exe',
            'app/jellyfin/system/jellyfin.exe', 'app/jellyfin/system/jellyfin.deps.json',
            'app/jellyfin/system/jellyfin-web/index.html'})
        self.assertTrue((out / 'app/data').is_dir())
        self.assertTrue((out / 'Movies').is_dir())
        self.assertTrue((out / 'Series').is_dir())

    def test_existing_destination_is_never_modified(self):
        out = self.root / 'release'
        out.mkdir()
        existing = out / 'server_settings.dat'
        existing.write_text('keep', encoding='utf-8')
        with self.assertRaises(FileExistsError):
            self.stage(out)
        self.assertEqual(existing.read_text(), 'keep')
        self.assertEqual(list(out.iterdir()), [existing])

    def test_notices_and_available_javascript_runtime_are_preserved(self):
        for relative in ('ffmpeg/LICENSE', 'ffmpeg/README.txt', 'yt-dlp/LICENSE',
                         'yt-dlp/THIRD_PARTY_LICENSES.txt', 'yt-dlp/deno.exe',
                         'yt-dlp/DENO-LICENSE.md', 'cloudflared/LICENSE',
                         'licenses/runtime/Python-LICENSE.txt'):
            path = self.engines / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('distributed file', encoding='utf-8')
        out = self.root / 'release'
        self.stage(out)
        self.assertTrue((out / 'app/ffmpeg/LICENSE').is_file())
        self.assertTrue((out / 'app/yt-dlp/THIRD_PARTY_LICENSES.txt').is_file())
        self.assertTrue((out / 'app/yt-dlp/deno.exe').is_file())
        self.assertTrue((out / 'app/cloudflared/LICENSE').is_file())
        self.assertTrue((out / 'app/licenses/runtime/Python-LICENSE.txt').is_file())

    def test_app_only_build_does_not_require_or_read_engines(self):
        out = self.root / 'app-only'
        def compile_fixture(command, **kwargs):
            target = Path(command[command.index('--distpath') + 1]) / 'StreamChromeServer.exe'
            target.parent.mkdir(parents=True)
            target.write_bytes(b'compiled fixture')
        with patch.object(sys, 'argv', ['build_release.py', '--app-only', '--output', str(out)]), \
                patch.object(self.release.subprocess, 'run', side_effect=compile_fixture):
            self.release.main()
        self.assertEqual((out / 'StreamChromeServer.exe').read_bytes(), b'compiled fixture')
        self.assertFalse((out / 'app').exists())
        self.assertFalse(list(out.glob('*.zip')))

    def test_missing_engine_fails_before_creating_release(self):
        (self.engines / 'ffmpeg/ffprobe.exe').unlink()
        out = self.root / 'release'
        with self.assertRaises(FileNotFoundError):
            self.stage(out)
        self.assertFalse(out.exists())

    def test_runtime_data_inside_jellyfin_system_is_rejected(self):
        path = self.engines / 'jellyfin/system/config/secret.json'
        path.parent.mkdir()
        path.write_text('SECRET', encoding='utf-8')
        with self.assertRaises(ValueError):
            self.stage(self.root / 'release')
        self.assertFalse((self.root / 'release').exists())

    def test_link_outside_engine_tree_is_rejected(self):
        secret = self.root / 'secret.txt'
        secret.write_text('SECRET', encoding='utf-8')
        link = self.engines / 'jellyfin/system/jellyfin-web/secret.txt'
        try:
            link.symlink_to(secret)
        except OSError:
            self.skipTest('Windows symlink privilege unavailable')
        with self.assertRaises(ValueError):
            self.stage(self.root / 'release')
        self.assertFalse((self.root / 'release').exists())


if __name__ == '__main__':
    unittest.main()
