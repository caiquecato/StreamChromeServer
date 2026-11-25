"""Official subtitle providers and local, encrypted settings (standard library only)."""
import ctypes
from ctypes import wintypes
import io
import json
import os
from pathlib import Path
import re
import secrets
import threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlencode, urljoin, urlparse
import urllib.request
import urllib.error
import zipfile

USER_AGENT = 'StreamChromePro v2.0'
MAX_SUBTITLE = 8 * 1024 * 1024
SETTINGS_PATH = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'StreamChromePro' / 'settings.dat'
LANGUAGES = {'pt-BR': ('BR_PT', 'pt-br'), 'pt': ('PT', 'pt'), 'en': ('EN', 'en'), 'es': ('ES', 'es'), 'fr': ('FR', 'fr'), 'it': ('IT', 'it'), 'de': ('DE', 'de'), 'ja': ('JA', 'ja')}

class ProviderError(Exception):
    pass


def _crypt(data, decrypt=False):
    if os.name != 'nt':
        raise ValueError('Protected storage requires Windows.')
    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output = Blob()
    function = ctypes.windll.crypt32.CryptUnprotectData if decrypt else ctypes.windll.crypt32.CryptProtectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    function.restype = wintypes.BOOL
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
        raise ValueError('Could not protect or open the keys in this Windows account.')
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        ctypes.windll.kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        ctypes.windll.kernel32.LocalFree(output.data)


def save_settings(settings, path=SETTINGS_PATH):
    encrypted = _crypt(json.dumps(settings).encode('utf-8'))
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_bytes(encrypted)
    temporary.replace(path)


def load_settings(path=SETTINGS_PATH):
    path = Path(path)
    return json.loads(_crypt(path.read_bytes(), decrypt=True)) if path.exists() else {}


def http_bytes(url, headers=None, payload=None, limit=MAX_SUBTITLE):
    if urlparse(url).scheme != 'https':
        raise ProviderError('The provider returned an insecure address.')
    request_headers = {'User-Agent': USER_AGENT, **(headers or {})}
    body = None
    if payload is not None:
        body = json.dumps(payload).encode('utf-8')
        request_headers['Content-Type'] = 'application/json'
    request = urllib.request.Request(url, data=body, headers=request_headers)
    try:
        with urllib.request.urlopen(request, timeout=18) as response:
            content = response.read(limit + 1)
            if len(content) > limit:
                raise ProviderError('The provider response exceeded the size limit.')
            return content
    except urllib.error.HTTPError as error:
        message = {401: 'Invalid key or session; check the settings.', 403: 'Access denied; check your key and account plan.', 406: 'Download limit reached or session expired.', 429: 'Search limit reached; try again later.'}.get(error.code, f'The service responded with HTTP {error.code}.')
        raise ProviderError(message) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise ProviderError('Could not connect to the service. Check your internet connection and try again.') from None


def http_json(url, headers=None, payload=None):
    try:
        return json.loads(http_bytes(url, headers, payload, limit=4 * 1024 * 1024))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ProviderError('The service returned an invalid response.') from None


def to_vtt(content, season=None, episode=None):
    if content.startswith(b'PK'):
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            files = [item for item in archive.infolist() if not item.is_dir() and Path(item.filename).suffix.lower() in ('.srt', '.vtt')]
            if episode is not None:
                pattern = re.compile(rf'(?:s0*{season}e0*{episode}(?!\d)|(?<!\d)0*{season}x0*{episode}(?!\d))', re.I)
                exact = [item for item in files if pattern.search(item.filename)]
                if exact:
                    files = exact
                elif len(files) != 1 or re.search(r'(?:s\d+e\d+|\d+x\d+)', files[0].filename, re.I):
                    raise ValueError('The archive contains no identifiable subtitle for this episode.')
            if len(files) != 1:
                raise ValueError('The archive contains multiple subtitles; choose an individual episode release.')
            if files[0].file_size > MAX_SUBTITLE:
                raise ValueError('The subtitle exceeded the size limit.')
            with archive.open(files[0]) as member:
                content = member.read(MAX_SUBTITLE + 1)
    if len(content) > MAX_SUBTITLE:
        raise ValueError('The subtitle exceeded the size limit.')
    if content.startswith((b'\xff\xfe', b'\xfe\xff')):
        text = content.decode('utf-16')
    else:
        try:
            text = content.decode('utf-8-sig')
        except UnicodeDecodeError:
            text = content.decode('cp1252', errors='replace')
    text = text.replace('\r\n', '\n').replace('\r', '\n').strip()
    if not re.search(r'\d{2}:\d{2}(?::\d{2})?[,.]\d{3}\s+-->\s+\d{2}:\d{2}', text):
        raise ValueError('The file does not contain a valid SRT or WebVTT subtitle.')
    if text.startswith('WEBVTT'):
        return text + '\n'
    text = re.sub(r'(\d{2}:\d{2}:\d{2}),(\d{3})', r'\1.\2', text)
    return 'WEBVTT\n\n' + text + '\n'


def guess_search(filename):
    stem = Path(filename).stem
    episode = re.search(r'(?:S(\d{1,3})E(\d{1,3})|(\d{1,3})x(\d{1,3}))', stem, re.I)
    year = re.search(r'(?<!\d)((?:19|20)\d{2})(?!\d)', stem)
    stop = episode.start() if episode else year.start() if year else len(stem)
    title = re.sub(r'[._]+', ' ', stem[:stop]).strip(' -()[]')
    return {'title': title, 'type': 'series' if episode else 'movie', 'season': int(episode.group(1) or episode.group(3)) if episode else 1, 'episode': int(episode.group(2) or episode.group(4)) if episode else 1, 'year': '' if episode or not year else year.group(1)}


def validate_search(data):
    if not isinstance(data, dict):
        raise ValueError('Invalid search.')
    title = str(data.get('title', '')).strip()
    kind = data.get('type', 'movie')
    language = data.get('language', 'pt-BR')
    if not 2 <= len(title) <= 250 or kind not in ('movie', 'series') or language not in LANGUAGES:
        raise ValueError('Enter a valid title, type and language.')
    result = {'title': title, 'type': kind, 'language': language}
    if kind == 'series':
        try:
            result.update(season=int(data.get('season', 1)), episode=int(data.get('episode', 1)))
        except (ValueError, TypeError):
            raise ValueError('Enter a valid season and episode.') from None
        if not 0 <= result['season'] <= 999 or not 1 <= result['episode'] <= 9999:
            raise ValueError('Enter a valid season and episode.')
    year = str(data.get('year', '')).strip()
    if year:
        if not year.isdigit() or not 1800 <= int(year) <= 2100:
            raise ValueError('Enter a valid year.')
        result['year'] = year
    return result


class SubtitleService:
    def __init__(self, settings, settings_path=SETTINGS_PATH):
        self.settings = settings.copy()
        self.settings_path = Path(settings_path)
        self.entries = OrderedDict()
        self.lock = threading.RLock()
        self.download_lock = threading.Lock()

    def os_headers(self):
        headers = {'Api-Key': self.settings.get('opensubtitles_key', ''), 'Accept': 'application/json'}
        if self.settings.get('opensubtitles_token'):
            headers['Authorization'] = 'Bearer ' + self.settings['opensubtitles_token']
        return headers

    def os_url(self, endpoint):
        host = self.settings.get('opensubtitles_host', 'api.opensubtitles.com')
        if host not in ('api.opensubtitles.com', 'vip-api.opensubtitles.com'):
            raise ProviderError('Invalid OpenSubtitles server.')
        return f'https://{host}/api/v1/{endpoint}'

    def configure(self, changes):
        with self.lock:
            settings = self.settings.copy()
            for key in ('subdl_key', 'opensubtitles_key'):
                value = changes.get(key, '')
                if not isinstance(value, str) or len(value) > 1000:
                    raise ValueError('Invalid key.')
                if value.strip():
                    settings[key] = value.strip()
                    if key == 'opensubtitles_key':
                        settings.pop('opensubtitles_token', None)
                        settings.pop('opensubtitles_host', None)
            if changes.get('username') or changes.get('password'):
                if not settings.get('opensubtitles_key') or not changes.get('username') or not changes.get('password'):
                    raise ValueError('To connect your account, enter your OpenSubtitles key, username and password.')
                login = http_json('https://api.opensubtitles.com/api/v1/login', {'Api-Key': settings['opensubtitles_key']}, {'username': changes['username'], 'password': changes['password']})
                if not login.get('token'):
                    raise ProviderError('OpenSubtitles did not return a session. Check your username and password.')
                host = login.get('base_url') or 'api.opensubtitles.com'
                if host not in ('api.opensubtitles.com', 'vip-api.opensubtitles.com'):
                    raise ProviderError('Invalid OpenSubtitles server.')
                settings.update(opensubtitles_token=login['token'], opensubtitles_host=host)
            save_settings(settings, self.settings_path)
            self.settings = settings
        return self.status()

    def status(self):
        return {'subdl': bool(self.settings.get('subdl_key')), 'opensubtitles': bool(self.settings.get('opensubtitles_key')), 'opensubtitles_login': bool(self.settings.get('opensubtitles_token'))}

    def _subdl(self, search):
        params = {'type': 'tv' if search['type'] == 'series' else 'movie', 'languages': LANGUAGES[search['language']][0], 'unpack': 1}
        imdb = re.fullmatch(r'tt\d{7,10}', search['title'])
        params['imdb_id' if imdb else 'film_name'] = search['title']
        for key in ('season', 'episode', 'year'):
            if key in search:
                params[key] = search[key]
        data = http_json('https://api.subdl.com/api/v2/subtitles/search?' + urlencode(params), {'Authorization': 'Bearer ' + self.settings['subdl_key']})
        if data.get('status') is False or data.get('error'):
            raise ProviderError('Search rejected; check the key, limits and title.')
        entries = []
        for sub in data.get('subtitles', []):
            children = sub.get('unpack_files') or [sub]
            for item in children:
                if search['type'] == 'series':
                    if children != [sub] and (str(item.get('season')) != str(search['season']) or str(item.get('episode')) != str(search['episode'])):
                        continue
                    if children == [sub] and sub.get('full_season'):
                        continue
                    if item.get('episode') is not None and str(item['episode']) != str(search['episode']):
                        continue
                    if item.get('season') is not None and str(item['season']) != str(search['season']):
                        continue
                download = item.get('url')
                if not download:
                    continue
                download = urljoin('https://dl.subdl.com', download)
                if urlparse(download).hostname not in ('dl.subdl.com', 'api.subdl.com') or urlparse(download).scheme != 'https':
                    continue
                release = item.get('release_name') or item.get('name') or sub.get('release_name') or 'Subtitle'
                entries.append({'provider': 'SubDL', 'label': f'SubDL | {release}', 'download_url': download, 'language': search['language'], 'season': search.get('season'), 'episode': search.get('episode')})
        return entries

    def _opensubtitles(self, search):
        params = {'languages': LANGUAGES[search['language']][1], 'type': 'episode' if search['type'] == 'series' else 'movie', 'order_by': 'download_count', 'order_direction': 'desc'}
        if re.fullmatch(r'tt\d{7,10}', search['title']):
            params['parent_imdb_id' if search['type'] == 'series' else 'imdb_id'] = search['title'][2:]
        else:
            params['query'] = search['title']
        for key in ('season', 'episode', 'year'):
            if key in search:
                params[key + '_number' if key != 'year' else key] = search[key]
        data = http_json(self.os_url('subtitles') + '?' + urlencode(params), self.os_headers())
        entries = []
        for sub in data.get('data', []):
            attrs = sub.get('attributes', {})
            feature = attrs.get('feature_details', {})
            for item in attrs.get('files', []):
                if not item.get('file_id'):
                    continue
                title = feature.get('movie_name') or feature.get('title') or search['title']
                release = attrs.get('release') or item.get('file_name', '')
                entries.append({'provider': 'OpenSubtitles', 'label': f'OpenSubtitles | {title} | {release}', 'file_id': item['file_id'], 'language': search['language'], 'season': search.get('season'), 'episode': search.get('episode')})
        return entries

    def search(self, data):
        search = validate_search(data)
        providers = [('SubDL', 'subdl_key', self._subdl), ('OpenSubtitles', 'opensubtitles_key', self._opensubtitles)]
        entries, warnings = [], []
        with ThreadPoolExecutor(max_workers=2) as executor:
            pending = []
            for name, key, function in providers:
                if not self.settings.get(key):
                    warnings.append(f'{name}: configure the API key in Subtitle settings.')
                else:
                    pending.append((name, executor.submit(function, search)))
            for name, future in pending:
                try:
                    entries.extend(future.result())
                except (ProviderError, ValueError, KeyError, TypeError):
                    # No remote body/URL is echoed: it may contain account data.
                    error = future.exception()
                    warnings.append(f'{name}: {error if isinstance(error, ProviderError) else "Resposta inesperada do servico."}')
        public = []
        with self.lock:
            for entry in entries[:100]:
                token = secrets.token_urlsafe(18)
                self.entries[token] = entry
                public.append({'id': token, 'provider': entry['provider'], 'label': entry['label'], 'language': entry['language']})
            while len(self.entries) > 300:
                self.entries.popitem(last=False)
        return {'subtitles': public, 'warnings': warnings}

    def download(self, token):
        with self.download_lock:
            with self.lock:
                entry = self.entries.get(token)
            if entry is None:
                raise ValueError('Result expired. Search for subtitles again.')
            if 'vtt' not in entry:
                if entry['provider'] == 'OpenSubtitles':
                    data = http_json(self.os_url('download'), self.os_headers(), {'file_id': entry['file_id'], 'sub_format': 'srt'})
                    link = data.get('link', '')
                    host = urlparse(link).hostname or ''
                    if not host.endswith('.opensubtitles.com') and host != 'opensubtitles.com':
                        raise ProviderError('OpenSubtitles did not return a valid download link.')
                    content = http_bytes(link)
                else:
                    content = http_bytes(entry['download_url'])
                entry['vtt'] = to_vtt(content, entry.get('season'), entry.get('episode'))
            return entry['vtt']
