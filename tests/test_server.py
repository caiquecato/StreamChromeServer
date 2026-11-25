import concurrent.futures
import http.client
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import server_pro as server

class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = server.HTTPServer(('127.0.0.1', 0), server.StreamProHandler)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join()

    def request(self, path, data=None, extra_headers=None):
        conn = http.client.HTTPConnection(*self.httpd.server_address, timeout=4)
        headers = {'X-StreamChrome': '1', 'Content-Type': 'application/json'}
        headers.update(extra_headers or {})
        conn.request('POST' if data is not None else 'GET', path, json.dumps(data) if data is not None else None, headers)
        response = conn.getresponse()
        result = response.status, response.read()
        conn.close()
        return result

    def test_local_selection_registers_token_and_never_sends_full_path(self):
        with tempfile.TemporaryDirectory() as directory:
            video = Path(directory) / 'Movie.2024.mkv'
            video.write_bytes(b'test')
            with patch.object(server, 'choose_local_video', return_value=str(video)):
                status, body = self.request('/api/local/open', {})
            self.assertEqual(status, 200)
            data = json.loads(body)
            self.assertEqual(data['filename'], video.name)
            self.assertNotIn(directory.encode(), body)
            self.assertEqual(server.resolve_target_url(data['source']), str(video.resolve()))

    def test_split_remote_stream_maps_external_audio_and_seeks_both_inputs(self):
        metadata={'video_codec':'av1','audio_tracks':[{'index':0}],'audio_url':'https://example.org/audio','headers':'User-Agent: Test\r\n','audio_headers':'User-Agent: Test\r\n'}
        command=server.build_stream_command('https://example.org/video',metadata,12)
        self.assertEqual(command.count('-i'),2)
        self.assertEqual(command.count('-ss'),2)
        self.assertEqual(command.count('-noaccurate_seek'),2)
        self.assertIn('1:a:0',command)
        self.assertIn('copy',command)

    def test_unregistered_local_paths_are_rejected(self):
        with self.assertRaises(ValueError):
            server.resolve_target_url('file:///C:/private.mkv')
        with self.assertRaises(ValueError):
            server.resolve_target_url('C:\\private.mkv')
        with self.assertRaises(ValueError):
            server.resolve_target_url('local:missing-token')

    def test_websites_cannot_trigger_local_dialog(self):
        with patch.object(server, 'choose_local_video') as dialog:
            status, _ = self.request('/api/local/open', {}, {'Origin': 'https://example.org'})
        self.assertEqual(status, 403)
        dialog.assert_not_called()

    def test_top_level_navigation_can_open_player_but_not_private_apis(self):
        status, body = self.request('/', extra_headers={'Sec-Fetch-Site': 'cross-site'})
        self.assertEqual(status, 200)
        self.assertIn(b'StreamChrome', body)
        status, _ = self.request('/api/settings', extra_headers={'Sec-Fetch-Site': 'cross-site'})
        self.assertEqual(status, 403)

    def test_host_guard_rejects_dns_rebinding(self):
        status, _ = self.request('/api/settings', extra_headers={'Host': 'attacker.example'})
        self.assertEqual(status, 403)

    def test_settings_response_contains_no_secret(self):
        status, body = self.request('/api/settings')
        self.assertEqual(status, 200)
        self.assertEqual(set(json.loads(body)), {'subdl', 'opensubtitles', 'opensubtitles_login'})

    def test_cancelled_dialog_is_not_a_failure(self):
        with patch.object(server, 'choose_local_video', return_value=''):
            status, body = self.request('/api/local/open', {})
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)['cancelled'])

    def test_long_running_request_does_not_block_other_requests(self):
        entered = threading.Event()
        release = threading.Event()
        def slow_search(data):
            entered.set()
            release.wait(3)
            return {'subtitles': [], 'warnings': []}
        with patch.object(server.SUBTITLES, 'search', side_effect=slow_search), concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            job = pool.submit(self.request, '/api/subtitles/search', {'title': 'Dune'})
            self.assertTrue(entered.wait(1))
            start = time.monotonic()
            try:
                self.assertEqual(self.request('/api/settings')[0], 200)
                self.assertLess(time.monotonic() - start, 1)
            finally:
                release.set()
            self.assertEqual(job.result()[0], 200)

    def test_original_hevc_is_not_reencoded_by_default(self):
        metadata = {'video_codec':'hevc', 'audio_tracks':[]}
        command = server.build_stream_command('movie.mkv', metadata, 0)
        self.assertEqual(command[command.index('-c:v') + 1], 'copy')
        self.assertIn('hvc1', command)
        fallback = server.build_stream_command('movie.mkv', metadata, 0, compatibility=True)
        self.assertEqual(fallback[fallback.index('-c:v') + 1], 'libx264')

    def test_invalid_seek_is_rejected(self):
        status, _ = self.request('/stream?url=https%3A%2F%2Fexample.org%2Fmovie.mkv&t=nan')
        self.assertEqual(status, 400)

    def test_catalog_scan_series_sidecar_and_range_media(self):
        from catalog_service import CatalogService
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'StreamServer'
            (root / 'Filmes').mkdir(parents=True)
            show_dir = root / 'Series' / 'Severance' / 'Season 01'
            show_dir.mkdir(parents=True)
            movie = root / 'Filmes' / 'Paprika.2006.mp4'
            movie.write_bytes(b'0123456789ABCDEF')
            ep = show_dir / 'Severance.S01E01.mp4'
            ep.write_bytes(b'episode-bytes')
            sub = root / 'Filmes' / 'Paprika.2006.pt-BR.srt'
            sub.write_text('1\n00:00:01,000 --> 00:00:02,000\nOla\n', encoding='utf-8')
            cat = CatalogService(root, Path(tmp) / 'data', enable_online_metadata=False)
            self.assertEqual(cat.scan(async_enrich=False), 2)
            with patch.object(server, 'CATALOG', cat), patch.object(server, 'SERVER_ROOT', root):
                st, body = self.request('/api/catalog')
                self.assertEqual(st, 200)
                home = json.loads(body)
                self.assertEqual(home['stats']['movies'], 1)
                self.assertEqual(home['stats']['series'], 1)
                m_item = home['movies'][0]
                self.assertEqual(len(m_item['subtitles']), 1)

                st_s, s_body = self.request('/api/catalog/series?key=' + home['series'][0]['series_key'])
                self.assertEqual(st_s, 200)
                self.assertEqual(json.loads(s_body)['seasons'][0]['episodes'][0]['episode'], 1)

                st_o, o_body = self.request('/api/catalog/open', {'id': m_item['id']})
                self.assertEqual(st_o, 200)
                self.assertNotIn(tmp.encode(), o_body)

                st_sub, sub_body = self.request('/api/catalog/subtitle?id=' + m_item['subtitles'][0]['id'])
                self.assertEqual(st_sub, 200)
                self.assertIn(b'WEBVTT', sub_body)

                st_r, r_body = self.request('/media/' + m_item['id'], extra_headers={'Range': 'bytes=4-9'})
                self.assertEqual(st_r, 206)
                self.assertEqual(r_body, b'456789')

    def test_remote_pin_gate_and_lan_host_guard(self):
        from remote_access import RemoteAccessService
        port = self.httpd.server_address[1]
        with tempfile.TemporaryDirectory() as tmp:
            rem = RemoteAccessService(Path(tmp) / 'settings.dat', hub_port=port)
            rem.set_pin('1234')
            rem._slots['hub'].host = 'demo.trycloudflare.com'
            prev_lan = self.httpd.lan_mode
            self.httpd.lan_mode = True
            try:
                with patch.object(server, 'REMOTE', rem):
                    self.assertEqual(self.request('/api/settings', extra_headers={'Host': f'192.168.1.3:{port}'})[0], 200)
                    self.assertEqual(self.request('/api/settings', extra_headers={'Host': f'attacker.example:{port}'})[0], 403)
                    # External Cloudflare tunnel requires PIN cookie
                    ext_hdr = {'Host': f'demo.trycloudflare.com', 'Cf-Connecting-Ip': '8.8.8.8'}
                    self.assertEqual(self.request('/api/catalog', extra_headers=ext_hdr)[0], 401)
                    st_page, page = self.request('/', extra_headers=ext_hdr)
                    self.assertEqual(st_page, 200)
                    self.assertIn(b'pinForm', page)

                    # Authenticate with PIN and get cookie
                    conn = http.client.HTTPConnection(*self.httpd.server_address, timeout=4)
                    conn.request('POST', '/api/remote/auth', json.dumps({'pin': '1234'}), {'Content-Type': 'application/json', 'X-StreamChrome': '1', **ext_hdr})
                    resp = conn.getresponse()
                    cookie = resp.getheader('Set-Cookie').split(';', 1)[0]
                    resp.read()
                    conn.close()
                    self.assertEqual(resp.status, 200)
                    self.assertEqual(self.request('/api/settings', extra_headers={**ext_hdr, 'Cookie': cookie})[0], 200)
                    remote_headers = {**ext_hdr, 'Cookie': cookie}
                    with patch.object(server.JELLYFIN, 'status', return_value={'port': 8096}), \
                         patch.object(server.JELLYFIN, 'current_password', return_value='dummy-private-password') as password, \
                         patch.object(server, 'detect_nvenc', return_value=False), \
                         patch.object(server, 'get_lan_ipv4', return_value='192.168.1.3'):
                        status, body = self.request('/api/network/info', extra_headers=remote_headers)
                        self.assertEqual(status, 200)
                        self.assertNotIn(b'dummy-private-password', body)
                        password.assert_not_called()
                    with patch.object(server, 'choose_local_video', return_value='') as dialog, \
                         patch.object(server.SUBTITLES, 'configure', return_value={}) as configure, \
                         patch.object(server.ADDONS, 'add_addon', return_value=[]) as addons:
                        self.assertEqual(self.request('/api/app-settings', extra_headers=remote_headers)[0], 403)
                        for path in ('/api/local/open', '/api/settings', '/api/stremio/addons', '/api/app-settings', '/api/updates/check', '/api/updates/apply'):
                            self.assertEqual(self.request(path, {}, remote_headers)[0], 403, path)
                        dialog.assert_not_called()
                        configure.assert_not_called()
                        addons.assert_not_called()
            finally:
                self.httpd.lan_mode = prev_lan

    def test_stremio_imdb_and_desktop_loopback_guards(self):
        from stremio_imdb import StremioAddonManager, get_meta
        with self.assertRaises(ValueError):
            get_meta('movie', 'bad-id')
        with tempfile.TemporaryDirectory() as tmp:
            mgr = StremioAddonManager(Path(tmp) / 'addons.dat')
            with self.assertRaises(ValueError):
                mgr.add_addon('http://insecure.example/manifest.json')
        with patch.object(server, 'is_loopback_client', return_value=False):
            self.assertEqual(self.request('/api/shutdown', {})[0], 403)
            self.assertEqual(self.request('/api/remote/pin', {'pin': '9999'})[0], 403)

    def test_addon_stream_tokens_masking_and_subtitles(self):
        from stremio_imdb import StremioAddonManager, _parse_addon_stream, mask_manifest_url
        secret_url = 'https://addon.example.invalid/stremio/user-id-123/secret-payload-xyz/manifest.json'
        masked = mask_manifest_url(secret_url)
        self.assertNotIn('user-id-123', masked)
        self.assertNotIn('secret-payload-xyz', masked)
        self.assertTrue(masked.endswith('/manifest.json'))

        parsed = _parse_addon_stream('AIOStreams', {
            'name': '[TB⚡] StremThru 2160p',
            'description': '🎬 4K HDR\n🌎 Portuguese | English\n💾 12.50 GB\n📁 Paprika.2006.2160p.mkv',
            'url': 'https://cdn.example.invalid/stream/secret-hash/Paprika.2006.2160p.mkv',
            'behaviorHints': {'filename': 'Paprika.2006.2160p.mkv', 'videoSize': 13421772800, 'bingeGroup': 'tb-4k'},
        }, 'movie', 'tt0851578')
        self.assertTrue(parsed['playable'])
        self.assertEqual(parsed['resolution'], '4K')
        self.assertTrue(parsed['pt_audio'])
        self.assertTrue(parsed['cached'])
        self.assertTrue(parsed['stream_token'].startswith('addon:'))
        self.assertNotIn('url', parsed)
        self.assertEqual(server.resolve_target_url(parsed['stream_token']), 'https://cdn.example.invalid/stream/secret-hash/Paprika.2006.2160p.mkv')

        with tempfile.TemporaryDirectory() as tmp:
            mgr = StremioAddonManager(Path(tmp) / 'addons.dat')
            with patch('stremio_imdb._http_json', return_value={'id': 'org.aiostreams', 'name': 'AIOStreams', 'resources': ['stream', 'subtitles']}):
                addons = mgr.add_addon(secret_url)
            self.assertEqual(len(addons), 1)
            self.assertNotIn('url', addons[0])
            self.assertNotIn('secret-payload-xyz', addons[0]['masked_url'])
            with patch.object(server, 'ADDONS', mgr), \
                 patch.object(mgr, 'resolve_subtitles', return_value=[{'url': 'https://subdl.example/pt.srt', 'lang': 'pob', 'release': 'BluRay', 'addon': 'AIOStreams'}]), \
                 patch.object(server, 'http_json', return_value={'subtitles': [{'url': 'https://os.example/en.srt', 'lang': 'eng'}]}):
                subs = server.fetch_stremio_subtitles('tt0851578')
            self.assertEqual(len(subs), 2)
            self.assertEqual(subs[0]['lang'], 'pob')

    def test_library_downloader_paths_resume_cancel_and_indexing(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from catalog_service import CatalogService
        from library_downloader import LibraryDownloadService
        from stremio_imdb import register_addon_stream

        payload_bytes = b'MKV-HEADER-' + (b'A' * 65536)

        class MockCdnHandler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                return

            def do_GET(self):
                if 'slow' in self.path:
                    self.send_response(200)
                    self.send_header('Content-Length', str(len(payload_bytes) * 10))
                    self.end_headers()
                    for _ in range(20):
                        time.sleep(0.05)
                        try:
                            self.wfile.write(b'Z' * 4096)
                            self.wfile.flush()
                        except OSError:
                            break
                    return
                rng = self.headers.get('Range', '')
                start = int(rng.split('=')[1].split('-')[0]) if rng.startswith('bytes=') else 0
                slice_bytes = payload_bytes[start:]
                self.send_response(206 if start > 0 else 200)
                if start > 0:
                    self.send_header('Content-Range', f'bytes {start}-{len(payload_bytes) - 1}/{len(payload_bytes)}')
                self.send_header('Content-Length', str(len(slice_bytes)))
                self.end_headers()
                self.wfile.write(slice_bytes)

        cdn = ThreadingHTTPServer(('127.0.0.1', 0), MockCdnHandler)
        cdn_thread = threading.Thread(target=cdn.serve_forever, daemon=True)
        cdn_thread.start()
        try:
            cdn_port = cdn.server_address[1]
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp) / 'StreamServer'
                cat = CatalogService(root, Path(tmp) / 'data', enable_online_metadata=False)
                done_event = threading.Event()

                def on_done(job):
                    cat.scan(async_enrich=False)
                    cat.assign_imdb_id(job['target_path'], job.get('imdb_id', ''))
                    done_event.set()

                dl = LibraryDownloadService(root, on_complete=on_done)
                token_ep = register_addon_stream({
                    'url': f'http://127.0.0.1:{cdn_port}/media/Severance.S01E02.mkv',
                    'headers': {},
                    'ffmpeg_headers': '',
                    'filename': 'Severance.S01E02.mkv',
                    'video_size': len(payload_bytes),
                    'video_id': 'tt11280740:1:2',
                    'kind': 'series',
                    'imdb_id': 'tt11280740',
                    'season': 1,
                    'episode': 2,
                })
                job = dl.enqueue_from_token({
                    'stream_token': token_ep,
                    'kind': 'series',
                    'title': 'Severance: Season One',
                    'year': '2022',
                    'season': 1,
                    'episode': 2,
                    'imdb_id': 'tt11280740',
                })
                self.assertTrue(done_event.wait(5))
                expected_rel = 'Series/Severance Season One (2022)/Season 01/Severance.S01E02.mkv'
                self.assertEqual(job['rel_path'], expected_rel)
                target_file = root / expected_rel
                self.assertEqual(target_file.read_bytes(), payload_bytes)
                self.assertEqual(len(cat.find_local_by_imdb('tt11280740')), 1)

                with patch.object(server, 'CATALOG', cat), \
                     patch.object(cat, '_probe_video', return_value={'duration': 3120.0, 'width': 3840, 'height': 2160, 'video_codec': 'hevc', 'bitrate_bps': 18500000, 'fps': '23.98 fps', 'video_profile': 'HEVC · Main 10 · 10-bit', 'audio_summary': 'EN EAC3 6ch', 'subtitle_summary': 'POR, ENG'}):
                    st_d, d_body = self.request('/api/catalog/details?series_key=' + cat.get_home_catalog()['series'][0]['series_key'])
                    self.assertEqual(st_d, 200)
                    details = json.loads(d_body)
                    self.assertEqual(details['bitrate_label'], '18.5 Mbps')
                    self.assertIn('4K UHD', details['resolution_label'])
                    self.assertEqual(details['fps'], '23.98 fps')

                token_cancel = register_addon_stream({
                    'url': f'http://127.0.0.1:{cdn_port}/slow/Movie.mkv',
                    'headers': {},
                    'ffmpeg_headers': '',
                    'filename': 'Movie.2024.mkv',
                    'video_size': len(payload_bytes) * 10,
                    'video_id': 'tt9999999',
                    'kind': 'movie',
                    'imdb_id': 'tt9999999',
                })
                c_job = dl.enqueue_from_token({'stream_token': token_cancel, 'kind': 'movie', 'title': 'Cancel Movie', 'year': '2024'})
                time.sleep(0.12)
                dl.cancel(c_job['id'])
                for _ in range(30):
                    cur = next(j for j in dl.list_jobs() if j['id'] == c_job['id'])
                    if cur['state'] == 'cancelled':
                        break
                    time.sleep(0.05)
                self.assertEqual(cur['state'], 'cancelled')
        finally:
            cdn.shutdown()
            cdn.server_close()
            cdn_thread.join()

    def test_library_downloader_auto_resumes_after_timeout_and_eof(self):
        from library_downloader import LibraryDownloadService
        from stremio_imdb import register_addon_stream

        full_data = b'MKV-START-' + (b'X' * 524288) + b'-MKV-END'
        attempts = []

        class FlakyResponse:
            def __init__(self, start, chunk_limit=None, raise_timeout=False):
                self.pos = start
                self.limit = len(full_data) if chunk_limit is None else min(len(full_data), start + chunk_limit)
                self.raise_timeout = raise_timeout
                self.status = 206 if start > 0 else 200
                self.headers = (
                    {'Content-Range': f'bytes {start}-{len(full_data) - 1}/{len(full_data)}', 'Content-Length': str(len(full_data) - start)}
                    if start > 0 else {'Content-Length': str(len(full_data))}
                )
                self.fp = None

            def read(self, n):
                if self.pos >= self.limit:
                    if self.raise_timeout:
                        raise TimeoutError('The read operation timed out')
                    return b''
                end = min(self.limit, self.pos + n)
                out = full_data[self.pos:end]
                self.pos = end
                return out

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        def fake_open(req, timeout=15):
            rng = req.headers.get('Range', '')
            start = int(rng.split('=')[1].split('-')[0]) if rng.startswith('bytes=') else 0
            attempts.append(start)
            if len(attempts) == 1:
                return FlakyResponse(start, chunk_limit=131072, raise_timeout=True)
            if len(attempts) == 2:
                return FlakyResponse(start, chunk_limit=131072, raise_timeout=False)
            return FlakyResponse(start)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'StreamServer'
            done = threading.Event()
            dl = LibraryDownloadService(root, on_complete=lambda j: done.set())
            tok = register_addon_stream({
                'url': 'https://cdn.example.invalid/Godzilla.mkv',
                'headers': {},
                'ffmpeg_headers': '',
                'filename': 'Godzilla.mkv',
                'video_size': len(full_data),
                'video_id': 'tt23289160',
                'kind': 'movie',
                'imdb_id': 'tt23289160',
            })
            with patch('library_downloader._open_media_stream', side_effect=fake_open):
                job = dl.enqueue_from_token({'stream_token': tok, 'kind': 'movie', 'title': 'Godzilla Minus One', 'year': '2023'})
                self.assertTrue(done.wait(10))
            self.assertEqual(attempts, [0, 131072, 262144])
            saved = root / job['rel_path']
            self.assertEqual(saved.read_bytes(), full_data)

if __name__ == '__main__':
    unittest.main()
