"""IMDb + Stremio Cinemeta discovery, rich metadata, and Stremio addon stream/subtitle resolution."""
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
import hashlib
import hmac
import json
import re
import secrets
import threading
import time
from urllib.parse import quote, urlparse
import urllib.error
import urllib.request

from subtitles import load_settings, save_settings

CINEMETA_BASE = 'https://v3-cinemeta.strem.io'
IMDB_SUGGEST_BASE = 'https://v3.sg.media-imdb.com/suggestion'
USER_AGENT = 'StreamChromeServer/1.0'
IMDB_ID_RE = re.compile(r'^tt\d{7,10}$')
VIDEO_ID_RE = re.compile(r'^tt\d{7,10}(?::\d+:\d+)?$')
MAX_ADDON_STREAMS = 512

ADDON_STREAMS = OrderedDict()
ADDON_STREAMS_LOCK = threading.RLock()
STREAM_ID_SECRET = secrets.token_bytes(32)

_NOISE_EMOJI_RE = re.compile(r'[\U0001F300-\U0001FAFF\u2600-\u27BF]+')
_PT_AUDIO_RE = re.compile(r'\b(portuguese|portugu[eê]s|pt-?br|dublado|dual\s*audio)\b|(\bpob\b|\bpor\b)', re.I)
_RES_RE = re.compile(r'\b(2160p|4k|uhd|1080p|720p|480p)\b', re.I)


def urlopen_resilient(req, timeout=25):
    """Open a request with the platform's normal TLS certificate validation."""
    return urllib.request.urlopen(req, timeout=timeout)


def _http_json(url, timeout=15, limit=6 * 1024 * 1024):
    parsed = urlparse(url)
    if parsed.scheme != 'https' or not parsed.hostname:
        raise ValueError('Only HTTPS addon and metadata URLs are supported.')
    req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT, 'Accept': 'application/json'})
    try:
        with urlopen_resilient(req, timeout=timeout) as resp:
            raw = resp.read(limit + 1)
            if len(raw) > limit:
                raise ValueError('Metadata response exceeded size limit.')
            return json.loads(raw.decode('utf-8'))
    except urllib.error.HTTPError as err:
        raise ValueError(f'Metadata service returned HTTP {err.code}.') from None
    except (urllib.error.URLError, TimeoutError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError('Could not reach the metadata service.') from None


def mask_manifest_url(url):
    parsed = urlparse(str(url or ''))
    hostname = parsed.hostname or ''
    if ':' in hostname:
        hostname = '[' + hostname + ']'
    port = ':' + str(parsed.port) if parsed.port else ''
    return f'{parsed.scheme}://{hostname}{port}/••••/manifest.json'


def _clean_text(text):
    value = str(text or '').replace('⚡', ' Instant ').replace('🌎', '').replace('📝', 'Sub: ')
    value = _NOISE_EMOJI_RE.sub('', value)
    value = re.sub(r'[ \t]+', ' ', value)
    return value.strip()


def _format_size(size_bytes):
    try:
        size = int(size_bytes or 0)
    except (ValueError, TypeError):
        return ''
    if size <= 0:
        return ''
    gb = size / (1024 ** 3)
    if gb >= 1:
        return f'{gb:.2f} GB'
    return f'{max(1, round(size / (1024 ** 2)))} MB'


def _header_dict_and_ffmpeg(proxy_headers):
    safe = {}
    if isinstance(proxy_headers, dict):
        for k, v in proxy_headers.items():
            ks, vs = str(k), str(v)
            if ks.lower() in ('user-agent', 'referer', 'origin') and '\r' not in vs and '\n' not in vs:
                safe[ks] = vs
    ffmpeg_headers = ''.join(f'{k}: {v}\r\n' for k, v in safe.items())
    return safe, ffmpeg_headers


def register_addon_stream(payload):
    token = secrets.token_urlsafe(18)
    with ADDON_STREAMS_LOCK:
        ADDON_STREAMS[token] = dict(payload)
        while len(ADDON_STREAMS) > MAX_ADDON_STREAMS:
            ADDON_STREAMS.popitem(last=False)
    return 'addon:' + token


def get_addon_stream(raw_or_token):
    token = str(raw_or_token or '').strip()
    if token.startswith('addon:'):
        token = token[len('addon:'):]
    with ADDON_STREAMS_LOCK:
        entry = ADDON_STREAMS.get(token)
    if not entry:
        raise ValueError('Stream expired. Reload streams and try again.')
    return dict(entry)


def _normalize_catalog_item(item, fallback_kind='movie'):
    imdb_id = str(item.get('imdb_id') or item.get('id') or '').strip()
    if not IMDB_ID_RE.match(imdb_id):
        return None
    kind = item.get('type') or fallback_kind
    if kind not in ('movie', 'series'):
        kind = fallback_kind
    return {
        'imdb_id': imdb_id,
        'kind': kind,
        'title': str(item.get('name') or item.get('title') or imdb_id),
        'year': str(item.get('releaseInfo') or item.get('year') or '')[:9],
        'imdb_rating': str(item.get('imdbRating') or ''),
        'poster': str(item.get('poster') or ''),
        'genres': list(item.get('genres') or [])[:3],
    }


def _imdb_suggest(query, kind='movie'):
    clean = re.sub(r'\s+', ' ', str(query or '')).strip()
    if len(clean) < 2:
        return []
    first = next((c.lower() for c in clean if c.isalnum()), 'x')
    data = _http_json(f'{IMDB_SUGGEST_BASE}/{first}/{quote(clean)}.json')
    allowed = {'movie', 'tvMovie'} if kind == 'movie' else {'tvSeries', 'tvMiniSeries'}
    results = []
    for entry in data.get('d', []):
        imdb_id = str(entry.get('id') or '')
        if not IMDB_ID_RE.match(imdb_id):
            continue
        qid = entry.get('qid')
        if qid and qid not in allowed:
            continue
        img = str((entry.get('i') or {}).get('imageUrl') or '')
        if img.startswith('https://'):
            img = re.sub(r'\._V1_.*\.jpg$', '._V1_UX500_.jpg', img)
        results.append({
            'imdb_id': imdb_id,
            'kind': kind,
            'title': str(entry.get('l') or imdb_id),
            'year': str(entry.get('y') or ''),
            'imdb_rating': '',
            'poster': img,
            'genres': [],
        })
    return results


def get_genres(kind='movie'):
    if kind not in ('movie', 'series'):
        raise ValueError('Invalid catalog kind.')
    manifest = _http_json(f'{CINEMETA_BASE}/manifest.json')
    for catalog in manifest.get('catalogs', []):
        if catalog.get('type') == kind and catalog.get('id') == 'top':
            for extra in catalog.get('extra', []):
                if extra.get('name') == 'genre':
                    return list(dict.fromkeys(str(g) for g in extra.get('options', []) if g))
    return []


def discover(kind='movie', catalog='top', search='', genre=''):
    if kind not in ('movie', 'series'):
        raise ValueError('Invalid catalog kind.')
    if catalog not in ('top', 'imdbRating'):
        catalog = 'top'
    query = str(search or '').strip()
    if query:
        items = []
        seen = set()
        if IMDB_ID_RE.match(query):
            try:
                meta = get_meta(kind, query)
                return [{
                    'imdb_id': meta['imdb_id'], 'kind': meta['kind'], 'title': meta['title'],
                    'year': meta['year'], 'imdb_rating': meta['imdb_rating'], 'poster': meta['poster'],
                    'genres': meta['genres'][:3],
                }]
            except ValueError:
                pass
        for fetcher in (
            lambda: _http_json(f'{CINEMETA_BASE}/catalog/{kind}/top/search={quote(query)}.json').get('metas', []),
            lambda: _imdb_suggest(query, kind),
        ):
            try:
                raw_list = fetcher()
            except ValueError:
                continue
            for entry in raw_list:
                norm = entry if 'imdb_id' in entry and 'title' in entry else _normalize_catalog_item(entry, kind)
                if norm and norm['imdb_id'] not in seen:
                    seen.add(norm['imdb_id'])
                    items.append(norm)
        if genre:
            items = [item for item in items if genre in item.get('genres', [])]
        return items[:40]

    extra = f'/genre={quote(str(genre), safe="")}' if genre else ''
    data = _http_json(f'{CINEMETA_BASE}/catalog/{kind}/{catalog}{extra}.json')
    out = []
    for entry in data.get('metas', []):
        norm = _normalize_catalog_item(entry, kind)
        if norm:
            out.append(norm)
        if len(out) >= 48:
            break
    return out


def get_meta(kind, imdb_id):
    if kind not in ('movie', 'series'):
        raise ValueError('Invalid media type.')
    if not IMDB_ID_RE.match(str(imdb_id or '')):
        raise ValueError('Invalid IMDb ID.')
    data = _http_json(f'{CINEMETA_BASE}/meta/{kind}/{imdb_id}.json')
    meta = data.get('meta') or {}
    if not meta:
        raise ValueError('Title not found in Cinemeta/IMDb.')
    seasons_map = {}
    for vid in meta.get('videos', []):
        season = int(vid.get('season') or 0)
        episode = int(vid.get('episode') or 0)
        if season <= 0 and episode <= 0:
            continue
        seasons_map.setdefault(season, []).append({
            'id': str(vid.get('id') or f'{imdb_id}:{season}:{episode}'),
            'season': season,
            'episode': episode,
            'title': str(vid.get('name') or vid.get('title') or f'Episode {episode}'),
            'overview': str(vid.get('overview') or ''),
            'thumbnail': str(vid.get('thumbnail') or ''),
            'released': str(vid.get('released') or '')[:10],
        })
    seasons = [
        {'season': s, 'episodes': sorted(seasons_map[s], key=lambda e: e['episode'])}
        for s in sorted(seasons_map)
    ]
    return {
        'imdb_id': imdb_id,
        'kind': kind,
        'title': str(meta.get('name') or imdb_id),
        'year': str(meta.get('releaseInfo') or meta.get('year') or ''),
        'runtime': str(meta.get('runtime') or ''),
        'imdb_rating': str(meta.get('imdbRating') or ''),
        'description': str(meta.get('description') or ''),
        'genres': list(meta.get('genres') or [])[:5],
        'cast': list(meta.get('cast') or [])[:8],
        'director': list(meta.get('director') or [])[:3],
        'poster': str(meta.get('poster') or ''),
        'background': str(meta.get('background') or ''),
        'logo': str(meta.get('logo') or ''),
        'seasons': seasons,
        'stremio_url': f'stremio:///detail/{kind}/{imdb_id}',
        'imdb_url': f'https://www.imdb.com/title/{imdb_id}/',
    }


def _normalize_manifest_url(url):
    text = str(url or '').strip()
    if text.startswith('stremio://'):
        text = 'https://' + text[len('stremio://'):]
    parsed = urlparse(text)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or not parsed.path.endswith('/manifest.json'):
        raise ValueError('Paste a valid HTTPS Stremio manifest.json URL.')
    return text


def _parse_addon_stream(addon_name, item, kind, video_id):
    stream_url = str(item.get('url') or '').strip()
    info_hash = str(item.get('infoHash') or '').strip()
    hints = item.get('behaviorHints') or {}
    raw_name = str(item.get('name') or addon_name).strip()
    raw_desc = str(item.get('description') or item.get('title') or '').strip()

    clean_name = _clean_text(raw_name).replace('[TB Instant ]', '[TB Instant]')
    filename = _clean_text(hints.get('filename') or '')
    desc_lines = []
    for line in raw_desc.splitlines():
        cleaned = _clean_text(line)
        if not cleaned or (filename and filename in cleaned):
            continue
        desc_lines.append(cleaned)
    summary = ' · '.join(desc_lines)[:220] or clean_name

    combined = f'{raw_name} {raw_desc} {filename}'
    res_match = _RES_RE.search(combined)
    resolution = res_match.group(1).upper() if res_match else 'SD'
    if resolution in ('2160P', 'UHD'):
        resolution = '4K'
    elif resolution in ('1080P', '720P', '480P'):
        resolution = resolution.lower()

    video_size = int(hints.get('videoSize') or 0) if str(hints.get('videoSize') or '').isdigit() else 0
    size_label = _format_size(video_size)
    if not size_label:
        m_size = re.search(r'(\d+(?:[.,]\d+)?\s*(?:GB|MB))', combined, re.I)
        if m_size:
            size_label = m_size.group(1).upper()

    pt_audio = bool(_PT_AUDIO_RE.search(combined))
    cached = bool(stream_url.startswith(('https://', 'http://')) and ('⚡' in raw_name or 'TB' in raw_name or 'RD' in raw_name or 'AD' in raw_name or 'instant' in combined.lower()))

    parts = video_id.split(':')
    imdb_id = parts[0]
    season = int(parts[1]) if len(parts) == 3 and parts[1].isdigit() else None
    episode = int(parts[2]) if len(parts) == 3 and parts[2].isdigit() else None
    binge_group = str(hints.get('bingeGroup') or '')

    if stream_url.startswith(('https://', 'http://')):
        hdrs, ff_hdrs = _header_dict_and_ffmpeg((hints.get('proxyHeaders') or {}).get('request'))
        stream_token = register_addon_stream({
            'url': stream_url,
            'headers': hdrs,
            'ffmpeg_headers': ff_hdrs,
            'filename': filename or f'{imdb_id}.mkv',
            'video_size': video_size,
            'video_id': video_id,
            'kind': kind,
            'imdb_id': imdb_id,
            'season': season,
            'episode': episode,
            'binge_group': binge_group,
            'name': clean_name,
        })
        return {
            'addon': addon_name,
            'source_key': hmac.new(STREAM_ID_SECRET, json.dumps([video_id, stream_url, hdrs], sort_keys=True).encode('utf-8'), hashlib.sha256).hexdigest(),
            'name': clean_name[:90],
            'summary': summary,
            'label': summary,
            'filename': filename[:180],
            'resolution': resolution,
            'size_bytes': video_size,
            'size_label': size_label,
            'pt_audio': pt_audio,
            'cached': cached,
            'binge_group': binge_group,
            'stream_token': stream_token,
            'playable': True,
        }
    if info_hash:
        return {
            'addon': addon_name,
            'name': clean_name[:90],
            'summary': summary,
            'label': summary,
            'filename': filename[:180],
            'resolution': resolution,
            'size_bytes': video_size,
            'size_label': size_label,
            'pt_audio': pt_audio,
            'cached': False,
            'binge_group': binge_group,
            'playable': False,
            'stremio_url': f'stremio:///detail/{kind}/{imdb_id}/{video_id}',
        }
    return None


class StremioAddonManager:
    def __init__(self, settings_path):
        self.settings_path = settings_path
        self._lock = threading.RLock()
        self._discovery_cache = None

    def _catalog_entries(self):
        addons = self.list_addons()
        sources = [{'url': CINEMETA_BASE + '/manifest.json', 'name': 'Cinemeta'}, *addons]
        signature = tuple(str(source.get('url') or '') for source in sources)
        with self._lock:
            cached = self._discovery_cache
            if cached and cached[0] == signature and time.monotonic() - cached[1] < 300:
                return cached[2]

        def read_source(source):
            url = str(source.get('url') or '')
            try:
                manifest = _http_json(_normalize_manifest_url(url), timeout=12)
            except ValueError:
                return []
            entries = []
            for catalog in manifest.get('catalogs', []):
                if not isinstance(catalog, dict):
                    continue
                kind, catalog_id = str(catalog.get('type') or ''), str(catalog.get('id') or '')
                if not kind or not catalog_id:
                    continue
                extras = {str(extra.get('name')): extra for extra in catalog.get('extra', []) if isinstance(extra, dict)}
                for name in catalog.get('extraSupported', []):
                    extras.setdefault(str(name), {})
                for name in catalog.get('extraRequired', []):
                    extras.setdefault(str(name), {})['isRequired'] = True
                genres = list(dict.fromkeys(str(g) for g in extras.get('genre', {}).get('options', []) if g))
                key = hashlib.sha256(json.dumps([url, kind, catalog_id]).encode('utf-8')).hexdigest()[:24]
                public = {
                    'key': key, 'kind': kind,
                    'name': str(catalog.get('name') or catalog_id)[:120],
                    'source': str(source.get('name') or manifest.get('name') or 'Addon')[:80],
                    'genres': genres, 'genre_filter': 'genre' in extras,
                    'search': 'search' in extras, 'pagination': 'skip' in extras,
                    'required_filters': [name for name, extra in extras.items() if extra.get('isRequired')],
                }
                entries.append((public, url[:-len('/manifest.json')], catalog_id))
            return entries

        with ThreadPoolExecutor(max_workers=min(6, len(sources))) as pool:
            entries = [entry for chunk in pool.map(read_source, sources) for entry in chunk]
        with self._lock:
            self._discovery_cache = (signature, time.monotonic(), entries)
        return entries

    def discovery_catalogs(self):
        return [dict(entry[0]) for entry in self._catalog_entries()]

    def discover_catalog(self, key, kind, genre='', search='', skip=0, with_page=False):
        if not isinstance(skip, int) or skip < 0:
            raise ValueError('Invalid catalog offset.')
        entry = next((entry for entry in self._catalog_entries() if entry[0]['key'] == key and entry[0]['kind'] == kind), None)
        if not entry:
            raise ValueError('Catalog unavailable. Reload Discover and select a catalog.')
        catalog, base, catalog_id = entry
        supplied = {'genre': str(genre or '').strip(), 'search': str(search or '').strip()}
        if catalog['pagination'] and ('skip' in catalog['required_filters'] or skip):
            supplied['skip'] = str(skip)
        for name in catalog['required_filters']:
            if not supplied.get(name):
                raise ValueError(f'This catalog requires a {name} filter.')
        if supplied['genre'] and (not catalog['genre_filter'] or catalog['genres'] and supplied['genre'] not in catalog['genres']):
            raise ValueError('This genre is not available for the selected catalog.')
        if supplied['search'] and not catalog['search']:
            raise ValueError('This catalog does not support search. Select a searchable catalog or clear the search.')
        if skip and not catalog['pagination']:
            raise ValueError('This catalog does not support pagination.')
        if skip:
            supplied['skip'] = str(skip)
        extras = '&'.join(f'{name}={quote(value, safe="")}' for name, value in supplied.items() if value)
        endpoint = f'{base}/catalog/{quote(kind, safe="")}/{quote(catalog_id, safe="")}'
        data = _http_json(endpoint + ('/' + extras if extras else '') + '.json')
        items, seen = [], set()
        raw_items = data.get('metas') or []
        for raw in raw_items:
            if not isinstance(raw, dict):
                continue
            item_id = str(raw.get('id') or raw.get('imdb_id') or '').strip()
            item_kind = str(raw.get('type') or kind)
            if not item_id or (item_kind, item_id) in seen:
                continue
            seen.add((item_kind, item_id))
            imdb_id = str(raw.get('imdb_id') or item_id)
            playable = item_kind in ('movie', 'series') and bool(IMDB_ID_RE.fullmatch(imdb_id))
            items.append({
                'id': item_id, 'imdb_id': imdb_id if playable else '', 'kind': item_kind,
                'title': str(raw.get('name') or raw.get('title') or item_id),
                'year': str(raw.get('releaseInfo') or raw.get('year') or '')[:9],
                'poster': str(raw.get('poster') or ''), 'imdb_rating': str(raw.get('imdbRating') or ''),
                'genres': list(raw.get('genres') or [])[:3], 'playable': playable,
                'stremio_url': f'stremio:///detail/{quote(item_kind, safe="")}/{quote(item_id, safe="")}',
            })
        if with_page:
            return {'items': items, 'next_skip': skip + len(raw_items), 'has_more': catalog['pagination'] and bool(raw_items)}
        return items

    def _load(self):
        try:
            return load_settings(self.settings_path)
        except (ValueError, OSError):
            return {}

    def _save(self, data):
        try:
            save_settings(data, self.settings_path)
        except (ValueError, OSError):
            pass

    def list_addons(self):
        with self._lock:
            return list(self._load().get('stremio_addons', []))

    def public_addons(self):
        out = []
        for a in self.list_addons():
            url = str(a.get('url') or '')
            key = a.get('key') or hashlib.sha256(url.encode('utf-8')).hexdigest()[:16]
            out.append({
                'key': key,
                'id': ('addon-' + key) if str(a.get('id', '')).startswith(('http://', 'https://')) else a.get('id', ''),
                'name': a.get('name', 'Addon'),
                'description': a.get('description', ''),
                'masked_url': mask_manifest_url(url),
                'resources': list(a.get('resources') or []),
            })
        return out

    def add_addon(self, manifest_url):
        clean_url = _normalize_manifest_url(manifest_url)
        manifest = _http_json(clean_url, timeout=15)
        name = str(manifest.get('name') or urlparse(clean_url).hostname)
        addon_id = str(manifest.get('id') or ('addon-' + hashlib.sha256(clean_url.encode('utf-8')).hexdigest()[:16]))
        raw_res = manifest.get('resources') or []
        resources = [r.get('name') if isinstance(r, dict) else str(r) for r in raw_res]
        entry = {
            'key': hashlib.sha256(clean_url.encode('utf-8')).hexdigest()[:16],
            'url': clean_url,
            'base_url': clean_url[:-len('/manifest.json')],
            'id': addon_id,
            'name': name[:80],
            'description': str(manifest.get('description') or '')[:160],
            'resources': resources,
        }
        with self._lock:
            data = self._load()
            addons = [a for a in data.get('stremio_addons', []) if a.get('url') != clean_url]
            addons.append(entry)
            data['stremio_addons'] = addons[:20]
            self._save(data)
            return self.public_addons()

    def remove_addon(self, key_or_url):
        target = str(key_or_url or '').strip()
        with self._lock:
            data = self._load()
            kept = []
            for a in data.get('stremio_addons', []):
                key = a.get('key') or hashlib.sha256(str(a.get('url') or '').encode('utf-8')).hexdigest()[:16]
                if target in (a.get('url'), key):
                    continue
                kept.append(a)
            data['stremio_addons'] = kept
            self._save(data)
            return self.public_addons()

    def resolve_streams(self, kind, video_id):
        if kind not in ('movie', 'series'):
            raise ValueError('Invalid stream type.')
        if not VIDEO_ID_RE.match(str(video_id or '')):
            raise ValueError('Invalid Stremio video ID.')
        addons = self.list_addons()
        if not addons:
            return []

        def query_one(addon):
            endpoint = f"{addon['base_url']}/stream/{kind}/{quote(video_id, safe=':')}.json"
            try:
                payload = _http_json(endpoint, timeout=25)
            except ValueError:
                return []
            streams = []
            for item in payload.get('streams', [])[:60]:
                parsed = _parse_addon_stream(addon['name'], item, kind, video_id)
                if parsed:
                    streams.append(parsed)
            return streams

        collected = []
        with ThreadPoolExecutor(max_workers=min(6, len(addons))) as pool:
            for chunk in pool.map(query_one, addons):
                collected.extend(chunk)
        return collected

    def resolve_subtitles(self, kind, video_id):
        if kind not in ('movie', 'series'):
            return []
        if not VIDEO_ID_RE.match(str(video_id or '')):
            return []
        addons = [a for a in self.list_addons() if not a.get('resources') or 'subtitles' in a.get('resources', [])]
        if not addons:
            return []

        def query_subs(addon):
            endpoint = f"{addon['base_url']}/subtitles/{kind}/{quote(video_id, safe=':')}.json"
            try:
                payload = _http_json(endpoint, timeout=12)
            except ValueError:
                return []
            out = []
            for item in payload.get('subtitles', [])[:40]:
                url = str(item.get('url') or '').strip()
                if not url.startswith('https://'):
                    continue
                out.append({
                    'url': url,
                    'lang': str(item.get('lang') or '').strip().lower(),
                    'release': str(item.get('id') or '')[:80],
                    'addon': addon.get('name') or 'Addon',
                })
            return out

        collected = []
        with ThreadPoolExecutor(max_workers=min(4, len(addons))) as pool:
            for chunk in pool.map(query_subs, addons):
                collected.extend(chunk)
        priority = {'pob': 0, 'pt-br': 0, 'por': 1, 'pt': 1, 'eng': 2, 'en': 2, 'spa': 3, 'es': 3}
        collected.sort(key=lambda s: priority.get(s['lang'], 9))
        return collected[:40]
