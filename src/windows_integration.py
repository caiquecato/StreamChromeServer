"""Opt-in, per-user portable integration. No server runtime imports or shell commands."""
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

RUN_NAME = 'StreamChromePortableWatcher'


def _digest(path):
    with open(path, 'rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _read(path, default):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2), encoding='utf-8')
    temporary.replace(path)


class WindowsAPI:
    def __init__(self):
        if sys.platform != 'win32':
            raise OSError('Windows integration is available on Windows only.')
        self.frozen = bool(getattr(sys, 'frozen', False))
        self.executable = Path(sys.executable).resolve()
        self.host_dir = Path(os.environ['LOCALAPPDATA']) / 'StreamChromeServer/PortableWatcher'
        from ctypes import wintypes
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.kernel.GetVolumePathNameW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
        self.kernel.GetVolumePathNameW.restype = wintypes.BOOL
        self.kernel.GetVolumeInformationW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(wintypes.DWORD), wintypes.LPWSTR, wintypes.DWORD]
        self.kernel.GetVolumeInformationW.restype = wintypes.BOOL
        self.kernel.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
        self.kernel.GetDriveTypeW.restype = wintypes.UINT
        self.kernel.GetLogicalDrives.argtypes = []
        self.kernel.GetLogicalDrives.restype = wintypes.DWORD

    def volume_for(self, path):
        root = ctypes.create_unicode_buffer(32768)
        if not self.kernel.GetVolumePathNameW(str(Path(path).resolve()), root, len(root)):
            raise ctypes.WinError(ctypes.get_last_error())
        serial = ctypes.c_ulong()
        if not self.kernel.GetVolumeInformationW(root.value, None, 0, ctypes.byref(serial), None, None, None, 0):
            raise ctypes.WinError(ctypes.get_last_error())
        return Path(root.value), f'{serial.value:08x}'

    def drives(self):
        mask = self.kernel.GetLogicalDrives()
        for i in range(26):
            if mask & (1 << i):
                root = Path(f'{chr(65+i)}:/')
                if self.kernel.GetDriveTypeW(str(root)) not in (2, 3):
                    continue
                try:
                    yield self.volume_for(root)
                except OSError:
                    continue

    def set_run(self, command):
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Run') as key:
            winreg.SetValueEx(key, RUN_NAME, 0, winreg.REG_SZ, command)

    def delete_run(self):
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Run', 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, RUN_NAME)
        except FileNotFoundError:
            pass

    def start(self, executable, arguments=()):
        subprocess.Popen([str(executable), *arguments], cwd=Path(executable).parent,
                         creationflags=subprocess.CREATE_NO_WINDOW, close_fds=True)


class WindowsIntegration:
    def __init__(self, app_directory, data_dir, icon_path, api=None):
        self.app_directory = Path(app_directory).resolve()
        self.data_dir = Path(data_dir).resolve()
        self.icon_path = Path(icon_path).resolve()
        self.api = api
        self.reason = ''
        if self.api is None:
            try:
                self.api = WindowsAPI()
            except OSError as exc:
                self.reason = str(exc)

    def _context(self):
        if self.api is None:
            raise ValueError(self.reason)
        if not self.api.frozen:
            raise ValueError('Integration requires the compiled Windows executable.')
        if self.data_dir != self.app_directory / 'app/data' or not self.data_dir.is_dir():
            raise ValueError('Integration requires a portable installation (app/data).')
        root, serial = self.api.volume_for(self.app_directory)
        relative = Path(self.api.executable).resolve().relative_to(Path(root).resolve())
        if Path(self.api.executable).resolve().parent != self.app_directory:
            raise ValueError('The running executable must belong to this portable installation.')
        return Path(root), str(serial), relative.as_posix()

    def status(self):
        try:
            root, serial, relative = self._context()
        except (ValueError, OSError) as exc:
            return {'available': False, 'autorun': False, 'drive_icon': False, 'reason': str(exc)}
        entries = _read(self.api.host_dir / 'registrations.json', [])
        state = _read(self.data_dir / 'drive-icon.json', {})
        return {'available': True,
                'autorun': any(e.get('serial') == serial and e.get('relative_exe') == relative for e in entries),
                'drive_icon': bool(state), 'reason': '',
                'notice': 'Automatic USB launch requires this Windows account and its local watcher.'}

    def refresh_registration(self):
        """Refresh a previously opted-in binary after an installer or manual upgrade."""
        _, serial, relative = self._context()
        entries = _read(self.api.host_dir / 'registrations.json', [])
        entry = next((entry for entry in entries if entry.get('serial') == serial
                      and entry.get('relative_exe') == relative), None)
        if entry is not None and entry.get('sha256') != _digest(self.api.executable):
            return self.configure({'autorun': True})
        return self.status()

    def configure(self, options):
        if not isinstance(options, dict) or not options or set(options) - {'autorun', 'drive_icon'}:
            raise ValueError('Expected autorun and/or drive_icon options.')
        if any(type(value) is not bool for value in options.values()):
            raise ValueError('Integration options must be boolean.')
        root, serial, relative = self._context()
        if 'drive_icon' in options:
            self._icon(root, options['drive_icon'])
        if 'autorun' in options:
            path = self.api.host_dir / 'registrations.json'
            entries = _read(path, [])
            entries = [e for e in entries if not (e.get('serial') == serial and e.get('relative_exe') == relative)]
            if options['autorun']:
                self.api.host_dir.mkdir(parents=True, exist_ok=True)
                # Versioned helpers avoid replacing the executable of an already-running watcher.
                digest = _digest(self.api.executable)
                helper = self.api.host_dir / f'watcher-{digest[:16]}.exe'
                if not helper.exists():
                    shutil.copy2(self.api.executable, helper)
                entries.append({'serial': serial, 'relative_exe': relative, 'sha256': digest})
                _write(path, entries)
                self.api.set_run(subprocess.list2cmdline([str(helper), '--watch-portable']))
                self.api.start(helper, ['--watch-portable'])
            else:
                _write(path, entries)
                if not entries:
                    self.api.delete_run()
        return self.status()

    def _icon(self, root, enabled):
        target = root / 'autorun.inf'
        state_path = self.data_dir / 'drive-icon.json'
        state = _read(state_path, {})
        if not enabled:
            if not state:
                return
            if not target.is_file() or _digest(target) != state['written_sha256']:
                raise ValueError('autorun.inf changed since activation; restore it manually to preserve those edits.')
            if state['existed']:
                target.write_bytes(bytes.fromhex(state['original_hex']))
            else:
                target.unlink()
            state_path.unlink()
            return
        if state:
            if not target.is_file() or _digest(target) != state['written_sha256']:
                raise ValueError('autorun.inf changed since activation.')
            return
        if not self.icon_path.is_file():
            raise ValueError('The application icon file is missing.')
        relative = self.icon_path.relative_to(root).as_posix()
        original = target.read_bytes() if target.exists() else b''
        # Refuse unknown encodings rather than corrupting an existing drive configuration.
        try:
            text = original.decode('utf-8-sig')
        except UnicodeDecodeError as exc:
            raise ValueError('autorun.inf encoding is unsupported; existing file was preserved.') from exc
        if re.search(r'^\s*icon\s*=', text, re.I | re.M):
            raise ValueError('autorun.inf already defines an icon; existing configuration was preserved.')
        match = re.search(r'^\s*\[autorun\][ \t]*\r?$', text, re.I | re.M)
        if match:
            text = text[:match.end()] + '\r\nicon=' + relative + text[match.end():]
        else:
            text += '\r\n[autorun]\r\nicon=' + relative + '\r\n'
        written = text.encode('utf-8')
        _write(state_path, {'existed': target.exists(), 'original_hex': original.hex(),
                            'written_sha256': hashlib.sha256(written).hexdigest()})
        try:
            target.write_bytes(written)
        except OSError:
            state_path.unlink(missing_ok=True)
            raise


def poll_registered(api, seen):
    """Launch only registered exact binaries, once per attachment, regardless of drive letter."""
    entries = _read(api.host_dir / 'registrations.json', [])
    present = set()
    for root, serial in api.drives():
        for entry in entries:
            relative = entry.get('relative_exe', '')
            if str(serial) != entry.get('serial') or not relative:
                continue
            candidate = (Path(root) / relative).resolve()
            if not candidate.is_relative_to(Path(root).resolve()) or candidate.suffix.lower() != '.exe':
                continue
            key = (str(serial), relative)
            present.add(key)
            if key in seen or not candidate.is_file():
                continue
            if _digest(candidate) == entry.get('sha256'):
                api.start(candidate)
    return present


def run_portable_watcher(api=None):
    api = api or WindowsAPI()
    # A per-user, host-local lock prevents duplicate helpers across upgrades and logins.
    import msvcrt
    api.host_dir.mkdir(parents=True, exist_ok=True)
    with (api.host_dir / 'watcher.lock').open('a+b') as lock:
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return
        seen = set()
        while _read(api.host_dir / 'registrations.json', []):
            try:
                seen = poll_registered(api, seen)
            except (OSError, ValueError):
                pass
            time.sleep(2)
