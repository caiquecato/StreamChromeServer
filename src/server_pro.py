import argparse
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
import ipaddress
import json
import math
import mimetypes
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
from urllib.parse import urlparse, parse_qs, unquote
import webbrowser
import urllib.request

from process_guard import attach_to_server, run_managed
from downloads import DownloadService
from subtitles import SubtitleService, ProviderError, load_settings, http_json, http_bytes, to_vtt, guess_search
from catalog_service import CatalogService
from jellyfin_bridge import JellyfinBridge
from remote_access import RemoteAccessService, is_autostart_enabled, is_loopback_client, is_private_or_loopback, render_pin_gate_html, set_autostart
from stremio_imdb import StremioAddonManager, discover as imdb_discover, get_addon_stream, get_genres as imdb_genres, get_meta as imdb_get_meta
from library_downloader import LibraryDownloadService

class HTTPServer(ThreadingHTTPServer):
    allow_reuse_address = False
    lan_mode = False


HOST, PORT = '127.0.0.1', 8780
APP_DIRECTORY = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent.parent


def _resolve_layout(exe_dir):
    if (exe_dir / 'app').is_dir():
        engine = exe_dir / 'app'
        return exe_dir, engine, engine / 'data'
    if exe_dir.parent.name == 'app':
        engine = exe_dir.parent
        return engine.parent, engine, engine / 'data'
    if (exe_dir / 'src' / 'server_pro.py').is_file():
        return exe_dir, exe_dir, exe_dir / 'data'
    # Standard Windows install: binaries in Program folder, library + data in user profile.
    data = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'StreamChromeServer'
    root_file = exe_dir / 'library_root.txt'
    root = Path.home() / 'Videos' / 'StreamChromeServer'
    if root_file.is_file():
        try:
            text = root_file.read_text(encoding='utf-8').strip()
            if text:
                root = Path(text)
        except OSError:
            pass
    return root, exe_dir, data


SERVER_ROOT, ENGINE_DIR, DATA_DIR = _resolve_layout(APP_DIRECTORY)
for _d in (DATA_DIR, SERVER_ROOT / 'Filmes', SERVER_ROOT / 'Series'):
    try:
        _d.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
SETTINGS_DAT = DATA_DIR / 'server_settings.dat'
SUBTITLE_SETTINGS_DAT = DATA_DIR / 'subtitle_settings.dat'

def find_media_tool(name):
    for candidate in (
        ENGINE_DIR / 'ffmpeg' / (name + '.exe'),
        APP_DIRECTORY / 'ffmpeg' / (name + '.exe'),
        *sorted((ENGINE_DIR / 'ffmpeg').glob('ffmpeg-*/bin/' + name + '.exe')),
        Path(r'C:\Program Files\FFmpeg\bin') / (name + '.exe'),
    ):
        if candidate.is_file():
            return str(candidate)
    return shutil.which(name) or name

FFMPEG_BIN = find_media_tool('ffmpeg')
FFPROBE_BIN = find_media_tool('ffprobe')
_YTDLP_EXE = ENGINE_DIR / 'yt-dlp' / 'yt-dlp.exe'
if not _YTDLP_EXE.is_file():
    _YTDLP_EXE = APP_DIRECTORY / 'yt-dlp' / 'yt-dlp.exe' if getattr(sys, 'frozen', False) else APP_DIRECTORY / 'installer' / 'yt-dlp' / 'yt-dlp.exe'
_DEFAULT_DEST = (SERVER_ROOT / 'Filmes') if (SERVER_ROOT / 'Filmes').is_dir() else None
DOWNLOADS = DownloadService(_YTDLP_EXE, FFMPEG_BIN, destination=_DEFAULT_DEST)
CATALOG = CatalogService(SERVER_ROOT, DATA_DIR, FFMPEG_BIN, FFPROBE_BIN)
REMOTE = RemoteAccessService(SETTINGS_DAT, ENGINE_DIR / 'cloudflared' / 'cloudflared.exe', hub_port=PORT, jellyfin_port=8096)
ADDONS = StremioAddonManager(SETTINGS_DAT)
JELLYFIN = JellyfinBridge(ENGINE_DIR / 'jellyfin', FFMPEG_BIN, DATA_DIR / 'jellyfin_link.json', SETTINGS_DAT, port=8096,
                          runtime_dir=None if DATA_DIR == ENGINE_DIR / 'data' else DATA_DIR / 'jellyfin')
NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
VIDEO_EXTENSIONS = {'.mp4', '.mkv', '.avi', '.mov', '.m4v', '.webm', '.ts', '.m2ts', '.mpg', '.mpeg', '.wmv'}
LOCAL_FILES = OrderedDict()
LOCAL_IMDB = OrderedDict()
PROBE_CACHE = OrderedDict()
STREAM_HEADERS = OrderedDict()
LOCAL_LOCK = threading.Lock()
DIALOG_LOCK = threading.Lock()
_NVENC_CACHE = None
_SCANNED_DONE_JOBS = set()
LANGUAGE_NAMES = {
    'por': 'Portuguese', 'pt': 'Portuguese', 'pob': 'Portuguese (Brazil)', 'pt-br': 'Portuguese (Brazil)',
    'eng': 'English', 'en': 'English', 'jpn': 'Japanese', 'ja': 'Japanese', 'spa': 'Spanish', 'es': 'Spanish',
    'fre': 'French', 'fr': 'French', 'ger': 'German', 'de': 'German', 'ita': 'Italian', 'it': 'Italian',
}


def _on_library_download_complete(job):
    try:
        CATALOG.scan(async_enrich=False)
        if job.get('imdb_id') and job.get('target_path'):
            CATALOG.assign_imdb_id(job['target_path'], job['imdb_id'])
        CATALOG.trigger_enrichment()
        JELLYFIN.refresh_library()
    except Exception:
        pass


LIBRARY_DOWNLOADS = LibraryDownloadService(SERVER_ROOT, on_complete=_on_library_download_complete)
try:
    SUBTITLES = SubtitleService(load_settings(SUBTITLE_SETTINGS_DAT), settings_path=SUBTITLE_SETTINGS_DAT)
except (ValueError, OSError):
    print('Could not read the protected settings. Configure the keys again in the interface.')
    SUBTITLES = SubtitleService({}, settings_path=SUBTITLE_SETTINGS_DAT)


def find_application(name, folders):
    for folder in folders:
        path = os.path.expandvars(folder)
        if os.path.isfile(path):
            return path
    return shutil.which(name)


def find_chrome():
    return find_application('chrome', [r'%ProgramFiles%\Google\Chrome\Application\chrome.exe', r'%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe', r'%LocalAppData%\Google\Chrome\Application\chrome.exe'])


def find_vlc():
    return find_application('vlc', [r'%ProgramFiles%\VideoLAN\VLC\vlc.exe', r'%ProgramFiles(x86)%\VideoLAN\VLC\vlc.exe'])


def normalize_url(raw):
    value = raw.strip()
    return 'https://' + value[len('stremio://'):] if value.startswith('stremio://') else value


def resolve_target_url(raw):
    value = normalize_url(raw)
    if value.startswith('local:'):
        with LOCAL_LOCK:
            path = LOCAL_FILES.get(value[6:])
        if not path or not os.path.isfile(path):
            raise ValueError('Local file unavailable. Use Open video from PC again.')
        return path
    if value.startswith('remote:'):
        source, options = DOWNLOADS.playback(value[7:])
        metadata = probe_media(source, options.get('headers', ''))
        metadata = {**metadata, **options}
        if options.get('audio_url'):
            metadata['audio_tracks'] = [{'index': 0, 'label': 'Default track'}]
        with LOCAL_LOCK:
            PROBE_CACHE[source] = metadata
        return source
    if value.startswith('addon:'):
        info = get_addon_stream(value)
        source = info['url']
        ff_headers = info.get('ffmpeg_headers', '')
        if ff_headers:
            with LOCAL_LOCK:
                STREAM_HEADERS[source] = ff_headers
                while len(STREAM_HEADERS) > 256:
                    STREAM_HEADERS.popitem(last=False)
        return source
    parsed = urlparse(value)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('Paste an HTTP/HTTPS link or use Open video from PC.')
    return value


def choose_local_video():
    # Tk owns its widgets on this request thread; no global Tk instance is shared.
    import tkinter
    from tkinter import filedialog
    with DIALOG_LOCK:
        root = tkinter.Tk()
        root.withdraw()
        icon_file = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent.parent)) / 'assets' / 'app.ico'
        if icon_file.exists():
            root.iconbitmap(str(icon_file))
        root.attributes('-topmost', True)
        try:
            return filedialog.askopenfilename(parent=root, title='Open video in StreamChrome Server', filetypes=[('Videos', ' '.join('*' + extension for extension in sorted(VIDEO_EXTENSIONS))), ('All files', '*.*')])
        finally:
            root.destroy()


def probe_media(source, headers=''):
    with LOCAL_LOCK:
        if source in PROBE_CACHE:
            return PROBE_CACHE[source]
        effective_headers = headers or STREAM_HEADERS.get(source, '')
    try:
        result = run_managed([FFPROBE_BIN, '-v', 'error', '-show_entries', 'format=duration:stream=index,codec_name,codec_type,width,height:stream_tags=language,title', '-of', 'json'] + (['-headers', effective_headers] if effective_headers else []) + [source], stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=25, creationflags=NO_WINDOW)
    except FileNotFoundError:
        raise ValueError('FFprobe not found. Install FFmpeg to play videos.') from None
    except subprocess.TimeoutExpired:
        raise ValueError('The video took too long to respond. Check the file or connection and try again.') from None
    if result.returncode:
        raise ValueError('Could not read the video. Check that the file or link is valid.')
    data = json.loads(result.stdout)
    tracks = []
    video_codec = None
    width = height = 0
    for stream in data.get('streams', []):
        if stream.get('codec_type') == 'video' and video_codec is None:
            video_codec = stream.get('codec_name')
            width, height = stream.get('width', 0), stream.get('height', 0)
        if stream.get('codec_type') == 'audio':
            tags = stream.get('tags', {})
            lang = tags.get('language', 'und')
            title = tags.get('title', '')
            tracks.append({'index': stream['index'], 'label': f"{LANGUAGE_NAMES.get(lang, lang.upper())} ({stream.get('codec_name', '').upper()}) {title}".strip()})
    try:
        duration = float(data.get('format', {}).get('duration', 0))
        if not math.isfinite(duration):
            duration = 0
    except (ValueError, TypeError):
        duration = 0
    metadata = {'duration': duration, 'audio_tracks': tracks, 'video_codec': video_codec, 'width': width, 'height': height}
    if effective_headers:
        metadata['headers'] = effective_headers
    if not video_codec:
        raise ValueError('The file does not contain a video track.')
    with LOCAL_LOCK:
        PROBE_CACHE[source] = metadata
        while len(PROBE_CACHE) > 64:
            PROBE_CACHE.popitem(last=False)
    return metadata


def fetch_stremio_subtitles(raw):
    match = re.search(r'tt\d{7,10}(?::\d+:\d+)?', unquote(str(raw or '')))
    if not match:
        return []
    video_id = match.group()
    kind = 'series' if ':' in video_id else 'movie'

    def _query_opensubs():
        try:
            data = http_json(f'https://opensubtitles-v3.strem.io/subtitles/{kind}/{video_id}.json')
            out = []
            for item in data.get('subtitles', []):
                url = str(item.get('url') or '').strip()
                if url.startswith('https://'):
                    lang = str(item.get('lang') or '').strip().lower()
                    out.append({'url': url, 'lang': lang, 'release': '', 'addon': 'OpenSubtitles'})
            return out
        except ProviderError:
            return []

    def _query_addons():
        return ADDONS.resolve_subtitles(kind, video_id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        f_addon = pool.submit(_query_addons)
        f_os = pool.submit(_query_opensubs)
        raw_items = [*f_addon.result(), *f_os.result()]

    merged = []
    seen_urls = set()
    for item in raw_items:
        url = item.get('url', '')
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        lang = item.get('lang', '')
        lang_label = LANGUAGE_NAMES.get(lang, lang.upper() or 'Subtitle')
        release = item.get('release') or item.get('addon') or ''
        label = f'{lang_label} · {release}' if release else lang_label
        merged.append({'url': url, 'lang': lang, 'label': label[:120]})

    priority = {'pob': 0, 'pt-br': 0, 'por': 1, 'pt': 1, 'eng': 2, 'en': 2, 'spa': 3, 'es': 3}
    merged.sort(key=lambda s: priority.get(s['lang'], 9))
    return merged[:50]


def convert_srt_to_vtt(content):
    return to_vtt(content)


def detect_nvenc():
    global _NVENC_CACHE
    if _NVENC_CACHE is not None:
        return _NVENC_CACHE
    try:
        res = run_managed([FFMPEG_BIN, '-hide_banner', '-encoders'], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=6, creationflags=NO_WINDOW)
        _NVENC_CACHE = 'h264_nvenc' in res.stdout
    except (OSError, subprocess.TimeoutExpired):
        _NVENC_CACHE = False
    return _NVENC_CACHE


def build_stream_command(source, metadata, seek, audio='', compatibility=False, max_height=0, use_nvenc=False):
    cmd = [FFMPEG_BIN, '-nostdin', '-hide_banner', '-loglevel', 'error', '-ss', str(seek)]
    if metadata.get('headers'): cmd += ['-headers', metadata['headers']]
    cmd += ['-i', source]
    if metadata.get('audio_url'):
        cmd += ['-ss', str(seek)]
        if metadata.get('audio_headers'): cmd += ['-headers', metadata['audio_headers']]
        cmd += ['-i', metadata['audio_url']]
    cmd += ['-map', '0:v:0']
    if audio:
        if not audio.isdigit() or int(audio) not in [item['index'] for item in metadata['audio_tracks']]:
            raise ValueError('Invalid audio track.')
        cmd += ['-map', '1:a:0' if metadata.get('audio_url') else '0:' + audio]
    else:
        cmd += ['-map', '1:a:0' if metadata.get('audio_url') else '0:a:0?']
    force_transcode = compatibility or int(max_height or 0) > 0
    if metadata['video_codec'] in ('h264', 'hevc', 'av1') and not force_transcode:
        # Preserve the same pre-roll for audio and copied video after input seeking.
        for index in reversed([i for i, value in enumerate(cmd) if value == '-ss']):
            cmd.insert(index, '-noaccurate_seek')
        cmd += ['-c:v', 'copy']
        if metadata['video_codec'] == 'hevc':
            cmd += ['-tag:v', 'hvc1']
    else:
        if int(max_height or 0) > 0:
            cmd += ['-vf', f"scale=-2:'min(ih,{int(max_height)})'"]
        if use_nvenc:
            cmd += ['-c:v', 'h264_nvenc', '-preset', 'p4', '-cq', '23', '-pix_fmt', 'yuv420p']
        else:
            cmd += ['-c:v', 'libx264', '-preset', 'veryfast', '-crf', '21', '-pix_fmt', 'yuv420p']
    cmd += ['-c:a', 'aac', '-b:a', '192k', '-ac', '2', '-sn', '-dn', '-map_chapters', '-1', '-avoid_negative_ts', 'make_zero', '-f', 'mp4', '-movflags', 'frag_keyframe+empty_moov+default_base_moof', 'pipe:1']
    return cmd


def seek_start(source, requested):
    if requested <= 0:
        return 0.0
    result = run_managed([FFPROBE_BIN, '-v', 'error', '-select_streams', 'v:0', '-read_intervals', f'{requested}%+#1', '-show_packets', '-show_entries', 'packet=pts_time', '-of', 'json'] + (['-headers', PROBE_CACHE.get(source, {}).get('headers')] if PROBE_CACHE.get(source, {}).get('headers') else []) + [source], stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding='utf-8', timeout=15, creationflags=NO_WINDOW)
    if result.returncode:
        raise ValueError('Could not locate the video position. Try again.')
    packets = json.loads(result.stdout).get('packets', [])
    if not packets or 'pts_time' not in packets[0]:
        raise ValueError('The file did not report the frame timestamp. Try another video position.')
    start = float(packets[0]['pts_time'])
    if not math.isfinite(start):
        raise ValueError('The file reported an invalid video timestamp.')
    return max(0.0, start)


def split_host_port(value):
    text = (value or '').strip()
    if not text:
        return '', None
    if text.startswith('['):
        end = text.find(']')
        if end == -1:
            return '', None
        host = text[1:end]
        rest = text[end + 1:]
        if not rest:
            return host, None
        return (host, int(rest[1:])) if rest.startswith(':') and rest[1:].isdigit() else ('', None)
    if ':' in text:
        host, port_text = text.rsplit(':', 1)
        if not port_text.isdigit():
            return '', None
        return host, int(port_text)
    return text, None


def allowed_lan_host(hostname, server_port):
    host = (hostname or '').strip().lower().strip('[]')
    if not host:
        return False
    if host in ('localhost', socket.gethostname().lower()) or host in REMOTE.tunnel_hosts():
        return True
    if is_private_or_loopback(host):
        return True
    return False


def get_lan_ipv4():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(('1.1.1.1', 80))
            ip = sock.getsockname()[0]
            if ip and not ip.startswith('127.'):
                return ip
    except OSError:
        pass
    return '127.0.0.1'


def save_library_subtitle(source, subtitle_id, language='pt-BR'):
    video_path = Path(resolve_target_url(str(source or ''))).resolve()
    root = CATALOG.server_root
    if not video_path.is_relative_to(root):
        raise ValueError('Save subtitles only for videos inside your library.')
    vtt_content = SUBTITLES.download(str(subtitle_id or ''))
    safe_lang = re.sub(r'[^a-zA-Z0-9_-]+', '', str(language or 'pt-BR')) or 'pt-BR'
    target = video_path.with_name(f'{video_path.stem}.{safe_lang}.vtt').resolve()
    if not target.is_relative_to(root):
        raise ValueError('Invalid subtitle destination.')
    target.write_text(vtt_content, encoding='utf-8')
    JELLYFIN.refresh_library()
    subs = CATALOG.list_sidecars(video_path)
    match = next((s for s in subs if Path(s['path']).resolve() == target), None)
    return {'saved': target.name, 'id': match['id'] if match else '', 'label': target.name}


def register_local_video(path_obj, imdb_video_id=''):
    resolved = Path(path_obj).resolve()
    token = secrets.token_urlsafe(24)
    with LOCAL_LOCK:
        LOCAL_FILES[token] = str(resolved)
        while len(LOCAL_FILES) > 64:
            LOCAL_FILES.popitem(last=False)
        if imdb_video_id:
            LOCAL_IMDB[token] = imdb_video_id
            while len(LOCAL_IMDB) > 64:
                LOCAL_IMDB.popitem(last=False)
    return token


def open_media_folder(folder_key):
    target = SERVER_ROOT / ('Series' if folder_key == 'series' else 'Filmes')
    target.mkdir(parents=True, exist_ok=True)
    if os.name == 'nt':
        os.startfile(str(target))
    return {'folder': str(target)}


def trigger_firewall_rule():
    if os.name != 'nt':
        return {'status': 'unsupported'}
    ps_script = (
        "Remove-NetFirewallRule -DisplayName 'StreamChromeServer*' -ErrorAction SilentlyContinue;"
        "New-NetFirewallRule -DisplayName 'StreamChromeServer Hub (TV e Celular)' -Direction Inbound -Protocol TCP -LocalPort 8780 -Action Allow -Profile Private | Out-Null;"
        "New-NetFirewallRule -DisplayName 'StreamChromeServer Jellyfin (TV e Celular)' -Direction Inbound -Protocol TCP -LocalPort 8096 -Action Allow -Profile Private | Out-Null;"
    )
    subprocess.Popen(
        ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', f'Start-Process powershell -Verb RunAs -WindowStyle Hidden -ArgumentList \'-NoProfile\',\'-Command\',\"{ps_script}\"'],
        stdin=subprocess.DEVNULL, creationflags=NO_WINDOW,
    )
    return {'status': 'requested'}


class StreamProHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        # Query strings can contain private video links; keep them out of logs.
        return

    def allowed(self, mutation=False):
        port = self.server.server_address[1]
        origin = self.headers.get('Origin')
        host_header = self.headers.get('Host')
        if getattr(self.server, 'lan_mode', False):
            host_name, host_port = split_host_port(host_header)
            is_tunnel = host_name in REMOTE.tunnel_hosts()
            host_ok = bool(host_name) and (host_port == port or (is_tunnel and host_port in (None, 443, 80))) and allowed_lan_host(host_name, port)
            origin_ok = True
            if origin:
                parsed_origin = urlparse(origin)
                o_host = (parsed_origin.hostname or '').lower()
                o_tunnel = o_host in REMOTE.tunnel_hosts()
                o_port = parsed_origin.port or (443 if parsed_origin.scheme == 'https' else 80)
                origin_ok = parsed_origin.scheme in ('http', 'https') and (o_port == port or o_tunnel) and allowed_lan_host(o_host, port)
            if not host_ok or not origin_ok or (self.headers.get('Sec-Fetch-Site') == 'cross-site' and urlparse(self.path).path not in ('/', '/index.html')):
                self.send_json({'error': 'Access is allowed only through the local application interface.'}, 403)
                return False
        else:
            origins = {f'http://127.0.0.1:{port}', f'http://localhost:{port}'}
            hosts = {f'127.0.0.1:{port}', f'localhost:{port}'}
            if host_header not in hosts or (origin and origin not in origins) or (self.headers.get('Sec-Fetch-Site') == 'cross-site' and urlparse(self.path).path not in ('/', '/index.html')):
                self.send_json({'error': 'Access is allowed only through the local application interface.'}, 403)
                return False
        if mutation and self.headers.get('X-StreamChrome') != '1':
            self.send_json({'error': 'Invalid local request.'}, 403)
            return False
        return True

    def is_host_client(self):
        return is_loopback_client(self.client_address[0]) and not REMOTE.is_external_request(self.headers, self.client_address[0])

    def check_remote_pin(self, parsed_path):
        if parsed_path == '/api/remote/auth':
            return True
        if not REMOTE.is_external_request(self.headers, self.client_address[0]):
            return True
        if REMOTE.has_valid_session(self.headers.get('Cookie', '')):
            return True
        if parsed_path in ('/', '/index.html'):
            self.send_bytes(render_pin_gate_html().encode('utf-8'), 'text/html; charset=utf-8')
        else:
            self.send_json({'error': 'PIN required.'}, 401)
        return False

    def do_GET(self):
        if not self.allowed():
            return
        parsed = urlparse(self.path)
        if not self.check_remote_pin(parsed.path):
            return
        query = parse_qs(parsed.query)
        try:
            if parsed.path in ('/', '/index.html'):
                self.serve_asset()
            elif parsed.path in ('/player_helpers.js', '/player.css', '/layouts.css', '/appearance.js', '/i18n.js', '/presentation.js', '/downloads.js', '/hub.css', '/hub.js', '/series.css', '/season_queue.js', '/qrcode.js', '/manifest.webmanifest'):
                self.serve_asset(parsed.path[1:])
            elif parsed.path.startswith('/api/poster/'):
                poster_file = unquote(parsed.path[len('/api/poster/'):])
                target = CATALOG.poster_path(poster_file)
                self.send_bytes(target.read_bytes(), 'image/jpeg', cache='public, max-age=86400')
            elif parsed.path.startswith('/media/'):
                item_id = parsed.path[len('/media/'):]
                self.handle_range_media(item_id)
            elif parsed.path == '/tv.m3u':
                self.handle_m3u_playlist()
            elif parsed.path == '/api/catalog':
                self.send_json(CATALOG.get_home_catalog())
            elif parsed.path == '/api/catalog/series':
                self.send_json(CATALOG.get_series_detail(query.get('key', [''])[0]))
            elif parsed.path == '/api/catalog/details':
                self.send_json(CATALOG.get_media_details(query.get('id', [''])[0], query.get('series_key', [''])[0]))
            elif parsed.path == '/api/catalog/subtitle':
                sub_path = CATALOG.resolve_sidecar_path(query.get('id', [''])[0])
                self.send_bytes(to_vtt(sub_path.read_bytes()).encode('utf-8'), 'text/vtt; charset=utf-8')
            elif parsed.path == '/api/discover':
                d_kind = query.get('kind', ['movie'])[0]
                self.send_json({
                    'items': imdb_discover(d_kind, query.get('catalog', ['top'])[0], query.get('q', [''])[0], query.get('genre', [''])[0]),
                    'genres': imdb_genres(d_kind),
                })
            elif parsed.path == '/api/imdb/meta':
                kind = query.get('kind', ['movie'])[0]
                imdb_id = query.get('id', [''])[0]
                meta = imdb_get_meta(kind, imdb_id)
                local_matches = CATALOG.find_local_by_imdb(imdb_id)
                stream_id = query.get('video_id', [''])[0] or (f'{imdb_id}:1:1' if kind == 'series' else imdb_id)
                meta['local_matches'] = local_matches
                include_streams = query.get('streams', ['1'])[0] != '0'
                meta['streams'] = ADDONS.resolve_streams(kind, stream_id) if (include_streams and ADDONS.list_addons()) else []
                self.send_json(meta)
            elif parsed.path == '/api/stremio/streams':
                kind = query.get('kind', ['movie'])[0]
                video_id = query.get('id', [''])[0]
                self.send_json({'streams': ADDONS.resolve_streams(kind, video_id)})
            elif parsed.path == '/api/stremio/addons':
                self.send_json({'addons': ADDONS.public_addons()})
            elif parsed.path == '/api/library/downloads':
                self.send_json({'jobs': LIBRARY_DOWNLOADS.list_jobs(CATALOG.find_item_id_by_rel_path)})
            elif parsed.path == '/api/network/info':
                lan_ip = get_lan_ipv4()
                jf = JELLYFIN.status()
                if self.is_host_client():
                    jf['jellyfin_password'] = JELLYFIN.current_password()
                rem = REMOTE.status()
                port = self.server.server_address[1]
                self.send_json({
                    'lan_ip': lan_ip,
                    'port': port,
                    'hub_lan_url': f'http://{lan_ip}:{port}',
                    'm3u_lan_url': f'http://{lan_ip}:{port}/tv.m3u',
                    'jellyfin_lan_url': f'http://{lan_ip}:{jf["port"]}',
                    'jellyfin_local_url': f'http://127.0.0.1:{jf["port"]}',
                    'jellyfin': jf,
                    'remote': rem,
                    'autostart': is_autostart_enabled(),
                    'nvenc': detect_nvenc(),
                })
            elif parsed.path == '/api/download/status':
                status = DOWNLOADS.status(query.get('id', [''])[0])
                if status.get('state') == 'complete' and status.get('id') not in _SCANNED_DONE_JOBS:
                    _SCANNED_DONE_JOBS.add(status['id'])
                    threading.Thread(target=lambda: (CATALOG.scan(), JELLYFIN.refresh_library()), daemon=True).start()
                self.send_json(status)
            elif parsed.path == '/api/settings':
                self.send_json(SUBTITLES.status())
            elif parsed.path == '/api/status':
                self.send_json({'version': '1.0.0', 'ffmpeg': bool(shutil.which(FFMPEG_BIN) or Path(FFMPEG_BIN).is_file()), 'ffprobe': bool(shutil.which(FFPROBE_BIN) or Path(FFPROBE_BIN).is_file()), 'lan': bool(getattr(self.server, 'lan_mode', False))})
            elif parsed.path == '/api/inspect':
                raw = query.get('url', [''])[0]
                addon_info = get_addon_stream(raw) if raw.startswith('addon:') else None
                source = resolve_target_url(raw)
                metadata = probe_media(source)
                stremio_subs = []
                if raw.startswith('remote:'):
                    filename = metadata.get('title', 'Video')
                elif raw.startswith('local:'):
                    filename = Path(source).name
                    with LOCAL_LOCK:
                        local_imdb = LOCAL_IMDB.get(raw[6:], '')
                    if local_imdb:
                        stremio_subs = fetch_stremio_subtitles(local_imdb)
                elif addon_info:
                    filename = addon_info.get('filename') or 'Video'
                    stremio_subs = fetch_stremio_subtitles(addon_info.get('video_id', ''))
                else:
                    filename = parse_qs(urlparse(raw).query).get('torrent_name', [unquote(Path(urlparse(raw).path).name) or 'Video por link'])[0]
                    stremio_subs = fetch_stremio_subtitles(raw)
                suggestion = guess_search(filename)
                if addon_info:
                    if addon_info.get('imdb_id'):
                        suggestion['imdb_id'] = addon_info['imdb_id']
                    if addon_info.get('kind') == 'series':
                        suggestion['type'] = 'series'
                        if addon_info.get('season'):
                            suggestion['season'] = str(addon_info['season'])
                        if addon_info.get('episode'):
                            suggestion['episode'] = str(addon_info['episode'])
                self.send_json({**{k:v for k,v in metadata.items() if k not in ('headers','audio_url','audio_headers','title')}, 'filename': filename, 'subtitles': stremio_subs, 'suggestion': suggestion})
            elif parsed.path == '/stream':
                self.handle_stream(query)
            elif parsed.path == '/api/subtitles/vtt':
                self.send_bytes(SUBTITLES.download(query.get('id', [''])[0]).encode('utf-8'), 'text/vtt; charset=utf-8')
            elif parsed.path == '/api/vtt':
                self.send_bytes(to_vtt(http_bytes(query.get('url', [''])[0])).encode('utf-8'), 'text/vtt; charset=utf-8')
            else:
                self.send_json({'error': 'Resource not found.'}, 404)
        except ValueError as error:
            self.send_json({'error': str(error)}, 400)
        except ProviderError as error:
            self.send_json({'error': str(error)}, 502)
        except (OSError, json.JSONDecodeError):
            self.send_json({'error': 'Could not complete the operation. Check the file and try again.'}, 500)

    def do_POST(self):
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 65536 or self.headers.get_content_type() != 'application/json':
                raise ValueError('Send a valid JSON request.')
            self.connection.settimeout(20)
            raw_body = self.rfile.read(length)
            if not self.allowed(mutation=True):
                return
            path = urlparse(self.path).path
            if not self.check_remote_pin(path):
                return
            data = json.loads(raw_body)
            if not isinstance(data, dict):
                raise ValueError('Invalid data.')
            host_only_routes = {
                '/api/local/open', '/api/download/folder', '/api/vlc', '/api/shutdown',
                '/api/settings', '/api/stremio/addons',
                '/api/system/open-folder', '/api/system/autostart', '/api/system/firewall',
                '/api/remote/pin', '/api/remote/toggle', '/api/jellyfin/password',
            }
            if path in host_only_routes and not self.is_host_client():
                self.send_json({'error': 'Available only on the host PC.'}, 403)
                return
            if path == '/api/remote/auth':
                token = REMOTE.verify_pin(data.get('pin'), self.client_address[0])
                secure_flag = '; Secure' if self.headers.get('X-Forwarded-Proto') == 'https' else ''
                self.send_json({'ok': True}, extra_headers={'Set-Cookie': f'sc_session={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age=1209600{secure_flag}'})
            elif path == '/api/remote/pin':
                self.send_json(REMOTE.set_pin(data.get('pin', '')))
            elif path == '/api/remote/toggle':
                target = str(data.get('target') or 'hub')
                enable = bool(data.get('enabled'))
                if enable:
                    self.send_json(REMOTE.start(target, self.server.server_address[1], JELLYFIN.has_password()))
                else:
                    self.send_json(REMOTE.stop(target))
            elif path == '/api/jellyfin/password':
                self.send_json(JELLYFIN.set_user_password(data.get('password', '')))
            elif path == '/api/jellyfin/refresh':
                self.send_json({'ok': JELLYFIN.refresh_library()})
            elif path == '/api/system/open-folder':
                self.send_json(open_media_folder(data.get('folder', 'movies')))
            elif path == '/api/system/firewall':
                self.send_json(trigger_firewall_rule())
            elif path == '/api/catalog/scan':
                count = CATALOG.scan()
                JELLYFIN.refresh_library()
                self.send_json({'count': count, **CATALOG.get_home_catalog()})
            elif path == '/api/catalog/open':
                rec = CATALOG.get_item_record(data.get('id'))
                imdb_vid = rec['imdb_id']
                if imdb_vid and rec['kind'] == 'episode' and rec.get('season') and rec.get('episode'):
                    imdb_vid = f"{imdb_vid}:{rec['season']}:{rec['episode']}"
                token = register_local_video(rec['full_path'], imdb_vid)
                suggestion = guess_search(rec['filename'])
                if rec['imdb_id']:
                    suggestion['imdb_id'] = rec['imdb_id']
                    if rec['kind'] == 'episode':
                        suggestion['type'] = 'series'
                        suggestion['title'] = rec['series_title'] or suggestion['title']
                self.send_json({
                    'item_id': rec['id'],
                    'source': 'local:' + token,
                    'filename': rec['filename'],
                    'position': rec['position'],
                    'next_episode_id': rec['next_episode_id'],
                    'imdb_id': rec['imdb_id'],
                    'suggestion': suggestion,
                    'local_subtitles': [{'id': s['id'], 'label': s['label']} for s in rec['sidecars']],
                })
            elif path == '/api/catalog/progress':
                self.send_json(CATALOG.save_progress(data.get('id'), data.get('position', 0), data.get('duration', 0)))
            elif path == '/api/stremio/addons':
                action = str(data.get('action') or 'add')
                if action == 'remove':
                    addons = ADDONS.remove_addon(data.get('key') or data.get('url'))
                else:
                    addons = ADDONS.add_addon(data.get('url'))
                self.send_json({'addons': addons})
            elif path == '/api/library/download':
                self.send_json(LIBRARY_DOWNLOADS.enqueue_from_token(data))
            elif path == '/api/library/download/cancel':
                self.send_json(LIBRARY_DOWNLOADS.cancel(data.get('id')))
            elif path == '/api/library/download/clear':
                self.send_json({'jobs': LIBRARY_DOWNLOADS.clear_finished()})
            elif path == '/api/download/category':
                category = str(data.get('category', '')).strip()
                if category not in ('movies', 'series'):
                    raise ValueError('Invalid library category.')
                target_dir = (SERVER_ROOT / ('Series' if category == 'series' else 'Filmes')).resolve()
                target_dir.mkdir(parents=True, exist_ok=True)
                with DOWNLOADS.lock:
                    DOWNLOADS.destination = target_dir
                self.send_json({'category': category, 'folder': str(target_dir)})
            elif path == '/api/subtitles/save':
                self.send_json(save_library_subtitle(data.get('source'), data.get('subtitle_id'), data.get('language', 'pt-BR')))
            elif path == '/api/download/analyze':
                self.send_json(DOWNLOADS.analyze(data.get('url')))
            elif path == '/api/download/folder':
                import tkinter
                from tkinter import filedialog
                with DIALOG_LOCK:
                    root = tkinter.Tk(); root.withdraw(); root.attributes('-topmost', True)
                    try:
                        selected = filedialog.askdirectory(parent=root, title='Choose download folder')
                    finally:
                        root.destroy()
                if selected:
                    with DOWNLOADS.lock:
                        DOWNLOADS.destination = Path(selected)
                with DOWNLOADS.lock:
                    dest = str(DOWNLOADS.destination)
                self.send_json({'folder': dest})
            elif path == '/api/download/start':
                self.send_json(DOWNLOADS.start(data.get('id'), data.get('quality','best')))
            elif path == '/api/download/cancel':
                self.send_json(DOWNLOADS.cancel(data.get('id')))
            elif path == '/api/download/open':
                downloaded = DOWNLOADS.completed_file(data.get('id'))
                token = register_local_video(downloaded)
                self.send_json({'source':'local:' + token,'filename':downloaded.name})
            elif path == '/api/shutdown':
                self.send_json({'status': 'stopping'})
                threading.Thread(target=request_shutdown, args=(self.server,), daemon=True).start()
            elif path == '/api/local/open':
                selected = choose_local_video()
                if not selected:
                    self.send_json({'cancelled': True})
                    return
                selected = Path(selected).resolve()
                if not selected.is_file() or selected.suffix.lower() not in VIDEO_EXTENSIONS:
                    raise ValueError('Select a supported video file.')
                token = register_local_video(selected)
                self.send_json({'source': 'local:' + token, 'filename': selected.name, 'suggestion': guess_search(selected.name)})
            elif path == '/api/playback':
                source = resolve_target_url(str(data.get('url', '')))
                requested = float(data.get('t', 0))
                if not math.isfinite(requested) or requested < 0:
                    raise ValueError('Invalid playback position.')
                metadata = probe_media(source)
                copied = metadata['video_codec'] in ('h264', 'hevc', 'av1') and not data.get('compatibility', False) and int(data.get('max_height') or 0) <= 0
                self.send_json({'start': seek_start(source, requested) if copied else requested})
            elif path == '/api/settings':
                self.send_json(SUBTITLES.configure(data))
            elif path == '/api/subtitles/search':
                self.send_json(SUBTITLES.search(data))
            elif path == '/api/vlc':
                source = resolve_target_url(str(data.get('url', '')))
                vlc = find_vlc()
                if not vlc:
                    raise ValueError('VLC was not found on this computer.')
                seek = float(data.get('t', 0))
                if not math.isfinite(seek) or seek < 0:
                    raise ValueError('Invalid playback position.')
                subprocess.Popen([vlc, source, f'--start-time={int(seek)}'], stdin=subprocess.DEVNULL)
                self.send_json({'status': 'ok'})
            else:
                self.send_json({'error': 'Resource not found.'}, 404)
        except (ValueError, TypeError) as error:
            self.send_json({'error': str(error)}, 400)
        except ProviderError as error:
            self.send_json({'error': str(error)}, 502)
        except Exception:
            self.send_json({'error': 'Could not complete the local operation. Try again.'}, 500)

    def serve_asset(self, filename='index.html'):
        bundle = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent.parent))
        candidates = [Path(sys.executable).parent / 'assets' / filename, bundle / 'assets' / filename, Path(__file__).resolve().parent.parent / 'assets' / filename] if getattr(sys, 'frozen', False) else [bundle / 'assets' / filename]
        mime = (
            'application/manifest+json' if filename.endswith('.webmanifest')
            else 'text/javascript' if filename.endswith('.js')
            else 'text/css' if filename.endswith('.css')
            else 'text/html'
        ) + '; charset=utf-8'
        for path in candidates:
            if path.is_file():
                self.send_bytes(path.read_bytes(), mime)
                return
        self.send_json({'error': 'assets/index.html was not found.'}, 404)

    def send_bytes(self, content, content_type, code=200, cache='no-store', extra_headers=None):
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(content)))
        self.send_header('Cache-Control', cache)
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(content)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def send_json(self, data, code=200, extra_headers=None):
        self.send_bytes(json.dumps(data, ensure_ascii=False).encode('utf-8'), 'application/json; charset=utf-8', code, extra_headers=extra_headers)

    def handle_range_media(self, item_id):
        rec = CATALOG.get_item_record(item_id)
        path = Path(rec['full_path'])
        size = path.stat().st_size
        mime = mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
        range_header = self.headers.get('Range', '')
        start, end = 0, size - 1
        status = 200
        if range_header.startswith('bytes='):
            parts = range_header[6:].split(',')[0].split('-')
            start = int(parts[0]) if parts[0] else 0
            end = int(parts[1]) if len(parts) > 1 and parts[1] else size - 1
            if start < 0 or end >= size or start > end:
                self.send_response(416)
                self.send_header('Content-Range', f'bytes */{size}')
                self.end_headers()
                return
            status = 206
        length = end - start + 1
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('Content-Length', str(length))
        if status == 206:
            self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
        self.end_headers()
        with path.open('rb') as handle:
            handle.seek(start)
            remaining = length
            try:
                while remaining > 0:
                    chunk = handle.read(min(262144, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

    def handle_m3u_playlist(self):
        host = self.headers.get('Host') or f'{get_lan_ipv4()}:{self.server.server_address[1]}'
        proto = 'https' if self.headers.get('X-Forwarded-Proto') == 'https' else 'http'
        lines = ['#EXTM3U']
        for item in CATALOG.all_playable_entries():
            if item['kind'] == 'episode':
                label = f"{item['series_title']} S{int(item['season'] or 1):02d}E{int(item['episode'] or 1):02d}"
            else:
                label = item['title']
            lines.append(f'#EXTINF:-1,{label}')
            lines.append(f"{proto}://{host}/media/{item['id']}")
        self.send_bytes(('\n'.join(lines) + '\n').encode('utf-8'), 'audio/x-mpegurl; charset=utf-8')

    def handle_stream(self, query):
        seek = float(query.get('t', ['0'])[0])
        if not math.isfinite(seek) or seek < 0:
            raise ValueError('Invalid playback position.')
        source = resolve_target_url(query.get('url', [''])[0])
        metadata = probe_media(source)
        max_h = int(query.get('maxh', ['0'])[0]) if str(query.get('maxh', ['0'])[0]).isdigit() else 0
        cmd = build_stream_command(source, metadata, seek, query.get('a', [''])[0], query.get('compat', ['0'])[0] == '1', max_height=max_h, use_nvenc=detect_nvenc())
        with tempfile.TemporaryFile() as errors:
            try:
                process = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=errors, creationflags=NO_WINDOW)
                attach_to_server(process)
            except FileNotFoundError:
                raise ValueError('FFmpeg not found. Install FFmpeg to play videos.') from None
            try:
                chunk = process.stdout.read(64 * 1024)
                if not chunk:
                    raise ValueError('FFmpeg could not convert this video. Try opening it in VLC.')
                self.send_response(200)
                self.send_header('Content-Type', 'video/mp4')
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Connection', 'close')
                self.end_headers()
                while chunk:
                    self.wfile.write(chunk)
                    self.wfile.flush()
                    chunk = process.stdout.read(64 * 1024)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                process.stdout.close()


def stop_services():
    for service, method in ((LIBRARY_DOWNLOADS, 'shutdown'), (DOWNLOADS, 'shutdown'), (REMOTE, 'stop'), (JELLYFIN, 'stop')):
        try:
            getattr(service, method)()
        except (OSError, RuntimeError):
            pass


def arm_exit_timeout(server):
    if getattr(server, 'allow_force_exit', False) and not getattr(server, '_exit_timer', None):
        timer = threading.Timer(6.0, lambda: os._exit(0))
        timer.daemon = True
        server._exit_timer = timer
        timer.start()


def request_shutdown(server):
    arm_exit_timeout(server)
    try:
        stop_services()
    finally:
        server.shutdown()


def open_browser(port=PORT, path=''):
    url = f'http://{HOST}:{port}{path}'
    chrome = find_chrome()
    if chrome:
        subprocess.Popen([chrome, url])
    else:
        webbrowser.open(url)


def start_tray(server, port):
    import pystray
    from PIL import Image
    icon_path = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent.parent)) / 'assets' / 'app.ico'
    image = Image.open(icon_path)

    def stop_all(icon, item):
        threading.Thread(target=request_shutdown, args=(server,), daemon=True).start()

    icon = pystray.Icon('StreamChromeServer', image, 'StreamChrome Server', menu=pystray.Menu(
        pystray.MenuItem('Open StreamChrome Server', lambda icon, item: open_browser(port), default=True),
        pystray.MenuItem('Open Jellyfin', lambda icon, item: open_browser(JELLYFIN.port)),
        pystray.MenuItem('Exit', stop_all),
    ))
    threading.Thread(target=icon.run, daemon=True).start()
    return icon


def main():
    if os.name == 'nt':
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('OhCato.StreamChromeServer')
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=PORT)
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--background', action='store_true')
    parser.add_argument('--local-only', action='store_true')
    args = parser.parse_args()
    lan_mode = not args.local_only
    bind_host = HOST if args.local_only else '0.0.0.0'
    try:
        server = HTTPServer((bind_host, args.port), StreamProHandler)
        server.lan_mode = lan_mode
        server.allow_force_exit = True
    except OSError:
        try:
            with urllib.request.urlopen(f'http://{HOST}:{args.port}/api/status', timeout=2) as response:
                status = json.load(response)
            if 'version' in status and 'ffmpeg' in status and 'ffprobe' in status:
                if not args.no_browser and not args.background:
                    open_browser(args.port)
                return 0
        except (OSError, ValueError):
            pass
        print(f'Port {args.port} is in use. Close the previous version and try again.')
        return 1
    REMOTE.hub_port = args.port
    threading.Thread(target=JELLYFIN.start, daemon=True).start()
    threading.Thread(target=CATALOG.scan, daemon=True).start()
    print(f'StreamChrome Server: http://{bind_host}:{args.port}')
    tray = start_tray(server, args.port) if not args.no_browser else None
    if not args.no_browser and not args.background:
        threading.Timer(0.6, open_browser, args=(args.port,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        arm_exit_timeout(server)
        stop_services()
        if tray:
            tray.stop()
        server.server_close()
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
