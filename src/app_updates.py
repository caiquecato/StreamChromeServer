"""Verified updates that replace only the packaged application executable."""
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.request
from urllib.parse import urlparse
import zipfile

RELEASE_API = 'https://api.github.com/repos/caiquecato/StreamChromeServer/releases/latest'
MAX_BYTES = 200 * 1024 * 1024
APP_NAME = 'StreamChromeServer.exe'


def _trusted_url(url):
    parsed = urlparse(url)
    host = (parsed.hostname or '').lower()
    return (parsed.scheme == 'https' and not parsed.username and not parsed.password
            and parsed.port in (None, 443)
            and (host in ('api.github.com', 'github.com', 'release-assets.githubusercontent.com')
                 or host.endswith('.githubusercontent.com')))


class _TrustedRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _trusted_url(newurl):
            raise ValueError('The update download redirected to an untrusted address.')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open_url(url):
    if not _trusted_url(url):
        raise ValueError('Untrusted update address.')
    request = urllib.request.Request(url, headers={'User-Agent': 'StreamChromeServer-Updater', 'Accept': 'application/vnd.github+json'})
    return urllib.request.build_opener(_TrustedRedirect()).open(request, timeout=30)


def _read_release():
    with _open_url(RELEASE_API) as response:
        payload = response.read(2 * 1024 * 1024 + 1)
    if len(payload) > 2 * 1024 * 1024:
        raise ValueError('The release response exceeded the size limit.')
    return json.loads(payload)


def _version(value):
    match = re.fullmatch(r'v?(\d+)\.(\d+)(?:\.(\d+))?', str(value))
    if not match:
        raise ValueError('Unsupported release version.')
    return tuple(int(part or 0) for part in match.groups())


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _restart_arguments(values, strict=True):
    if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
        raise ValueError('Invalid update restart arguments.')
    result = []
    index = 0
    while index < len(values):
        value = values[index]
        index += 1
        if value in ('--local-only', '--no-browser', '--background'):
            result.append(value)
        elif value in ('--port', '--jellyfin-port') or value.startswith(('--port=', '--jellyfin-port=')):
            option = value.partition('=')[0]
            if value == option:
                if index == len(values):
                    raise ValueError('Missing restart port.')
                port = values[index]
                index += 1
            else:
                port = value.partition('=')[2]
            if not port.isascii() or not port.isdigit() or not 1 <= int(port) <= 65535:
                raise ValueError('Invalid restart port.')
            result.extend((option, str(int(port))))
        elif strict:
            raise ValueError('Unsupported update restart argument.')
    return result


class Updater:
    def __init__(self, app_directory, data_dir, version, restart_args=None):
        self.app_directory = Path(app_directory).resolve()
        self.data_dir = Path(data_dir).resolve()
        self.version = str(version)
        self.restart_args = _restart_arguments(sys.argv[1:], strict=False) if restart_args is None else _restart_arguments(restart_args)

    def _release(self):
        release = _read_release()
        latest = str(release.get('tag_name') or '').removeprefix('v')
        assets = release.get('assets') or []
        asset = next((item for item in assets if item.get('name') == f'StreamChromeServer-{latest}-app.zip'), None)
        reason = ''
        if release.get('draft') or release.get('prerelease'):
            reason = 'Only stable releases can be installed.'
        elif _version(latest) <= _version(self.version):
            reason = 'The application is up to date.'
        elif not asset:
            reason = 'This release does not include an application update package.'
        elif not re.fullmatch(r'sha256:[0-9a-fA-F]{64}', str(asset.get('digest') or '')):
            reason = 'The release is missing a verified SHA-256 digest.'
        elif not _trusted_url(asset.get('browser_download_url') or ''):
            reason = 'The release download address is untrusted.'
        return release, latest, asset, reason

    def check(self):
        supported = bool(getattr(sys, 'frozen', False))
        if not supported:
            return {'current_version': self.version, 'latest_version': self.version, 'available': False,
                    'release_url': '', 'supported': False, 'reason': 'Updates require the packaged desktop application.'}
        release, latest, asset, reason = self._release()
        release_url = release.get('html_url') or ''
        if not _trusted_url(release_url):
            release_url = ''
        return {'current_version': self.version, 'latest_version': latest, 'available': not reason,
                'release_url': release_url, 'supported': supported, 'reason': reason}

    def apply(self):
        if not getattr(sys, 'frozen', False):
            raise ValueError('Updates require the packaged desktop application.')
        release, latest, asset, reason = self._release()
        if reason:
            raise ValueError(reason)
        cache = self.data_dir / 'updates'
        cache.mkdir(parents=True, exist_ok=True)
        work = Path(tempfile.mkdtemp(prefix='app-', dir=cache))
        try:
            bundle = work / 'app.zip'
            digest = hashlib.sha256()
            size = 0
            with _open_url(asset['browser_download_url']) as response, bundle.open('wb') as output:
                while chunk := response.read(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_BYTES:
                        raise ValueError('The update package exceeded the size limit.')
                    digest.update(chunk)
                    output.write(chunk)
            if digest.hexdigest() != asset['digest'].split(':', 1)[1].lower():
                raise ValueError('The update package failed SHA-256 verification.')
            candidate = work / APP_NAME
            try:
                with zipfile.ZipFile(bundle) as archive:
                    entries = archive.infolist()
                    if (len(entries) != 1 or entries[0].filename != APP_NAME
                            or entries[0].file_size > MAX_BYTES
                            or stat.S_ISLNK(entries[0].external_attr >> 16)):
                        raise ValueError('The update package must contain only the application executable.')
                    with archive.open(entries[0]) as source, candidate.open('wb') as output:
                        shutil.copyfileobj(source, output)
            except zipfile.BadZipFile as error:
                raise ValueError('Invalid update package.') from error
            target = self.app_directory / APP_NAME
            if not target.is_file():
                raise ValueError('The installed application executable was not found.')
            # A separate executable can replace the application after this process exits.
            helper = work / 'update-helper.exe'
            shutil.copy2(sys.executable, helper)
            spec = work / 'update.json'
            spec.write_text(json.dumps({'pid': os.getpid(), 'target': str(target), 'candidate': str(candidate),
                                        'sha256': _sha256(candidate), 'restart_args': self.restart_args}), encoding='utf-8')
            subprocess.Popen([str(helper), '--apply-update', str(spec)], cwd=str(work),
                             creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            return {'restarting': True, 'latest_version': latest}
        except Exception:
            shutil.rmtree(work, ignore_errors=True)
            raise


def _wait_for_parent(pid):
    if os.name != 'nt':
        raise RuntimeError('Application updates are supported on Windows only.')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.OpenProcess(0x00100000, False, int(pid))
    if not handle:
        if ctypes.get_last_error() == 87:  # Parent already exited.
            return
        raise OSError(ctypes.get_last_error(), 'Cannot wait for the application to exit.')
    try:
        if kernel.WaitForSingleObject(handle, 120000) != 0:
            raise TimeoutError('The application did not exit before the update timeout.')
    finally:
        kernel.CloseHandle(handle)


def run_update_helper(specstr):
    spec = json.loads(Path(specstr).read_text(encoding='utf-8'))
    target = Path(spec['target']).resolve()
    candidate = Path(spec['candidate']).resolve()
    if target.name != APP_NAME or _sha256(candidate) != spec['sha256']:
        raise ValueError('Invalid application update specification.')
    restart_args = _restart_arguments(spec.get('restart_args', []))
    _wait_for_parent(int(spec['pid']))
    staging = target.with_name(APP_NAME + '.update')
    backup = target.with_name(APP_NAME + '.previous')
    if backup.exists() or staging.exists():
        raise ValueError('A previous update backup or staging file requires recovery.')
    try:
        shutil.copy2(candidate, staging)
        if _sha256(staging) != spec['sha256']:
            raise ValueError('The staged executable failed verification.')
        os.replace(target, backup)
        try:
            os.replace(staging, target)
            subprocess.Popen([str(target), '--refresh-integration', *restart_args], cwd=str(target.parent))
        except Exception:
            os.replace(backup, target)
            raise
        backup.unlink()
    finally:
        staging.unlink(missing_ok=True)
