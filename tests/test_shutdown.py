import http.server
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import process_guard
from library_downloader import LibraryDownloadService
from downloads import DownloadService


class CentralShutdownTests(unittest.TestCase):
    def test_actual_tray_exit_callback_stops_both_downloaders_and_server(self):
        import server_pro as app
        completed = threading.Event()
        stopped = []
        def record(name):
            return lambda: stopped.append(name)
        server = SimpleNamespace(shutdown=completed.set, allow_force_exit=False)
        fake_tray = SimpleNamespace(
            MenuItem=lambda label, callback, **kwargs: (label, callback),
            Menu=lambda *items: items,
            Icon=lambda *args, **kwargs: SimpleNamespace(menu=kwargs['menu'], run=lambda: None),
        )
        with patch.dict(sys.modules, {'pystray': fake_tray}), \
                patch('PIL.Image.open', return_value=object()), \
                patch.object(app, 'LIBRARY_DOWNLOADS', SimpleNamespace(shutdown=record('library'))), \
                patch.object(app, 'DOWNLOADS', SimpleNamespace(shutdown=record('links'))), \
                patch.object(app, 'REMOTE', SimpleNamespace(stop=record('remote'))), \
                patch.object(app, 'JELLYFIN', SimpleNamespace(stop=record('jellyfin'), port=8096)):
            icon = app.start_tray(server, 8780)
            label, callback = icon.menu[-1]
            self.assertEqual(label, 'Exit')
            callback(icon, None)
            self.assertTrue(completed.wait(2), 'Tray Exit did not reach server.shutdown')
            self.assertEqual(stopped, ['library', 'links', 'remote', 'jellyfin'])
            self.assertFalse(hasattr(server, '_exit_timer'))


class ManagedProcessTests(unittest.TestCase):
    def test_managed_run_preserves_output_check_and_timeout(self):
        self.assertTrue(hasattr(process_guard, 'run_managed'))
        result = process_guard.run_managed([sys.executable, '-c', 'print("hello")'], capture_output=True, text=True)
        self.assertEqual(result.stdout.strip(), 'hello')
        with self.assertRaises(subprocess.CalledProcessError) as failed:
            process_guard.run_managed([sys.executable, '-c', 'import sys;print("failed");sys.exit(7)'], capture_output=True, check=True, text=True)
        self.assertEqual(failed.exception.returncode, 7)
        self.assertIn('failed', failed.exception.stdout)
        with self.assertRaises(subprocess.TimeoutExpired):
            process_guard.run_managed([sys.executable, '-c', 'import time;time.sleep(20)'], capture_output=True, timeout=.1)

    @unittest.skipUnless(os.name == 'nt', 'Windows job protection')
    def test_binding_failure_kills_child_before_raising(self):
        self.assertTrue(hasattr(process_guard, 'run_managed'))
        with patch.object(process_guard, 'attach_to_server', return_value=False):
            with self.assertRaises(OSError):
                process_guard.run_managed([sys.executable, '-c', 'import time;time.sleep(20)'])


class LibraryShutdownTests(unittest.TestCase):
    def test_link_download_shutdown_rejects_new_work(self):
        service = DownloadService('unused', 'ffmpeg')
        self.assertTrue(hasattr(service, 'shutdown'))
        service.shutdown()
        service.shutdown()
        with self.assertRaisesRegex(ValueError, 'shut|stop'):
            service.analyze('https://example.org/video')

    def test_shutdown_is_idempotent_and_rejects_new_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = LibraryDownloadService(tmp)
            self.assertTrue(hasattr(service, 'shutdown'))
            service.shutdown()
            service.shutdown()
            with self.assertRaisesRegex(ValueError, 'shut|stop'):
                service.enqueue_from_token({})

    def test_slow_download_shutdown_unblocks_reads_preserves_partial_and_process_exits(self):
        released = threading.Event()
        class SlowHandler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header('Content-Length', str(100 * 1024 * 1024))
                self.end_headers()
                self.wfile.write(b'x' * (1024 * 1024))
                self.wfile.flush()
                released.wait(15)
            def log_message(self, *args):
                pass
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), SlowHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                script = '''import sys,time,json
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,sys.argv[1])
from library_downloader import LibraryDownloadService
s=LibraryDownloadService(sys.argv[2])
with patch('library_downloader.get_addon_stream',return_value={'url':sys.argv[3],'filename':'test.mkv'}):
    j=s.enqueue_from_token({'stream_token':'test','title':'Test'})
deadline=time.monotonic()+5
while s.list_jobs()[0]['downloaded'] < 1048576 and time.monotonic()<deadline: time.sleep(.01)
assert s.list_jobs()[0]['downloaded'] >= 1048576, s.list_jobs()
start=time.monotonic()
s.shutdown();s.shutdown()
assert time.monotonic()-start < 1
print('shutdown',flush=True)
'''
                proc = subprocess.Popen([sys.executable, '-c', script, str(Path(__file__).resolve().parents[1] / 'src'), tmp,
                                         f'http://127.0.0.1:{server.server_port}/slow'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                try:
                    stdout, stderr = proc.communicate(timeout=8)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    stdout, stderr = proc.communicate()
                    self.fail('Shutdown kept download process alive: ' + stderr)
                self.assertEqual(proc.returncode, 0, stderr)
                self.assertIn('shutdown', stdout)
                parts = list(Path(tmp).rglob('*.part'))
                self.assertEqual(len(parts), 1)
                self.assertGreaterEqual(parts[0].stat().st_size, 1048576)
        finally:
            released.set()
            server.shutdown()
            server.server_close()


if __name__ == '__main__':
    unittest.main()
