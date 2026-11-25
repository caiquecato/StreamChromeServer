"""Managed Jellyfin Portable lifecycle, library refresh, and user password bridge."""
import json
import os
from pathlib import Path
import subprocess
import threading
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

from process_guard import attach_to_server
from subtitles import load_settings, save_settings

NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0


class JellyfinBridge:
    def __init__(self, jellyfin_dir, ffmpeg_bin, link_path, settings_path, port=8096, runtime_dir=None):
        self.jellyfin_dir = Path(jellyfin_dir)
        self.exe = self.jellyfin_dir / 'system' / 'jellyfin.exe'
        if (self.jellyfin_dir / 'jellyfin.exe').is_file():
            self.exe = self.jellyfin_dir / 'jellyfin.exe'
        runtime = Path(runtime_dir) if runtime_dir is not None else self.jellyfin_dir
        self.data_dir = runtime / 'data'
        self.config_dir = runtime / 'config'
        self.cache_dir = runtime / 'cache'
        self.log_dir = runtime / 'logs'
        self.ffmpeg_bin = Path(ffmpeg_bin)
        self.link_path = Path(link_path)
        self.settings_path = Path(settings_path)
        self.port = int(port)
        self._lock = threading.RLock()
        self._process = None

    def _read_link(self):
        if not self.link_path.is_file():
            return {}
        try:
            return json.loads(self.link_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return {}

    def _settings(self):
        try:
            return load_settings(self.settings_path)
        except (OSError, ValueError):
            return {}

    def _save_settings(self, data):
        try:
            save_settings(data, self.settings_path)
        except (OSError, ValueError):
            pass

    def has_password(self):
        return bool(self._settings().get('jellyfin_password_set'))

    def current_password(self):
        return str(self._settings().get('jellyfin_current_pw') or '')

    def is_available(self):
        return self.exe.is_file()

    def is_running(self):
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{self.port}/health', timeout=1.5) as resp:
                return resp.read().decode('utf-8', errors='replace').strip() == 'Healthy'
        except (OSError, ValueError):
            return False

    def start(self):
        with self._lock:
            if not self.is_available() or self.is_running():
                return self.is_running()
            if self._process is not None and self._process.poll() is None:
                return True
            self._configure_http_port()
            args = [
                str(self.exe),
                '--datadir', str(self.data_dir),
                '--configdir', str(self.config_dir),
                '--cachedir', str(self.cache_dir),
                '--logdir', str(self.log_dir),
                '--ffmpeg', str(self.ffmpeg_bin),
                '--service',
            ]
            proc = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)
            attach_to_server(proc)
            self._process = proc
        for _ in range(30):
            if self.is_running():
                return True
            time.sleep(0.5)
        return self.is_running()

    def _configure_http_port(self):
        if not 1 <= self.port <= 65535:
            raise ValueError('Invalid Jellyfin port.')
        path = self.config_dir / 'network.xml'
        parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
        tree = ET.parse(path, parser=parser) if path.exists() else ET.ElementTree(ET.Element('NetworkConfiguration'))
        for name in ('InternalHttpPort', 'PublicHttpPort'):
            field = tree.getroot().find(name)
            if field is None:
                field = ET.SubElement(tree.getroot(), name)
            field.text = str(self.port)
        self.config_dir.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix('.xml.tmp')
        tree.write(temporary, encoding='utf-8', xml_declaration=True)
        temporary.replace(path)

    def stop(self):
        with self._lock:
            proc = self._process
            self._process = None
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=4)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()

    def status(self):
        info = {}
        running = False
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{self.port}/System/Info/Public', timeout=1.5) as resp:
                info = json.loads(resp.read().decode('utf-8'))
                running = True
        except (OSError, ValueError):
            pass
        link = self._read_link()
        return {
            'available': self.is_available(),
            'running': running,
            'port': self.port,
            'version': str(info.get('Version') or ''),
            'server_name': str(info.get('ServerName') or 'StreamServer'),
            'username': str(link.get('username') or 'caique'),
            'has_password': self.has_password(),
        }

    def refresh_library(self):
        link = self._read_link()
        token = link.get('jellyfin_api_key', '')
        if not token or not self.is_running():
            return False
        def _send():
            try:
                req = urllib.request.Request(
                    f'http://127.0.0.1:{self.port}/Library/Refresh',
                    data=b'',
                    headers={'X-Emby-Token': token},
                    method='POST',
                )
                with urllib.request.urlopen(req, timeout=4):
                    pass
            except (OSError, ValueError):
                pass
        threading.Thread(target=_send, daemon=True).start()
        return True

    def set_user_password(self, new_password):
        pw = str(new_password or '').strip()
        if len(pw) < 4:
            raise ValueError('Jellyfin password must have at least 4 characters.')
        link = self._read_link()
        token = link.get('jellyfin_api_key', '')
        user_id = link.get('jellyfin_user_id', '')
        if not token or not user_id:
            raise ValueError('Jellyfin API link is not initialized yet.')
        settings = self._settings()
        current_pw = str(settings.get('jellyfin_current_pw') or '')
        payload = json.dumps({'CurrentPw': current_pw, 'NewPw': pw}).encode('utf-8')
        req = urllib.request.Request(
            f'http://127.0.0.1:{self.port}/Users/{user_id}/Password',
            data=payload,
            headers={'Content-Type': 'application/json', 'X-Emby-Token': token},
            method='POST',
        )
        try:
            with urllib.request.urlopen(req, timeout=6):
                pass
        except urllib.error.HTTPError as err:
            raise ValueError(f'Jellyfin rejected password update (HTTP {err.code}).') from None
        except (OSError, ValueError):
            raise ValueError('Could not reach Jellyfin to update the password.') from None
        settings['jellyfin_current_pw'] = pw
        settings['jellyfin_password_set'] = True
        self._save_settings(settings)
        return self.status()
