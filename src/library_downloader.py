"""Direct HTTPS/Debrid library downloader into Movies and Series folders."""
from concurrent.futures import ThreadPoolExecutor
import http.client
from pathlib import Path
import re
import secrets
import socket
import ssl
import threading
import time
import urllib.error
from urllib.parse import unquote, urlparse
import urllib.request

from stremio_imdb import USER_AGENT, get_addon_stream, urlopen_resilient

VIDEO_EXTENSIONS = {'.mp4', '.mkv', '.avi', '.mov', '.m4v', '.webm', '.ts', '.m2ts', '.mpg', '.mpeg', '.wmv'}
_INVALID_WIN_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED_WIN_NAMES = {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))}
_SEASON_EP_RE = re.compile(r'(?:S\d{1,2}E\d{1,3}|\b\d{1,2}x\d{1,3}\b)', re.I)
CHUNK_BYTES = 256 * 1024
MAX_STALL_RETRIES = 15


def _safe_win_name(text, fallback='Video', max_len=110):
    cleaned = _INVALID_WIN_CHARS.sub(' ', str(text or ''))
    cleaned = re.sub(r'\s+', ' ', cleaned).strip().rstrip('. ')
    if not cleaned or cleaned in ('.', '..') or cleaned.upper() in _RESERVED_WIN_NAMES:
        return fallback
    return cleaned[:max_len].rstrip('. ') or fallback


def _parse_total_from_headers(headers, status, appended_offset):
    cr = headers.get('Content-Range', '')
    if status == 206 and '/' in cr:
        tail = cr.rsplit('/', 1)[1].strip()
        if tail.isdigit():
            return int(tail)
    cl = headers.get('Content-Length', '')
    if str(cl).isdigit():
        return int(cl) + (appended_offset if status == 206 else 0)
    return 0


def _open_media_stream(req, timeout=30):
    try:
        return urlopen_resilient(req, timeout=timeout)
    except urllib.error.URLError as err:
        # Some Debrid CDN storage nodes serve expired chain timestamps; keep CA + hostname checks strict.
        if isinstance(err.reason, ssl.SSLCertVerificationError) and getattr(err.reason, 'verify_code', None) == 10:
            ctx = ssl.create_default_context()
            ctx.verify_flags |= getattr(ssl, 'X509_V_FLAG_NO_CHECK_TIME', 0x200000)
            return urllib.request.urlopen(req, timeout=timeout, context=ctx)
        raise


class LibraryDownloadService:
    """Downloads direct HTTPS/Debrid streams straight into the Movies or Series library folder."""

    def __init__(self, server_root, on_complete=None, max_workers=2):
        self.server_root = Path(server_root).resolve()
        self.on_complete = on_complete
        self._executor = ThreadPoolExecutor(max_workers=max_workers)
        self._lock = threading.RLock()
        self._jobs = {}
        self._closing = False

    def _build_target_path(self, kind, title, year, season, episode, episode_title, filename, url):
        raw_name = Path(str(filename or '')).name or Path(unquote(urlparse(url).path)).name or 'Video.mkv'
        ext = Path(raw_name).suffix.lower()
        if ext not in VIDEO_EXTENSIONS:
            url_ext = Path(unquote(urlparse(url).path)).suffix.lower()
            ext = url_ext if url_ext in VIDEO_EXTENSIONS else '.mkv'

        raw_stem = Path(raw_name).stem
        safe_title = _safe_win_name(title, fallback='Untitled', max_len=90)
        year_match = re.search(r'(19|20)\d{2}', str(year or ''))
        year_str = year_match.group(0) if year_match else ''

        if kind in ('series', 'episode'):
            s_num = max(1, int(season or 1))
            e_num = max(1, int(episode or 1))
            series_folder = f'{safe_title} ({year_str})' if year_str else safe_title
            folder = self.server_root / 'Series' / _safe_win_name(series_folder, fallback=safe_title, max_len=100) / f'Season {s_num:02d}'
            if _SEASON_EP_RE.search(raw_stem):
                stem = _safe_win_name(raw_stem, fallback=f'{safe_title} - S{s_num:02d}E{e_num:02d}', max_len=130)
            else:
                ep_tail = _safe_win_name(episode_title or raw_stem, fallback='', max_len=60)
                base_stem = f'{safe_title} - S{s_num:02d}E{e_num:02d}'
                stem = f'{base_stem} - {ep_tail}' if ep_tail else base_stem
            subtitle = f'S{s_num:02d}E{e_num:02d}'
        else:
            folder_name = f'{safe_title} ({year_str})' if year_str else safe_title
            folder = self.server_root / 'Movies' / _safe_win_name(folder_name, fallback=safe_title, max_len=100)
            stem = _safe_win_name(raw_stem, fallback=folder_name, max_len=130)
            subtitle = year_str or 'Movie'

        target = (folder / f'{stem}{ext}').resolve()
        if not target.is_relative_to(self.server_root):
            raise ValueError('Invalid download destination path.')
        return target, subtitle

    def _serialize(self, job, catalog_lookup=None):
        item_id = ''
        if job['state'] == 'complete' and callable(catalog_lookup):
            try:
                item_id = catalog_lookup(job['rel_path']) or ''
            except Exception:
                item_id = ''
        return {
            'id': job['id'],
            'kind': job['kind'],
            'title': job['title'],
            'subtitle': job['subtitle'],
            'imdb_id': job['imdb_id'],
            'season': job['season'],
            'episode': job['episode'],
            'filename': job['filename'],
            'rel_path': job['rel_path'],
            'state': job['state'],
            'downloaded': job['downloaded'],
            'total': job['total'],
            'speed': job['speed'],
            'eta': job['eta'],
            'error': job['error'],
            'catalog_item_id': item_id,
            'stream_token': job['stream_token'],
        }

    def enqueue_from_token(self, data):
        with self._lock:
            if self._closing:
                raise ValueError('The download service is shutting down.')
        stream_token = str(data.get('stream_token') or '').strip()
        stream = get_addon_stream(stream_token)
        url = str(stream.get('url') or '').strip()
        if not urlparse(url).scheme in ('http', 'https'):
            raise ValueError('Only HTTP/HTTPS streams can be downloaded directly.')

        kind = str(data.get('kind') or stream.get('kind') or 'movie').strip().lower()
        if kind not in ('movie', 'series', 'episode'):
            kind = 'movie'
        title = str(data.get('title') or stream.get('name') or 'Video').strip()
        year = str(data.get('year') or '')
        season = data.get('season') if data.get('season') is not None else stream.get('season')
        episode = data.get('episode') if data.get('episode') is not None else stream.get('episode')
        episode_title = str(data.get('episode_title') or '')
        filename = str(data.get('filename') or stream.get('filename') or '')
        imdb_id = str(data.get('imdb_id') or stream.get('imdb_id') or '').strip()

        target_path, subtitle = self._build_target_path(kind, title, year, season, episode, episode_title, filename, url)
        rel_path = target_path.relative_to(self.server_root).as_posix()

        with self._lock:
            if self._closing:
                raise ValueError('The download service is shutting down.')
            for existing in self._jobs.values():
                if existing['target_path'] == target_path and existing['state'] in ('queued', 'downloading'):
                    return self._serialize(existing)
            job_id = secrets.token_urlsafe(12)
            job = {
                'id': job_id,
                'stream_token': stream_token,
                'kind': 'series' if kind in ('series', 'episode') else 'movie',
                'title': title[:140],
                'subtitle': subtitle,
                'imdb_id': imdb_id,
                'season': int(season) if season is not None and str(season).isdigit() else None,
                'episode': int(episode) if episode is not None and str(episode).isdigit() else None,
                'filename': target_path.name,
                'rel_path': rel_path,
                'target_path': target_path,
                'url': url,
                'headers': dict(stream.get('headers') or {}),
                'state': 'queued',
                'downloaded': 0,
                'total': int(stream.get('video_size') or 0),
                'speed': 0,
                'eta': None,
                'error': '',
                'created_at': time.time(),
                'cancel_event': threading.Event(),
            }
            self._jobs[job_id] = job
            self._executor.submit(self._run_job, job)
            return self._serialize(job)

    def _run_job(self, job):
        target = job['target_path']
        part = target.with_name(target.name + '.part')
        cancel_ev = job['cancel_event']
        if cancel_ev.is_set():
            with self._lock:
                job['state'] = 'cancelled'
            return

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with self._lock:
                job['state'] = 'downloading'
                job['error'] = ''

            stalls = 0
            last_error = ''
            while not cancel_ev.is_set():
                resume_from = part.stat().st_size if part.is_file() else 0
                if job['total'] > 0 and resume_from >= job['total']:
                    break

                req_headers = {'User-Agent': USER_AGENT, **job['headers']}
                if resume_from > 0:
                    req_headers['Range'] = f'bytes={resume_from}-'
                req = urllib.request.Request(job['url'], headers=req_headers)

                progressed_bytes = 0
                try:
                    with _open_media_stream(req, timeout=15) as resp:
                        # Interrupt the socket, not BufferedReader.close(), which waits on its read lock.
                        active_socket = getattr(getattr(getattr(resp, 'fp', None), 'raw', None), '_sock', None)
                        with self._lock:
                            job['active_socket'] = active_socket
                            stopping = cancel_ev.is_set()
                        if stopping and active_socket is not None:
                            try:
                                active_socket.shutdown(socket.SHUT_RDWR)
                            except OSError:
                                pass
                        status = getattr(resp, 'status', 200)
                        append_mode = status == 206 and resume_from > 0
                        downloaded = resume_from if append_mode else 0
                        total = _parse_total_from_headers(resp.headers, status, downloaded) or job['total']
                        with self._lock:
                            job['downloaded'] = downloaded
                            job['total'] = total

                        win_start = time.monotonic()
                        win_bytes = 0
                        with part.open('ab' if append_mode else 'wb') as out:
                            while not cancel_ev.is_set():
                                chunk = resp.read(CHUNK_BYTES)
                                if not chunk:
                                    break
                                out.write(chunk)
                                n = len(chunk)
                                progressed_bytes += n
                                win_bytes += n
                                downloaded += n
                                now = time.monotonic()
                                elapsed = now - win_start
                                if elapsed >= 0.35:
                                    speed = int(win_bytes / elapsed)
                                    win_start = now
                                    win_bytes = 0
                                else:
                                    speed = int(win_bytes / max(0.05, elapsed))
                                eta = int((total - downloaded) / speed) if (total > downloaded and speed > 0) else None
                                with self._lock:
                                    job['downloaded'] = downloaded
                                    if total > 0:
                                        job['total'] = total
                                    job['speed'] = speed
                                    job['eta'] = eta

                        if cancel_ev.is_set():
                            break
                        if total <= 0 or downloaded >= total:
                            break
                        last_error = f'Connection closed at {downloaded}/{total} bytes.'
                except urllib.error.HTTPError as err:
                    if err.code in (416, 500, 502, 503, 504) and not cancel_ev.is_set():
                        last_error = f'HTTP {err.code}'
                    else:
                        raise
                except (urllib.error.URLError, http.client.HTTPException, TimeoutError, ssl.SSLError, OSError) as err:
                    if cancel_ev.is_set():
                        break
                    last_error = str(err)[:160] or 'Connection interrupted.'
                finally:
                    with self._lock:
                        job.pop('active_socket', None)

                if progressed_bytes >= CHUNK_BYTES:
                    stalls = 0
                else:
                    stalls += 1
                if stalls >= MAX_STALL_RETRIES:
                    raise ValueError(last_error or 'Download stalled after repeated retries.')
                with self._lock:
                    job.update(speed=0, eta=None)
                if cancel_ev.wait(min(5.0, 0.5 + stalls * 0.5)):
                    break

            if cancel_ev.is_set():
                try:
                    if not job.get('preserve_partial') and part.exists():
                        part.unlink()
                except OSError:
                    pass
                with self._lock:
                    job.update(state='cancelled', speed=0, eta=None)
                return

            part.replace(target)
            final_size = target.stat().st_size
            with self._lock:
                job.update(state='complete', downloaded=final_size, total=max(job['total'], final_size), speed=0, eta=0)
            if callable(self.on_complete):
                self.on_complete(dict(job))
        except urllib.error.HTTPError as err:
            with self._lock:
                job.update(state='cancelled' if cancel_ev.is_set() else 'failed', speed=0, eta=None, error=f'HTTP {err.code}')
        except (urllib.error.URLError, http.client.HTTPException, TimeoutError, ssl.SSLError, OSError, ValueError) as err:
            if cancel_ev.is_set():
                try:
                    if not job.get('preserve_partial') and part.exists():
                        part.unlink()
                except OSError:
                    pass
                with self._lock:
                    job.update(state='cancelled', speed=0, eta=None)
            else:
                with self._lock:
                    job.update(state='failed', speed=0, eta=None, error=str(err)[:160] or 'Download failed.')
        finally:
            with self._lock:
                job.pop('active_socket', None)

    def shutdown(self):
        """Stop downloads without waiting on network reads; keep resumable partial files."""
        with self._lock:
            if self._closing:
                return
            self._closing = True
            sockets = []
            for job in self._jobs.values():
                if job['state'] in ('queued', 'downloading'):
                    job['preserve_partial'] = not job['cancel_event'].is_set()
                    job['cancel_event'].set()
                    if job['state'] == 'queued':
                        job['state'] = 'cancelled'
                    if job.get('active_socket') is not None:
                        sockets.append(job['active_socket'])
        self._executor.shutdown(wait=False, cancel_futures=True)
        for connection in sockets:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def list_jobs(self, catalog_lookup=None):
        with self._lock:
            ordered = sorted(self._jobs.values(), key=lambda j: j['created_at'], reverse=True)
            return [self._serialize(j, catalog_lookup=catalog_lookup) for j in ordered]

    def cancel(self, job_id):
        target_id = str(job_id or '').strip()
        with self._lock:
            job = self._jobs.get(target_id)
            if not job:
                raise ValueError('Download job not found.')
            if job['state'] in ('queued', 'downloading'):
                job['cancel_event'].set()
                if job['state'] == 'queued':
                    job['state'] = 'cancelled'
            return self._serialize(job)

    def clear_finished(self):
        with self._lock:
            self._jobs = {k: v for k, v in self._jobs.items() if v['state'] in ('queued', 'downloading')}
            return []
