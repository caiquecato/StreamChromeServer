import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from app_updates import Updater, run_update_helper, _TrustedRedirect, _open_url


def archive(name='StreamChromeServer.exe'):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as z:
        z.writestr(name, b'new executable')
    return stream.getvalue()


class UpdateTests(unittest.TestCase):
    def release(self, payload, digest=True, version='1.1.0'):
        asset = {'name': f'StreamChromeServer-{version}-app.zip', 'browser_download_url': 'https://github.com/caiquecato/StreamChromeServer/releases/download/v'+version+'/app.zip'}
        if digest:
            asset['digest'] = 'sha256:' + hashlib.sha256(payload).hexdigest()
        return {'tag_name': 'v'+version, 'html_url': 'https://github.com/caiquecato/StreamChromeServer/releases/tag/v'+version, 'assets': [asset]}

    def test_check_requires_digest_and_prevents_downgrade(self):
        with tempfile.TemporaryDirectory() as tmp, patch('app_updates.sys.frozen', True, create=True):
            updater = Updater(tmp, Path(tmp)/'data', '1.0.0')
            for release in (self.release(archive(), digest=False), self.release(archive(), version='0.9.0')):
                with patch('app_updates._read_release', return_value=release):
                    self.assertFalse(updater.check()['available'])

    def test_release_without_app_asset_is_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp, patch('app_updates.sys.frozen', True, create=True):
            updater = Updater(tmp, Path(tmp)/'data', '1.0.0')
            with patch('app_updates._read_release', return_value={'tag_name':'v1.1.0','assets':[]}):
                self.assertFalse(updater.check()['available'])

    def test_new_release_is_available(self):
        with tempfile.TemporaryDirectory() as tmp, patch('app_updates.sys.frozen', True, create=True):
            updater = Updater(tmp, Path(tmp)/'data', '1.0.0')
            with patch('app_updates._read_release', return_value=self.release(archive())):
                result = updater.check()
            self.assertTrue(result['available'])
            self.assertEqual(result['latest_version'], '1.1.0')

    def test_bad_digest_or_archive_preserves_current_executable(self):
        with tempfile.TemporaryDirectory() as tmp, patch('app_updates.sys.frozen', True, create=True):
            root = Path(tmp)
            exe = root/'StreamChromeServer.exe'
            exe.write_bytes(b'original')
            updater = Updater(root, root/'data', '1.0.0')
            good = archive()
            for payload, release in ((b'corrupt', self.release(good)), (archive('../StreamChromeServer.exe'), self.release(archive('../StreamChromeServer.exe')))):
                with patch('app_updates._read_release', return_value=release), patch('app_updates._open_url', return_value=io.BytesIO(payload)):
                    with self.assertRaises(ValueError):
                        updater.apply()
                self.assertEqual(exe.read_bytes(), b'original')

    def test_source_checkout_cannot_apply(self):
        with tempfile.TemporaryDirectory() as tmp, patch('app_updates.sys.frozen', False, create=True):
            with self.assertRaises(ValueError):
                Updater(tmp, Path(tmp)/'data', '1.0.0').apply()

    def test_helper_replaces_only_executable_and_relaunches(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve()
            exe=root/'StreamChromeServer.exe'
            exe.write_bytes(b'original')
            candidate=root/'candidate.exe'
            candidate.write_bytes(b'new executable')
            media=root/'Movies'; media.mkdir(); (media/'keep.mkv').write_bytes(b'media')
            spec=root/'update.json'
            spec.write_text(json.dumps({'pid':123,'target':str(exe),'candidate':str(candidate),'sha256':hashlib.sha256(candidate.read_bytes()).hexdigest()}))
            with patch('app_updates._wait_for_parent'), patch('app_updates.subprocess.Popen') as launch:
                run_update_helper(str(spec))
            self.assertEqual(exe.read_bytes(), b'new executable')
            self.assertEqual((media/'keep.mkv').read_bytes(), b'media')
            launch.assert_called_once_with([str(exe), '--refresh-integration'], cwd=str(root))

    def test_helper_rolls_back_when_relaunch_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve(); exe=root/'StreamChromeServer.exe'; exe.write_bytes(b'original')
            candidate=root/'candidate.exe'; candidate.write_bytes(b'new')
            spec=root/'update.json'
            spec.write_text(json.dumps({'pid':123,'target':str(exe),'candidate':str(candidate),'sha256':hashlib.sha256(b'new').hexdigest()}))
            with patch('app_updates._wait_for_parent'), patch('app_updates.subprocess.Popen', side_effect=OSError('launch failed')):
                with self.assertRaises(OSError): run_update_helper(str(spec))
            self.assertEqual(exe.read_bytes(), b'original')

    def test_apply_starts_detached_helper_with_verified_candidate(self):
        with tempfile.TemporaryDirectory() as tmp, patch('app_updates.sys.frozen', True, create=True):
            root = Path(tmp); exe = root/'StreamChromeServer.exe'; exe.write_bytes(b'original')
            payload = archive()
            with patch('app_updates._read_release', return_value=self.release(payload)), patch('app_updates._open_url', return_value=io.BytesIO(payload)), patch('app_updates.subprocess.Popen') as launch, patch('app_updates.sys.executable', str(exe)):
                with patch('app_updates.sys.argv', ['app.exe', '--port', '9011', '--local-only', '--no-browser', '--background']):
                    result = Updater(root, root/'data', '1.0.0').apply()
            self.assertEqual(result, {'restarting':True, 'latest_version':'1.1.0'})
            args = launch.call_args.args[0]
            self.assertEqual(args[1], '--apply-update')
            spec = json.loads(Path(args[2]).read_text())
            self.assertEqual(Path(spec['candidate']).read_bytes(), b'new executable')
            self.assertEqual(spec['restart_args'], ['--port', '9011', '--local-only', '--no-browser', '--background'])
            self.assertEqual(exe.read_bytes(), b'original')

    def test_untrusted_initial_urls_and_redirects_are_rejected(self):
        for url in ('http://github.com/asset', 'https://github.com.evil.test/a', 'https://user@github.com/a', 'https://example.org/a'):
            with self.subTest(url=url):
                with self.assertRaises(ValueError): _open_url(url)
                with self.assertRaises(ValueError): _TrustedRedirect().redirect_request(None, None, 302, '', {}, url)

    def test_apply_rejects_unverified_release_and_downgrade_before_download(self):
        with tempfile.TemporaryDirectory() as tmp, patch('app_updates.sys.frozen', True, create=True):
            root = Path(tmp); exe = root/'StreamChromeServer.exe'; exe.write_bytes(b'original')
            for release in (self.release(archive(), digest=False), self.release(archive(), version='0.9.0')):
                with patch('app_updates._read_release', return_value=release), patch('app_updates._open_url') as download:
                    with self.assertRaises(ValueError): Updater(root, root/'data', '1.0.0').apply()
                    download.assert_not_called()
                    self.assertEqual(exe.read_bytes(), b'original')

    def test_helper_preserves_network_and_launch_options(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve(); exe=root/'StreamChromeServer.exe'; exe.write_bytes(b'original')
            candidate=root/'candidate.exe'; candidate.write_bytes(b'new')
            args=['--port','9011','--jellyfin-port','9012','--local-only','--no-browser','--background']
            spec=root/'update.json'
            spec.write_text(json.dumps({'pid':123,'target':str(exe),'candidate':str(candidate),'sha256':hashlib.sha256(b'new').hexdigest(),'restart_args':args}))
            with patch('app_updates._wait_for_parent'), patch('app_updates.subprocess.Popen') as launch:
                run_update_helper(str(spec))
            launch.assert_called_once_with([str(exe), '--refresh-integration', *args], cwd=str(root))

    def test_helper_rejects_unexpected_restart_options_before_replacement(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve(); exe=root/'StreamChromeServer.exe'; exe.write_bytes(b'original')
            candidate=root/'candidate.exe'; candidate.write_bytes(b'new')
            for args in (['--apply-update','bad.json'], ['--port','0'], ['--port=65536'], 'bad', ['--port']):
                spec=root/'update.json'
                spec.write_text(json.dumps({'pid':123,'target':str(exe),'candidate':str(candidate),'sha256':hashlib.sha256(b'new').hexdigest(),'restart_args':args}))
                with patch('app_updates._wait_for_parent'), patch('app_updates.subprocess.Popen') as launch:
                    with self.assertRaises(ValueError): run_update_helper(str(spec))
                    launch.assert_not_called()
                self.assertEqual(exe.read_bytes(), b'original')
