"""SQLite media catalog, keyless IMDb/TVMaze poster cache, and watch progress."""
from contextlib import closing
import hashlib
import html
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import threading
import time
import urllib.error
from urllib.parse import quote
import urllib.request

from subtitles import guess_search
from process_guard import run_managed

VIDEO_EXTENSIONS = {'.mp4', '.mkv', '.avi', '.mov', '.m4v', '.webm', '.ts', '.m2ts', '.mpg', '.mpeg', '.wmv'}
SUBTITLE_EXTENSIONS = {'.srt', '.vtt'}
NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
USER_AGENT = 'StreamChromeServer/1.0'
SEASON_FOLDER_RE = re.compile(r'^(?:season|temporada|temp|s)\s*0*\d+$', re.I)


def _slugify(text):
    slug = re.sub(r'[^a-z0-9]+', '-', str(text or '').lower()).strip('-')
    return slug or 'item'


def _clean_html(text):
    cleaned = re.sub(r'<[^>]+>', ' ', str(text or ''))
    cleaned = html.unescape(cleaned)
    return re.sub(r'\s+', ' ', cleaned).strip()


def _http_get_bytes(url, timeout=10, limit=6 * 1024 * 1024, headers=None):
    req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT, **(headers or {})})
    resp_cm = urllib.request.urlopen(req, timeout=timeout)
    with resp_cm as resp:
        data = resp.read(limit + 1)
        if len(data) > limit:
            raise ValueError('Response exceeded size limit.')
        return data


def _http_get_json(url, timeout=8, headers=None):
    return json.loads(_http_get_bytes(url, timeout=timeout, limit=2 * 1024 * 1024, headers=headers).decode('utf-8'))


def _resolution_label(width, height):
    h = int(height or 0)
    w = int(width or 0)
    if h >= 2000 or w >= 3600:
        return f'{w}×{h} · 4K UHD'
    if h >= 1000 or w >= 1800:
        return f'{w}×{h} · 1080p Full HD'
    if h >= 680 or w >= 1200:
        return f'{w}×{h} · 720p HD'
    return f'{w}×{h}' if (w and h) else ''


def _format_bitrate(bps):
    val = int(bps or 0)
    if val <= 0:
        return ''
    mbps = val / 1_000_000
    if mbps >= 1:
        return f'{mbps:.1f} Mbps'
    return f'{max(1, round(val / 1000))} kbps'


def _format_size_bytes(size_bytes):
    val = int(size_bytes or 0)
    if val <= 0:
        return ''
    gb = val / (1024 ** 3)
    if gb >= 1:
        return f'{gb:.2f} GB'
    return f'{max(1, round(val / (1024 ** 2)))} MB'


class CatalogService:
    def __init__(self, server_root, data_dir, ffmpeg_bin='ffmpeg', ffprobe_bin='ffprobe', enable_online_metadata=True):
        self.server_root = Path(server_root).resolve()
        self.movies_dir = self.server_root / 'Movies'
        self.series_dir = self.server_root / 'Series'
        self.data_dir = Path(data_dir).resolve()
        self.posters_dir = self.data_dir / 'posters'
        self.db_path = self.data_dir / 'library.db'
        self.ffmpeg_bin = str(ffmpeg_bin)
        self.ffprobe_bin = str(ffprobe_bin)
        self.enable_online_metadata = enable_online_metadata
        self._scan_lock = threading.RLock()
        self._enrich_lock = threading.Lock()
        self._enrich_running = False
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.posters_dir.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self):
        conn = sqlite3.connect(str(self.db_path), timeout=15)
        conn.row_factory = sqlite3.Row
        return closing(conn)

    def _init_db(self):
        with self._connect() as conn:
            conn.execute('PRAGMA journal_mode=WAL')
            conn.execute('''
                CREATE TABLE IF NOT EXISTS media_items (
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    series_key TEXT NOT NULL DEFAULT '',
                    series_title TEXT NOT NULL DEFAULT '',
                    title TEXT NOT NULL,
                    year INTEGER,
                    season INTEGER,
                    episode INTEGER,
                    rel_path TEXT UNIQUE NOT NULL,
                    filename TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL DEFAULT 0,
                    mtime INTEGER NOT NULL DEFAULT 0,
                    duration REAL NOT NULL DEFAULT 0,
                    width INTEGER NOT NULL DEFAULT 0,
                    height INTEGER NOT NULL DEFAULT 0,
                    video_codec TEXT NOT NULL DEFAULT '',
                    imdb_id TEXT NOT NULL DEFAULT '',
                    poster_file TEXT NOT NULL DEFAULT '',
                    backdrop_file TEXT NOT NULL DEFAULT '',
                    overview TEXT NOT NULL DEFAULT '',
                    cast_text TEXT NOT NULL DEFAULT '',
                    rating REAL,
                    enriched INTEGER NOT NULL DEFAULT 0
                )
            ''')
            extra_cols = (
                ('bitrate_bps', 'INTEGER NOT NULL DEFAULT 0'),
                ('fps', "TEXT NOT NULL DEFAULT ''"),
                ('video_profile', "TEXT NOT NULL DEFAULT ''"),
                ('audio_summary', "TEXT NOT NULL DEFAULT ''"),
                ('subtitle_summary', "TEXT NOT NULL DEFAULT ''"),
                ('rt_critics', "TEXT NOT NULL DEFAULT ''"),
                ('rt_audience', "TEXT NOT NULL DEFAULT ''"),
                ('genres_text', "TEXT NOT NULL DEFAULT ''"),
                ('director_text', "TEXT NOT NULL DEFAULT ''"),
            )
            for col_name, col_def in extra_cols:
                try:
                    conn.execute(f'ALTER TABLE media_items ADD COLUMN {col_name} {col_def}')
                except sqlite3.OperationalError:
                    pass
            conn.execute('''
                CREATE TABLE IF NOT EXISTS watch_progress (
                    id TEXT PRIMARY KEY,
                    position REAL NOT NULL DEFAULT 0,
                    duration REAL NOT NULL DEFAULT 0,
                    updated_at INTEGER NOT NULL DEFAULT 0
                )
            ''')
            conn.commit()

    def _parse_entry(self, path, kind):
        resolved = path.resolve()
        rel = resolved.relative_to(self.server_root)
        rel_posix = rel.as_posix()
        item_id = hashlib.sha256(rel_posix.encode('utf-8')).hexdigest()[:16]
        guessed = guess_search(resolved.name)
        title = guessed.get('title') or resolved.stem
        if '[' in title or ']' in title:
            stripped = re.sub(r'\[[^\]]*\]', ' ', resolved.stem).replace('[', ' ').replace(']', ' ')
            cleaned_guess = guess_search(re.sub(r'\s+', ' ', stripped).strip() + resolved.suffix)
            title = cleaned_guess.get('title') or re.sub(r'[\[\]]', '', title).strip()
        year_str = guessed.get('year') or ''
        year = int(year_str) if str(year_str).isdigit() else None
        season = int(guessed.get('season') or 1) if kind == 'episode' else None
        episode = int(guessed.get('episode') or 1) if kind == 'episode' else None
        series_title = ''
        series_key = ''
        if kind == 'episode':
            try:
                sub_parts = resolved.relative_to(self.series_dir.resolve()).parts[:-1]
            except ValueError:
                sub_parts = ()
            candidates = [part for part in sub_parts if not SEASON_FOLDER_RE.match(part)]
            raw_series = candidates[0] if candidates else title
            y_match = re.search(r'\(((?:19|20)\d{2})\)\s*$', raw_series)
            series_title = re.sub(r'\s*\((?:19|20)\d{2}\)\s*$', '', raw_series).strip() or raw_series
            if year is None and y_match:
                year = int(y_match.group(1))
            series_key = _slugify(series_title)
        elif year is None:
            y_match = re.search(r'\(((?:19|20)\d{2})\)\s*$', resolved.parent.name)
            if y_match:
                year = int(y_match.group(1))
        stat = resolved.stat()
        return {
            'id': item_id,
            'kind': kind,
            'series_key': series_key,
            'series_title': series_title,
            'title': title,
            'year': year,
            'season': season,
            'episode': episode,
            'rel_path': rel_posix,
            'filename': resolved.name,
            'size_bytes': stat.st_size,
            'mtime': int(stat.st_mtime),
        }

    def scan(self, async_enrich=True, reenrich_missing=False):
        self.movies_dir.mkdir(parents=True, exist_ok=True)
        self.series_dir.mkdir(parents=True, exist_ok=True)
        discovered = []
        seen_paths = set()
        for base, kind in ((self.movies_dir, 'movie'), (self.server_root / 'Filmes', 'movie'), (self.series_dir, 'episode')):
            if not base.is_dir():
                continue
            for path in base.rglob('*'):
                if not path.is_file() or path.suffix.lower() not in VIDEO_EXTENSIONS:
                    continue
                resolved = path.resolve()
                if resolved in seen_paths:
                    continue
                seen_paths.add(resolved)
                discovered.append(self._parse_entry(resolved, kind))

        with self._scan_lock, self._connect() as conn:
            existing = {row['rel_path']: dict(row) for row in conn.execute('SELECT * FROM media_items')}
            found_rel = {item['rel_path'] for item in discovered}
            for stale_rel in set(existing) - found_rel:
                conn.execute('DELETE FROM media_items WHERE rel_path = ?', (stale_rel,))
            for item in discovered:
                prev = existing.get(item['rel_path'])
                if prev and prev['mtime'] == item['mtime'] and prev['size_bytes'] == item['size_bytes']:
                    if reenrich_missing and not prev.get('imdb_id'):
                        conn.execute(
                            'UPDATE media_items SET title = ?, year = ?, enriched = 0 WHERE id = ?',
                            (item['title'], item['year'], prev['id']),
                        )
                    continue
                conn.execute('''
                    INSERT INTO media_items (id, kind, series_key, series_title, title, year, season, episode, rel_path, filename, size_bytes, mtime, enriched)
                    VALUES (:id, :kind, :series_key, :series_title, :title, :year, :season, :episode, :rel_path, :filename, :size_bytes, :mtime, 0)
                    ON CONFLICT(id) DO UPDATE SET
                        kind=excluded.kind, series_key=excluded.series_key, series_title=excluded.series_title,
                        title=excluded.title, year=excluded.year, season=excluded.season, episode=excluded.episode,
                        rel_path=excluded.rel_path, filename=excluded.filename, size_bytes=excluded.size_bytes,
                        mtime=excluded.mtime, enriched=0
                ''', item)
            if reenrich_missing:
                conn.execute("UPDATE media_items SET enriched = 0 WHERE imdb_id = '' OR poster_file = '' OR substr(poster_file, 1, 6) = 'thumb_'")
            conn.execute('DELETE FROM watch_progress WHERE id NOT IN (SELECT id FROM media_items)')
            conn.commit()
        if async_enrich:
            self.trigger_enrichment()
        return len(discovered)

    def trigger_enrichment(self):
        with self._enrich_lock:
            if self._enrich_running:
                return
            self._enrich_running = True
        threading.Thread(target=self._enrich_worker, daemon=True).start()

    def _probe_video(self, path):
        try:
            res = run_managed(
                [
                    self.ffprobe_bin, '-v', 'error',
                    '-show_entries', 'format=duration,bit_rate,size:stream=codec_name,codec_type,profile,pix_fmt,color_transfer,width,height,r_frame_rate,channels:stream_tags=language,title',
                    '-of', 'json', str(path),
                ],
                stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=20, creationflags=NO_WINDOW,
            )
            if res.returncode != 0:
                return {}
            data = json.loads(res.stdout)
            fmt = data.get('format') or {}
            streams = data.get('streams') or []
            video = next((s for s in streams if s.get('codec_type') == 'video'), {})
            duration = float(fmt.get('duration', 0) or 0)
            duration = duration if duration > 0 else 0.0
            try:
                file_size = int(fmt.get('size') or Path(path).stat().st_size)
            except (ValueError, TypeError, OSError):
                file_size = 0
            try:
                bitrate_bps = int(fmt.get('bit_rate') or 0)
            except (ValueError, TypeError):
                bitrate_bps = 0
            if bitrate_bps <= 0 and duration > 0 and file_size > 0:
                bitrate_bps = int((file_size * 8) / duration)

            fps_str = ''
            rate = str(video.get('r_frame_rate') or '')
            if '/' in rate:
                num, den = rate.split('/', 1)
                try:
                    n_val, d_val = float(num), float(den)
                    if d_val > 0 and n_val > 0:
                        fps_val = n_val / d_val
                        if 1 <= fps_val <= 240:
                            fps_str = f'{round(fps_val)} fps' if abs(fps_val - round(fps_val)) < 0.015 else f'{fps_val:.2f} fps'
                except ValueError:
                    pass

            profile_parts = []
            if video.get('codec_name'):
                profile_parts.append(str(video['codec_name']).upper())
            if video.get('profile'):
                profile_parts.append(str(video['profile']))
            pix = str(video.get('pix_fmt') or '').lower()
            if '10le' in pix or '10be' in pix:
                profile_parts.append('10-bit')
            elif '12le' in pix:
                profile_parts.append('12-bit')
            transfer = str(video.get('color_transfer') or '').lower()
            if transfer in ('smpte2084', 'arib-std-b67'):
                profile_parts.append('HDR')

            audio_tracks = []
            for s in streams:
                if s.get('codec_type') != 'audio':
                    continue
                lang = str((s.get('tags') or {}).get('language') or 'und').upper()
                codec = str(s.get('codec_name') or '').upper()
                ch = s.get('channels')
                ch_txt = f'{ch}ch' if ch else ''
                audio_tracks.append(' '.join(p for p in (lang, codec, ch_txt) if p))

            sub_langs = []
            for s in streams:
                if s.get('codec_type') != 'subtitle':
                    continue
                lang = str((s.get('tags') or {}).get('language') or 'und').upper()
                if lang not in sub_langs:
                    sub_langs.append(lang)

            return {
                'duration': duration,
                'width': int(video.get('width') or 0),
                'height': int(video.get('height') or 0),
                'video_codec': str(video.get('codec_name') or ''),
                'bitrate_bps': bitrate_bps,
                'fps': fps_str,
                'video_profile': ' · '.join(profile_parts[:4]),
                'audio_summary': ' · '.join(audio_tracks[:5]),
                'subtitle_summary': ', '.join(sub_langs[:8]),
            }
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return {}

    def _extract_still(self, video_path, duration, filename):
        target = self.posters_dir / filename
        if target.is_file() and target.stat().st_size > 0:
            return filename
        seek = max(1.0, min(duration * 0.15, 180.0)) if duration > 4 else 0.5
        try:
            run_managed(
                [self.ffmpeg_bin, '-nostdin', '-hide_banner', '-loglevel', 'error', '-ss', f'{seek:.2f}', '-i', str(video_path), '-frames:v', '1', '-vf', 'scale=640:-2', '-q:v', '4', '-y', str(target)],
                stdin=subprocess.DEVNULL, capture_output=True, timeout=20, creationflags=NO_WINDOW,
            )
            if target.is_file() and target.stat().st_size > 0:
                return filename
        except (OSError, subprocess.TimeoutExpired):
            pass
        return ''

    def _fetch_rotten_tomatoes(self, imdb_id='', title='', year=None, is_series=False):
        if not self.enable_online_metadata:
            return '', ''
        slug = re.sub(r'[^a-z0-9]+', '_', str(title or '').lower()).strip('_')
        if not slug:
            return '', ''
        year_str = str(year or '').strip()
        prefix = 'tv' if is_series else 'm'
        candidates = []
        if year_str.isdigit():
            candidates.append(f'{prefix}/{slug}_{year_str}')
        candidates.append(f'{prefix}/{slug}')
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36'}
        for path in candidates:
            try:
                html_text = _http_get_bytes(f'https://www.rottentomatoes.com/{path}', timeout=7, limit=2 * 1024 * 1024, headers=headers).decode('utf-8', errors='ignore')
                if year_str.isdigit() and year_str not in html_text[:90000] and str(int(year_str) + 1) not in html_text[:90000]:
                    continue
                crit_m = re.search(r'"criticsScore":\{[^}]*"scorePercent":"(\d+%)"', html_text)
                if not crit_m:
                    crit_m = re.search(r'"scorePercent":"(\d+%)"[^}]*"title":"Tomatometer"', html_text)
                aud_m = re.search(r'"audienceScore":\{[^}]*"scorePercent":"(\d+%)"', html_text)
                if not aud_m:
                    aud_m = re.search(r'"scorePercent":"(\d+%)"[^}]*"title":"Popcornmeter"', html_text)
                if crit_m or aud_m:
                    return (crit_m.group(1) if crit_m else ''), (aud_m.group(1) if aud_m else '')
            except (OSError, ValueError):
                continue
        return '', ''

    def _fetch_online_meta(self, query, kind, year=None, cache_key='item', imdb_id=''):
        if not self.enable_online_metadata or (not query and not imdb_id):
            return {}
        info = {
            'imdb_id': '', 'title': '', 'poster_file': '', 'overview': '', 'cast_text': '',
            'rating': None, 'rt_critics': '', 'rt_audience': '', 'genres_text': '', 'director_text': '',
        }
        clean_q = re.sub(r'\[[^\]]*\]', ' ', str(query or ''))
        clean_q = re.sub(r'[\[\]]', ' ', clean_q)
        clean_q = re.sub(r'\s+', ' ', clean_q).strip()
        known_imdb = str(imdb_id or '').strip()
        if re.fullmatch(r'tt\d{7,10}', known_imdb):
            info['imdb_id'] = known_imdb
        elif clean_q:
            first = next((c.lower() for c in clean_q if c.isalnum()), 'x')
            try:
                data = _http_get_json(f'https://v3.sg.media-imdb.com/suggestion/{first}/{quote(clean_q)}.json')
                allowed_qids = {'movie', 'tvMovie'} if kind == 'movie' else {'tvSeries', 'tvMiniSeries'}
                candidates = [d for d in data.get('d', []) if str(d.get('id', '')).startswith('tt') and d.get('qid') in allowed_qids]
                if not candidates:
                    candidates = [d for d in data.get('d', []) if str(d.get('id', '')).startswith('tt')]
                if year and candidates:
                    candidates.sort(key=lambda d: abs(int(d.get('y') or year) - int(year)))
                if candidates:
                    best = candidates[0]
                    info['imdb_id'] = str(best.get('id') or '')
                    info['title'] = str(best.get('l') or '')
                    info['cast_text'] = str(best.get('s') or '')
                    img_url = str((best.get('i') or {}).get('imageUrl') or '')
                    if img_url.startswith('https://'):
                        sized = re.sub(r'\._V1_.*\.jpg$', '._V1_UX600_.jpg', img_url)
                        try:
                            img_bytes = _http_get_bytes(sized, timeout=10)
                            poster_name = f'{cache_key}.jpg'
                            (self.posters_dir / poster_name).write_bytes(img_bytes)
                            info['poster_file'] = poster_name
                        except (OSError, ValueError):
                            pass
            except (OSError, ValueError):
                pass

        if kind == 'episode':
            try:
                tv = _http_get_json(f'https://api.tvmaze.com/singlesearch/shows?q={quote(clean_q)}')
                info['overview'] = _clean_html(tv.get('summary') or '')[:420]
                rating_val = (tv.get('rating') or {}).get('average')
                if isinstance(rating_val, (int, float)):
                    info['rating'] = float(rating_val)
                if not info['poster_file']:
                    tv_img = str((tv.get('image') or {}).get('medium') or (tv.get('image') or {}).get('original') or '')
                    if tv_img.startswith('https://'):
                        try:
                            poster_name = f'{cache_key}.jpg'
                            (self.posters_dir / poster_name).write_bytes(_http_get_bytes(tv_img, timeout=10))
                            info['poster_file'] = poster_name
                        except (OSError, ValueError):
                            pass
            except (OSError, ValueError):
                pass

        if info['imdb_id']:
            c_kind = 'series' if kind == 'episode' else 'movie'
            try:
                c_meta = _http_get_json(f'https://v3-cinemeta.strem.io/meta/{c_kind}/{info["imdb_id"]}.json').get('meta') or {}
                if not info.get('title') and c_meta.get('name'):
                    info['title'] = str(c_meta['name'])
                if not info['overview'] and c_meta.get('description'):
                    info['overview'] = str(c_meta['description'])[:420]
                if info['rating'] is None and c_meta.get('imdbRating'):
                    try:
                        info['rating'] = float(c_meta['imdbRating'])
                    except (ValueError, TypeError):
                        pass
                if not info['cast_text'] and c_meta.get('cast'):
                    info['cast_text'] = ', '.join(str(c) for c in c_meta['cast'][:5])
                if c_meta.get('genres'):
                    info['genres_text'] = ', '.join(str(g) for g in c_meta['genres'][:4])
                if c_meta.get('director'):
                    info['director_text'] = ', '.join(str(d) for d in c_meta['director'][:3])
                if not info['poster_file'] and str(c_meta.get('poster') or '').startswith('https://'):
                    try:
                        poster_name = f'{cache_key}.jpg'
                        (self.posters_dir / poster_name).write_bytes(_http_get_bytes(c_meta['poster'], timeout=10))
                        info['poster_file'] = poster_name
                    except (OSError, ValueError):
                        pass
            except (OSError, ValueError):
                pass
            rt_c, rt_a = self._fetch_rotten_tomatoes(info['imdb_id'], query, year, kind == 'episode')
            info['rt_critics'] = rt_c
            info['rt_audience'] = rt_a
        return info

    def _enrich_worker(self):
        try:
            series_cache = {}
            while True:
                with self._connect() as conn:
                    rows = [dict(r) for r in conn.execute('SELECT * FROM media_items WHERE enriched = 0 LIMIT 12')]
                if not rows:
                    break
                for row in rows:
                    video_path = (self.server_root / row['rel_path']).resolve()
                    if not video_path.is_file():
                        with self._connect() as conn:
                            conn.execute('UPDATE media_items SET enriched = 1 WHERE id = ?', (row['id'],))
                            conn.commit()
                        continue
                    probe = self._probe_video(video_path)
                    duration = probe.get('duration', 0.0)
                    thumb_name = self._extract_still(video_path, duration, f"thumb_{row['id']}.jpg")

                    if row['kind'] == 'episode' and row['series_key']:
                        if row['series_key'] not in series_cache:
                            with self._connect() as conn:
                                existing_s = conn.execute(
                                    "SELECT imdb_id, poster_file, overview, cast_text, rating, rt_critics, rt_audience, genres_text, director_text FROM media_items WHERE series_key = ? AND poster_file != '' AND substr(poster_file, 1, 6) != 'thumb_' LIMIT 1",
                                    (row['series_key'],),
                                ).fetchone()
                            if existing_s:
                                series_cache[row['series_key']] = dict(existing_s)
                            else:
                                series_cache[row['series_key']] = self._fetch_online_meta(
                                    row['series_title'] or row['title'], 'episode', row['year'], f"series_{row['series_key']}", imdb_id=row.get('imdb_id', '')
                                )
                        meta = series_cache[row['series_key']]
                    else:
                        meta = self._fetch_online_meta(row['title'], 'movie', row['year'], f"movie_{row['id']}", imdb_id=row.get('imdb_id', ''))

                    poster_file = meta.get('poster_file') or thumb_name
                    backdrop_file = thumb_name or poster_file
                    final_imdb = meta.get('imdb_id') or row.get('imdb_id') or ''
                    final_title = (meta.get('title') or row['title']) if row['kind'] == 'movie' else row['title']
                    with self._connect() as conn:
                        conn.execute('''
                            UPDATE media_items SET
                                title = ?, duration = ?, width = ?, height = ?, video_codec = ?,
                                bitrate_bps = ?, fps = ?, video_profile = ?,
                                audio_summary = ?, subtitle_summary = ?,
                                imdb_id = ?, poster_file = ?, backdrop_file = ?,
                                overview = ?, cast_text = ?, rating = ?,
                                rt_critics = ?, rt_audience = ?, genres_text = ?, director_text = ?,
                                enriched = 1
                            WHERE id = ?
                        ''', (
                            final_title, duration, probe.get('width', 0), probe.get('height', 0), probe.get('video_codec', ''),
                            probe.get('bitrate_bps', 0), probe.get('fps', ''), probe.get('video_profile', ''),
                            probe.get('audio_summary', ''), probe.get('subtitle_summary', ''),
                            final_imdb, poster_file, backdrop_file,
                            meta.get('overview', ''), meta.get('cast_text', ''), meta.get('rating'),
                            meta.get('rt_critics', ''), meta.get('rt_audience', ''),
                            meta.get('genres_text', ''), meta.get('director_text', ''),
                            row['id'],
                        ))
                        conn.commit()
        finally:
            with self._enrich_lock:
                self._enrich_running = False

    def list_sidecars(self, video_path):
        resolved = Path(video_path).resolve()
        if not resolved.is_file():
            return []
        stem_lower = resolved.stem.lower()
        subs = []
        for sibling in sorted(resolved.parent.iterdir()):
            if not sibling.is_file() or sibling.suffix.lower() not in SUBTITLE_EXTENSIONS:
                continue
            sib_stem = sibling.stem.lower()
            if sib_stem == stem_lower or sib_stem.startswith(stem_lower + '.'):
                sub_rel = sibling.resolve().relative_to(self.server_root).as_posix()
                sub_id = hashlib.sha256(sub_rel.encode('utf-8')).hexdigest()[:16]
                subs.append({'id': sub_id, 'label': sibling.name, 'path': str(sibling.resolve())})
        return subs

    def _serialize_item(self, row, progress_map):
        prog = progress_map.get(row['id'])
        pos = float(prog['position']) if prog else 0.0
        dur = float(row['duration'] or (prog['duration'] if prog else 0.0) or 0.0)
        ratio = round(min(1.0, max(0.0, pos / dur)), 4) if dur > 0 and pos > 0 else 0.0
        video_path = self.server_root / row['rel_path']
        sidecars = [{'id': s['id'], 'label': s['label']} for s in self.list_sidecars(video_path)]
        return {
            'id': row['id'],
            'kind': row['kind'],
            'series_key': row['series_key'],
            'series_title': row['series_title'],
            'title': row['title'],
            'year': row['year'],
            'season': row['season'],
            'episode': row['episode'],
            'filename': row['filename'],
            'size_mb': round((row['size_bytes'] or 0) / 1048576, 1),
            'mtime': row['mtime'],
            'duration': dur,
            'position': pos,
            'progress': ratio,
            'width': row['width'],
            'height': row['height'],
            'video_codec': row['video_codec'],
            'imdb_id': row['imdb_id'],
            'poster_url': f"/api/poster/{row['poster_file']}" if row['poster_file'] else '',
            'backdrop_url': f"/api/poster/{row['backdrop_file']}" if row['backdrop_file'] else '',
            'overview': row['overview'],
            'cast_text': row['cast_text'],
            'rating': row['rating'],
            'subtitles': sidecars,
        }

    def get_home_catalog(self):
        with self._connect() as conn:
            rows = [dict(r) for r in conn.execute('SELECT * FROM media_items ORDER BY mtime DESC, title COLLATE NOCASE ASC')]
            prog_rows = {r['id']: dict(r) for r in conn.execute('SELECT * FROM watch_progress')}

        items = [self._serialize_item(r, prog_rows) for r in rows]
        movies = [i for i in items if i['kind'] == 'movie']

        series_map = {}
        for ep in items:
            if ep['kind'] != 'episode':
                continue
            key = ep['series_key'] or ep['id']
            grp = series_map.get(key)
            if not grp:
                grp = {
                    'series_key': key,
                    'title': ep['series_title'] or ep['title'],
                    'year': ep['year'],
                    'imdb_id': ep['imdb_id'],
                    'poster_url': ep['poster_url'],
                    'backdrop_url': ep['backdrop_url'],
                    'overview': ep['overview'],
                    'cast_text': ep['cast_text'],
                    'rating': ep['rating'],
                    'seasons': set(),
                    'episode_count': 0,
                    'latest_mtime': ep['mtime'],
                    'first_episode_id': ep['id'],
                    'episodes': [],
                }
                series_map[key] = grp
            grp['episode_count'] += 1
            grp['seasons'].add(ep['season'] or 1)
            grp['latest_mtime'] = max(grp['latest_mtime'], ep['mtime'])
            if ep['poster_url'] and (not grp['poster_url'] or '/thumb_' in grp['poster_url'] and '/thumb_' not in ep['poster_url']):
                grp['poster_url'] = ep['poster_url']
            if not grp['backdrop_url'] and ep['backdrop_url']:
                grp['backdrop_url'] = ep['backdrop_url']
            if not grp['overview'] and ep['overview']:
                grp['overview'] = ep['overview']
            grp['episodes'].append(ep)

        series_list = []
        for grp in series_map.values():
            eps_sorted = sorted(grp['episodes'], key=lambda e: (e['season'] or 0, e['episode'] or 0, e['filename']))
            series_list.append({
                'series_key': grp['series_key'],
                'title': grp['title'],
                'year': grp['year'],
                'imdb_id': grp['imdb_id'],
                'poster_url': grp['poster_url'],
                'backdrop_url': grp['backdrop_url'],
                'overview': grp['overview'],
                'cast_text': grp['cast_text'],
                'rating': grp['rating'],
                'season_count': len(grp['seasons']),
                'episode_count': grp['episode_count'],
                'latest_mtime': grp['latest_mtime'],
                'first_episode_id': eps_sorted[0]['id'] if eps_sorted else grp['first_episode_id'],
            })
        series_list.sort(key=lambda s: (-s['latest_mtime'], s['title'].lower()))

        continue_watching = [
            i for i in sorted(items, key=lambda x: prog_rows.get(x['id'], {}).get('updated_at', 0), reverse=True)
            if i['position'] >= 3 and i['progress'] <= 0.94
        ][:12]

        featured = None
        if continue_watching:
            featured = continue_watching[0]
        elif movies:
            featured = movies[0]
        elif series_list:
            featured = {**series_list[0], 'kind': 'series', 'id': series_list[0]['first_episode_id']}

        return {
            'media_root': str(self.server_root),
            'library_paths': {'movies': str(self.movies_dir), 'series': str(self.series_dir)},
            'featured': featured,
            'hero_items': movies + [{**show, 'kind': 'series', 'id': show['first_episode_id']} for show in series_list],
            'continue_watching': continue_watching,
            'movies': movies,
            'series': series_list,
            'stats': {'movies': len(movies), 'series': len(series_list), 'episodes': sum(s['episode_count'] for s in series_list)},
        }

    def get_series_detail(self, series_key):
        with self._connect() as conn:
            rows = [dict(r) for r in conn.execute(
                'SELECT * FROM media_items WHERE kind = ? AND series_key = ? ORDER BY season ASC, episode ASC, filename ASC',
                ('episode', str(series_key or '')),
            )]
            prog_rows = {r['id']: dict(r) for r in conn.execute('SELECT * FROM watch_progress')}
        if not rows:
            raise ValueError('Series not found.')
        episodes = [self._serialize_item(r, prog_rows) for r in rows]
        seasons_map = {}
        for ep in episodes:
            s_num = ep['season'] or 1
            seasons_map.setdefault(s_num, []).append(ep)
        first = episodes[0]
        return {
            'series_key': first['series_key'],
            'title': first['series_title'] or first['title'],
            'year': first['year'],
            'imdb_id': first['imdb_id'],
            'poster_url': next((e['poster_url'] for e in episodes if e['poster_url']), ''),
            'backdrop_url': next((e['backdrop_url'] for e in episodes if e['backdrop_url']), ''),
            'overview': next((e['overview'] for e in episodes if e['overview']), ''),
            'cast_text': next((e['cast_text'] for e in episodes if e['cast_text']), ''),
            'rating': next((e['rating'] for e in episodes if e['rating'] is not None), None),
            'seasons': [{'season': s, 'episodes': seasons_map[s]} for s in sorted(seasons_map)],
        }

    def get_item_record(self, item_id):
        with self._connect() as conn:
            row = conn.execute('SELECT * FROM media_items WHERE id = ?', (str(item_id or ''),)).fetchone()
            if not row:
                raise ValueError('Library video unavailable.')
            prog = conn.execute('SELECT * FROM watch_progress WHERE id = ?', (row['id'],)).fetchone()
            item = dict(row)
            video_path = (self.server_root / item['rel_path']).resolve()
            if not video_path.is_file():
                raise ValueError('Library video unavailable.')
            next_episode_id = ''
            if item['kind'] == 'episode' and item['series_key']:
                sibs = [dict(r) for r in conn.execute(
                    'SELECT id FROM media_items WHERE kind = ? AND series_key = ? ORDER BY season ASC, episode ASC, filename ASC',
                    ('episode', item['series_key']),
                )]
                ids = [s['id'] for s in sibs]
                if item['id'] in ids:
                    idx = ids.index(item['id'])
                    if idx + 1 < len(ids):
                        next_episode_id = ids[idx + 1]
        return {
            **item,
            'full_path': str(video_path),
            'position': float(prog['position']) if prog else 0.0,
            'sidecars': self.list_sidecars(video_path),
            'next_episode_id': next_episode_id,
        }

    def resolve_sidecar_path(self, sub_id):
        with self._connect() as conn:
            rows = conn.execute('SELECT rel_path FROM media_items').fetchall()
        for row in rows:
            video_path = self.server_root / row['rel_path']
            for sub in self.list_sidecars(video_path):
                if sub['id'] == sub_id:
                    return Path(sub['path'])
        raise ValueError('Subtitle file unavailable.')

    def save_progress(self, item_id, position, duration):
        pos = max(0.0, float(position or 0.0))
        dur = max(0.0, float(duration or 0.0))
        with self._connect() as conn:
            exists = conn.execute('SELECT 1 FROM media_items WHERE id = ?', (str(item_id or ''),)).fetchone()
            if not exists:
                raise ValueError('Library video unavailable.')
            conn.execute('''
                INSERT INTO watch_progress (id, position, duration, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET position=excluded.position, duration=excluded.duration, updated_at=excluded.updated_at
            ''', (item_id, pos, dur, int(time.time())))
            conn.commit()
        return {'ok': True}

    def poster_path(self, filename):
        safe = Path(str(filename or '')).name
        if not safe or safe != str(filename):
            raise ValueError('Invalid poster name.')
        target = (self.posters_dir / safe).resolve()
        if not target.is_relative_to(self.posters_dir) or not target.is_file():
            raise ValueError('Poster not found.')
        return target

    def all_playable_entries(self):
        with self._connect() as conn:
            rows = [dict(r) for r in conn.execute('SELECT id, kind, series_title, title, season, episode, rel_path FROM media_items ORDER BY kind, series_title, season, episode, title')]
        return rows

    def find_local_by_imdb(self, imdb_id):
        if not imdb_id:
            return []
        with self._connect() as conn:
            rows = [dict(r) for r in conn.execute(
                'SELECT id, kind, series_key, series_title, title, season, episode, filename FROM media_items WHERE imdb_id = ? ORDER BY season ASC, episode ASC',
                (str(imdb_id),),
            )]
        return rows

    def assign_imdb_id(self, file_path, imdb_id):
        if not imdb_id or not re.fullmatch(r'tt\d{7,10}', str(imdb_id)):
            return ''
        try:
            rel_posix = Path(file_path).resolve().relative_to(self.server_root).as_posix()
        except ValueError:
            return ''
        with self._scan_lock, self._connect() as conn:
            conn.execute('UPDATE media_items SET imdb_id = ? WHERE rel_path = ?', (str(imdb_id), rel_posix))
            conn.commit()
            row = conn.execute('SELECT id FROM media_items WHERE rel_path = ?', (rel_posix,)).fetchone()
            return row['id'] if row else ''

    def find_item_id_by_rel_path(self, rel_path):
        if not rel_path:
            return ''
        with self._connect() as conn:
            row = conn.execute('SELECT id FROM media_items WHERE rel_path = ?', (str(rel_path),)).fetchone()
            return row['id'] if row else ''

    def get_media_details(self, item_id='', series_key=''):
        with self._connect() as conn:
            if series_key:
                rows = [dict(r) for r in conn.execute(
                    'SELECT * FROM media_items WHERE kind = ? AND series_key = ? ORDER BY season ASC, episode ASC, filename ASC',
                    ('episode', str(series_key)),
                )]
                if not rows:
                    raise ValueError('Series not found.')
                row = rows[0]
                is_series = True
                total_size = sum(int(r['size_bytes'] or 0) for r in rows)
                season_count = len({r['season'] or 1 for r in rows})
                episode_count = len(rows)
            else:
                r = conn.execute('SELECT * FROM media_items WHERE id = ?', (str(item_id or ''),)).fetchone()
                if not r:
                    raise ValueError('Library item not found.')
                row = dict(r)
                is_series = row['kind'] == 'episode' and bool(row['series_key'])
                total_size = int(row['size_bytes'] or 0)
                season_count = 1
                episode_count = 1

        video_path = (self.server_root / row['rel_path']).resolve()
        updates = {}
        if not int(row.get('bitrate_bps') or 0) and video_path.is_file():
            probe = self._probe_video(video_path)
            for k in ('duration', 'width', 'height', 'video_codec', 'bitrate_bps', 'fps', 'video_profile', 'audio_summary', 'subtitle_summary'):
                if probe.get(k):
                    row[k] = probe[k]
                    updates[k] = probe[k]

        imdb_id = str(row.get('imdb_id') or '')
        if self.enable_online_metadata and imdb_id:
            c_kind = 'series' if is_series else 'movie'
            if not row.get('genres_text') or not row.get('director_text') or not row.get('overview'):
                try:
                    c_meta = _http_get_json(f'https://v3-cinemeta.strem.io/meta/{c_kind}/{imdb_id}.json', timeout=6).get('meta') or {}
                    if not row.get('genres_text') and c_meta.get('genres'):
                        row['genres_text'] = updates['genres_text'] = ', '.join(str(g) for g in c_meta['genres'][:4])
                    if not row.get('director_text') and c_meta.get('director'):
                        row['director_text'] = updates['director_text'] = ', '.join(str(d) for d in c_meta['director'][:3])
                    if not row.get('overview') and c_meta.get('description'):
                        row['overview'] = updates['overview'] = str(c_meta['description'])[:420]
                    if row.get('rating') is None and c_meta.get('imdbRating'):
                        try:
                            row['rating'] = updates['rating'] = float(c_meta['imdbRating'])
                        except (ValueError, TypeError):
                            pass
                except (OSError, ValueError):
                    pass
            if not row.get('rt_critics') and not row.get('rt_audience'):
                rt_title = row.get('series_title') if is_series and row.get('series_title') else row.get('title')
                rt_c, rt_a = self._fetch_rotten_tomatoes(imdb_id, rt_title, row.get('year'), is_series)
                if rt_c or rt_a:
                    row['rt_critics'] = updates['rt_critics'] = rt_c
                    row['rt_audience'] = updates['rt_audience'] = rt_a

        if updates:
            with self._scan_lock, self._connect() as conn:
                cols = ', '.join(f'{k} = ?' for k in updates)
                vals = list(updates.values()) + [row['id']]
                conn.execute(f'UPDATE media_items SET {cols} WHERE id = ?', vals)
                conn.commit()

        sidecars = self.list_sidecars(video_path) if video_path.is_file() else []
        return {
            'id': row['id'],
            'kind': 'series' if is_series and series_key else row['kind'],
            'series_key': row['series_key'],
            'title': row['series_title'] if (is_series and row['series_title']) else row['title'],
            'year': row['year'],
            'imdb_id': imdb_id,
            'imdb_rating': row['rating'],
            'rt_critics': row.get('rt_critics') or '',
            'rt_audience': row.get('rt_audience') or '',
            'duration': float(row.get('duration') or 0.0),
            'width': int(row.get('width') or 0),
            'height': int(row.get('height') or 0),
            'resolution_label': _resolution_label(row.get('width'), row.get('height')),
            'bitrate_bps': int(row.get('bitrate_bps') or 0),
            'bitrate_label': _format_bitrate(row.get('bitrate_bps')),
            'size_bytes': total_size,
            'size_label': _format_size_bytes(total_size),
            'video_codec': row.get('video_codec') or '',
            'video_profile': row.get('video_profile') or '',
            'fps': row.get('fps') or '',
            'audio_summary': row.get('audio_summary') or '',
            'subtitle_summary': row.get('subtitle_summary') or '',
            'sidecar_count': len(sidecars),
            'overview': row.get('overview') or '',
            'cast_text': row.get('cast_text') or '',
            'genres': row.get('genres_text') or '',
            'director': row.get('director_text') or '',
            'poster_url': f"/api/poster/{row['poster_file']}" if row.get('poster_file') else '',
            'backdrop_url': f"/api/poster/{row['backdrop_file']}" if row.get('backdrop_file') else '',
            'season_count': season_count,
            'episode_count': episode_count,
        }
