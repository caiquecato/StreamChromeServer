"""Cloudflare Quick Tunnels (Hub + Jellyfin), PIN session guard, and Windows autostart."""
import hashlib
import hmac
import html
import ipaddress
import os
from pathlib import Path
import re
import secrets
import subprocess
import threading
import time

from process_guard import attach_to_server
from subtitles import load_settings, save_settings

NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
TUNNEL_URL_RE = re.compile(r'https://([a-z0-9-]+\.trycloudflare\.com)')
AUTOSTART_KEY_PATH = r'Software\Microsoft\Windows\CurrentVersion\Run'
AUTOSTART_VALUE_NAME = 'StreamChromeServer'
SESSION_TTL_SECONDS = 14 * 24 * 3600


def is_private_or_loopback(address):
    try:
        ip = ipaddress.ip_address(str(address).split('%')[0])
        return ip.is_loopback or ip.is_private
    except ValueError:
        return False


def is_loopback_client(address):
    try:
        return ipaddress.ip_address(str(address).split('%')[0]).is_loopback
    except ValueError:
        return False


def is_autostart_enabled():
    if os.name != 'nt':
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_KEY_PATH, 0, winreg.KEY_READ) as key:
            value, _ = winreg.QueryValueEx(key, AUTOSTART_VALUE_NAME)
            return bool(value)
    except OSError:
        return False


def set_autostart(enabled, target_command):
    if os.name != 'nt':
        return False
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, AUTOSTART_KEY_PATH) as key:
        if enabled:
            winreg.SetValueEx(key, AUTOSTART_VALUE_NAME, 0, winreg.REG_SZ, str(target_command))
        else:
            try:
                winreg.DeleteValue(key, AUTOSTART_VALUE_NAME)
            except FileNotFoundError:
                pass
    return is_autostart_enabled()


class _TunnelSlot:
    def __init__(self, target_url):
        self.target_url = target_url
        self.process = None
        self.public_url = ''
        self.host = ''
        self.error = ''
        self.starting = False


class RemoteAccessService:
    def __init__(self, settings_path, cloudflared_bin='cloudflared.exe', hub_port=8780, jellyfin_port=8096):
        self.settings_path = Path(settings_path)
        self.cloudflared_bin = str(cloudflared_bin)
        self.hub_port = int(hub_port)
        self.jellyfin_port = int(jellyfin_port)
        self._lock = threading.RLock()
        self._failures = {}
        self._slots = {
            'hub': _TunnelSlot(f'http://127.0.0.1:{self.hub_port}'),
            'jellyfin': _TunnelSlot(f'http://127.0.0.1:{self.jellyfin_port}'),
        }
        self._settings = self._read_settings()
        if not self._settings.get('session_secret'):
            self._settings['session_secret'] = secrets.token_hex(32)
            self._write_settings()

    def _read_settings(self):
        try:
            return load_settings(self.settings_path)
        except (ValueError, OSError):
            return {}

    def _write_settings(self):
        try:
            save_settings(self._settings, self.settings_path)
        except (ValueError, OSError):
            pass

    def has_pin(self):
        with self._lock:
            return bool(self._settings.get('pin_hash') and self._settings.get('pin_salt'))

    def set_pin(self, pin):
        value = str(pin or '').strip()
        if value == '':
            with self._lock:
                self._settings.pop('pin_hash', None)
                self._settings.pop('pin_salt', None)
                self._settings['session_secret'] = secrets.token_hex(32)
                self._write_settings()
            self.stop('hub')
            return self.status()
        if not 4 <= len(value) <= 16:
            raise ValueError('PIN must be 4 to 16 characters.')
        salt = secrets.token_hex(16)
        digest = hashlib.pbkdf2_hmac('sha256', value.encode('utf-8'), bytes.fromhex(salt), 120000).hex()
        with self._lock:
            self._settings['pin_salt'] = salt
            self._settings['pin_hash'] = digest
            self._settings['session_secret'] = secrets.token_hex(32)
            self._write_settings()
        return self.status()

    def verify_pin(self, pin, client_key='remote'):
        now = time.monotonic()
        with self._lock:
            count, blocked_until = self._failures.get(client_key, (0, 0.0))
            if blocked_until > now:
                raise ValueError('Too many attempts. Wait 60 seconds and try again.')
            if not self.has_pin():
                raise ValueError('Configure an access PIN first.')
            candidate = hashlib.pbkdf2_hmac(
                'sha256', str(pin or '').strip().encode('utf-8'),
                bytes.fromhex(self._settings['pin_salt']), 120000,
            ).hex()
            if not hmac.compare_digest(candidate, self._settings['pin_hash']):
                count += 1
                self._failures[client_key] = (count, now + 60.0 if count >= 5 else 0.0)
                if count >= 5:
                    raise ValueError('Too many attempts. Wait 60 seconds and try again.')
                raise ValueError('Invalid PIN.')
            self._failures.pop(client_key, None)
            payload = str(int(time.time()) + SESSION_TTL_SECONDS)
            sig = hmac.new(self._settings['session_secret'].encode('utf-8'), payload.encode('utf-8'), hashlib.sha256).hexdigest()
            return f'{payload}.{sig}'

    def has_valid_session(self, cookie_header):
        if not cookie_header:
            return False
        token = ''
        for part in str(cookie_header).split(';'):
            name, _, val = part.strip().partition('=')
            if name == 'sc_session':
                token = val.strip()
                break
        if '.' not in token:
            return False
        exp_str, sig = token.split('.', 1)
        if not exp_str.isdigit() or int(exp_str) < int(time.time()):
            return False
        with self._lock:
            expected = hmac.new(self._settings['session_secret'].encode('utf-8'), exp_str.encode('utf-8'), hashlib.sha256).hexdigest()
        return hmac.compare_digest(sig, expected)

    def is_external_request(self, headers, client_ip):
        if headers.get('Cf-Connecting-Ip') or headers.get('Cf-Ray'):
            return True
        host = (headers.get('Host') or '').split(':')[0].lower()
        with self._lock:
            hub_host = self._slots['hub'].host
            if hub_host and host == hub_host:
                return True
        if host.endswith('.trycloudflare.com'):
            return True
        return not is_private_or_loopback(client_ip)

    def tunnel_hosts(self):
        with self._lock:
            return {slot.host for slot in self._slots.values() if slot.host}

    def status(self):
        with self._lock:
            out = {
                'has_pin': self.has_pin(),
                'cloudflared_available': Path(self.cloudflared_bin).is_file(),
            }
            for name, slot in self._slots.items():
                active = slot.process is not None and slot.process.poll() is None
                if not active and not slot.starting:
                    slot.public_url = ''
                    slot.host = ''
                out[name] = {
                    'active': active,
                    'starting': slot.starting,
                    'public_url': slot.public_url if active else '',
                    'error': slot.error,
                }
            # Flat shortcuts for the hub tunnel
            out['active'] = out['hub']['active']
            out['public_url'] = out['hub']['public_url']
            return out

    def start(self, target='hub', port=None, jellyfin_password_ready=False):
        if target not in self._slots:
            raise ValueError('Invalid tunnel target.')
        if port is not None and target == 'hub':
            self.hub_port = int(port)
            self._slots['hub'].target_url = f'http://127.0.0.1:{self.hub_port}'
        with self._lock:
            if target == 'hub' and not self.has_pin():
                raise ValueError('Set an access PIN before enabling the remote Hub link.')
            if target == 'jellyfin' and not jellyfin_password_ready:
                raise ValueError('Set a Jellyfin password first before exposing Jellyfin remotely.')
            slot = self._slots[target]
            if slot.process is not None and slot.process.poll() is None:
                return self.status()
            if not Path(self.cloudflared_bin).is_file():
                raise ValueError('cloudflared.exe was not found in app\\cloudflared.')
            slot.error = ''
            slot.public_url = ''
            slot.host = ''
            slot.starting = True
            proc = subprocess.Popen(
                [self.cloudflared_bin, 'tunnel', '--url', slot.target_url, '--no-autoupdate'],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding='utf-8', errors='replace', creationflags=NO_WINDOW,
            )
            attach_to_server(proc)
            slot.process = proc
            ready_event = threading.Event()
            threading.Thread(target=self._read_tunnel_output, args=(target, proc, ready_event), daemon=True).start()

        ready_event.wait(timeout=14.0)
        with self._lock:
            self._slots[target].starting = False
        return self.status()

    def _read_tunnel_output(self, target, proc, ready_event):
        try:
            for line in proc.stdout:
                match = TUNNEL_URL_RE.search(line)
                if match:
                    with self._lock:
                        slot = self._slots[target]
                        if slot.process is proc:
                            slot.host = match.group(1).lower()
                            slot.public_url = f'https://{slot.host}'
                            slot.starting = False
                    ready_event.set()
            code = proc.wait()
            with self._lock:
                slot = self._slots[target]
                if slot.process is proc:
                    slot.process = None
                    slot.public_url = ''
                    slot.host = ''
                    slot.starting = False
                    if code != 0 and not slot.error:
                        slot.error = 'Cloudflare tunnel disconnected.'
        finally:
            ready_event.set()
            if proc.stdout:
                proc.stdout.close()

    def stop(self, target=None):
        targets = [target] if target in self._slots else list(self._slots.keys())
        for name in targets:
            with self._lock:
                slot = self._slots[name]
                proc = slot.process
                slot.process = None
                slot.public_url = ''
                slot.host = ''
                slot.starting = False
            if proc and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
        return self.status()


def render_pin_gate_html(error_text='', language='pt'):
    msg = html.escape(error_text or '')
    title = 'StreamChrome Server'
    heading = 'Acesso Remoto' if language == 'pt' else 'Remote Access'
    subtitle = 'Digite o PIN definido no servidor para acessar o acervo.' if language == 'pt' else 'Enter the server PIN to unlock the library.'
    placeholder = 'PIN de acesso' if language == 'pt' else 'Access PIN'
    submit = 'Desbloquear' if language == 'pt' else 'Unlock'
    err_block = f'<p class="err">{msg}</p>' if msg else ''
    return f'''<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>
*,*::before,*::after{{box-sizing:border-box;margin:0}}
body{{min-height:100dvh;display:grid;place-items:center;background:#111110;color:#edeae3;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;padding:20px}}
.card{{width:100%;max-width:360px;background:#181816;border:1px solid #2c2c28;border-radius:12px;padding:24px;display:grid;gap:14px}}
.brand{{display:flex;align-items:center;gap:8px;font-weight:650;font-size:15px}}
.brand span{{color:#9a968d;font-size:12px;font-weight:500}}
h1{{font-size:18px;font-weight:650}}
p{{font-size:13px;color:#9a968d;line-height:1.45}}
input{{width:100%;min-height:40px;padding:8px 12px;border-radius:6px;border:1px solid #2c2c28;background:#141412;color:#edeae3;font:inherit;letter-spacing:.12em;text-align:center}}
input:focus-visible{{outline:2px solid #e2ddd2;outline-offset:2px}}
button{{min-height:40px;border:0;border-radius:6px;background:#e2ddd2;color:#141412;font:inherit;font-weight:600;cursor:pointer}}
button:hover{{background:#f1ede4}}
.err{{color:#f2a5a0;font-size:12px}}
</style>
</head>
<body>
<form class="card" id="pinForm">
  <div class="brand"><svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>StreamChrome <span>SERVER</span></div>
  <h1>{heading}</h1>
  <p>{subtitle}</p>
  <input id="pinInput" type="password" inputmode="numeric" autocomplete="current-password" placeholder="{placeholder}" required autofocus>
  {err_block}
  <button type="submit">{submit}</button>
</form>
<script>
document.getElementById('pinForm').addEventListener('submit', async e => {{
  e.preventDefault();
  const res = await fetch('/api/remote/auth', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json', 'X-StreamChrome': '1'}},
    body: JSON.stringify({{pin: document.getElementById('pinInput').value}})
  }});
  if (res.ok) {{ location.reload(); return; }}
  const data = await res.json().catch(() => ({{}}));
  alert(data.error || 'PIN invalido');
}});
</script>
</body>
</html>'''
