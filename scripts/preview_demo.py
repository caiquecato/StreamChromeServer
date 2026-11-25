"""Local UI preview with fictional data; never reads an external library."""
from pathlib import Path
import json
import sys
from urllib.parse import parse_qs, urlparse

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / 'src'))
import server_pro as app
from catalog_service import _format_bitrate, _format_size_bytes, _resolution_label

ASSETS = PROJECT / 'docs' / 'demo-assets'
ITEMS = [
    {'id': 'orbit', 'kind': 'movie', 'title': 'Orbit', 'year': '2026', 'overview': 'A small crew follows a signal beyond the edge of the familiar.', 'image': 'orbit.jpg'},
    {'id': 'ridge', 'kind': 'movie', 'title': 'The Ridge', 'year': '2025', 'overview': 'Two climbers return to the mountain that changed their lives.', 'image': 'the_ridge.jpg'},
    {'id': 'tides', 'kind': 'movie', 'title': 'Tides', 'year': '2026', 'overview': 'A coastal town records the passing seasons through the stories of its residents.', 'image': 'tides.jpg'},
    {'id': 'city', 'kind': 'series', 'title': 'After Hours', 'year': '2026', 'overview': 'A night shift reveals the lives that keep a city moving.', 'image': 'nightfall.jpg', 'series_key': 'demo-series', 'season_count': 1, 'episode_count': 3, 'first_episode_id': 'city'},
]
for item in ITEMS:
    item.update({'poster_url': '/demo-assets/' + item['image'], 'backdrop_url': '/demo-assets/' + item['image'],
                 'poster': '/demo-assets/' + item['image'], 'duration': 5400, 'position': 0, 'progress': 0,
                 'height': 1080, 'video_codec': 'h264', 'rating': '', 'imdb_id': 'tt0386676'})

META = {
    'kind': 'series', 'imdb_id': 'tt0386676', 'title': 'After Hours', 'year': '2026',
    'poster': '/demo-assets/nightfall.jpg', 'description': ITEMS[3]['overview'], 'genres': ['Drama'],
    'runtime': '45 min', 'cast': [], 'local_matches': [], 'stremio_url': 'stremio://detail/series/tt0386676',
    'imdb_url': 'https://www.imdb.com/title/tt0386676/',
    'seasons': [{'season': 1, 'episodes': [
        {'id': f'tt0386676:1:{i}', 'season': 1, 'episode': i, 'title': title, 'released': '2026-01-01'}
        for i, title in enumerate(('First Light', 'The Crossing', 'Homeward'), 1)
    ]}],
}
JOBS = []
TOKENS = {}
COUNTER = 0


class DemoHandler(app.StreamProHandler):
    def do_GET(self):
        global COUNTER
        path = urlparse(self.path).path
        query = parse_qs(urlparse(self.path).query)
        if not self.allowed():
            return
        if path.startswith('/demo-assets/'):
            name = path.removeprefix('/demo-assets/')
            if name not in {item['image'] for item in ITEMS}:
                self.send_json({'error': 'Not found'}, 404)
            elif (ASSETS / name).is_file():
                self.send_bytes((ASSETS / name).read_bytes(), 'image/jpeg')
            else:
                self.send_json({'error': 'Demo image not available'}, 404)
        elif path == '/api/app-settings':
            self.send_json({'version': app.APP_VERSION, 'integration': {'available': False, 'autorun': False, 'drive_icon': False, 'reason': 'Integration requires the compiled Windows executable.'},
                            'paths': {'movies': 'C:\\StreamChromeServer\\Movies', 'series': 'C:\\StreamChromeServer\\Series', 'data': 'C:\\StreamChromeServer\\app\\data'}})
        elif path == '/api/catalog':
            self.send_json({'movies': ITEMS[:3], 'series': ITEMS[3:], 'hero_items': ITEMS,
                            'featured': ITEMS[0], 'continue_watching': [i for i in ITEMS if i['position'] >= 3],
                            'media_root': str(PROJECT / 'scratch/demo_library'), 'library_paths': {}, 'stats': {'movies': 3, 'series': 1}})
        elif path == '/api/discover':
            self.send_json({'items': [dict(i, kind='movie') for i in ITEMS[:3]], 'genres': ['Drama', 'Comedy', 'Sci-Fi']})
        elif path == '/api/imdb/meta':
            self.send_json(META)
        elif path == '/api/catalog/series':
            self.send_json(dict(META, series_key='demo-series', poster_url=META['poster']))
        elif path == '/api/catalog/details':
            item = next((i for i in ITEMS if i['id'] == query.get('id', [''])[0]), ITEMS[0])
            video = PROJECT / 'scratch/demo_media' / (item['id'] + '.mp4')
            probe = app.CATALOG._probe_video(video) if video.is_file() else {}
            size = video.stat().st_size if video.is_file() else 0
            self.send_json(dict(item, **probe, imdb_id='', imdb_rating='', genres='Demo library',
                                resolution_label=_resolution_label(probe.get('width'), probe.get('height')),
                                bitrate_label=_format_bitrate(probe.get('bitrate_bps')), size_label=_format_size_bytes(size)))
        elif path == '/api/stremio/streams':
            video_id = query.get('id', [''])[0]
            COUNTER += 1
            streams = []
            for resolution in ('1080p', '4K'):
                token = f'addon:demo-{COUNTER}-{resolution}'
                TOKENS[token] = video_id
                streams.append({'stream_token': token, 'source_key': video_id + resolution, 'name': 'Demo source',
                                'summary': resolution + ' · demonstration source', 'resolution': resolution,
                                'size_label': '1.2 GB', 'filename': f'After.Hours.{resolution}.mkv', 'playable': True,
                                'pt_audio': resolution == '1080p', 'addon': 'Demo'})
            self.send_json({'streams': streams})
        elif path == '/api/library/downloads':
            self.send_json({'jobs': JOBS})
        else:
            super().do_GET()

    def do_POST(self):
        path = urlparse(self.path).path
        if path not in ('/api/library/download', '/api/catalog/progress', '/api/catalog/open'):
            return super().do_POST()
        if not self.allowed(mutation=True):
            return
        length = int(self.headers.get('Content-Length', '0'))
        if not 0 < length <= 65536:
            return self.send_json({'error': 'Invalid body'}, 400)
        data = json.loads(self.rfile.read(length))
        if path == '/api/library/download':
            token = data.get('stream_token')
            if token not in TOKENS:
                return self.send_json({'error': 'Expired demo token'}, 400)
            job = dict(data, id=f'demo-{len(JOBS)}', state='queued', progress=0, downloaded_bytes=0, total_bytes=0,
                       rel_path=f"Series/After Hours (2026)/Season 01/After.Hours.S01E{int(data['episode']):02}.mkv")
            JOBS.append(job)
            self.send_json(job)
        elif path == '/api/catalog/progress':
            item = next((i for i in ITEMS if i['id'] == data.get('id')), None)
            if item:
                item['position'] = data['position']
                item['progress'] = data['position'] / data['duration'] if data.get('duration') else 0
            self.send_json({'ok': True})
        else:
            item = next((i for i in ITEMS if i['id'] == data.get('id')), ITEMS[0])
            video = PROJECT / 'scratch/demo_media' / (item['id'] + '.mp4')
            if not video.is_file():
                return self.send_json({'error': 'Demo playback clip not generated'}, 404)
            token = app.register_local_video(video)
            self.send_json({'source': 'local:' + token, 'filename': item['title'], 'item_id': item['id'], 'local_subtitles': []})


if __name__ == '__main__':
    server = app.HTTPServer(('127.0.0.1', 8781), DemoHandler)
    print('Demo preview: http://127.0.0.1:8781 (fictional data, no real downloads)', flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
