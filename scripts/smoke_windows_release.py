"""Install, launch, stop and remove a release in disposable C: test folders."""
import argparse
import json
import os
from pathlib import Path
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import winreg

PROJECT = Path(__file__).resolve().parents[1]
SCRATCH = PROJECT / 'scratch'
UNINSTALL_KEY = r'Software\Microsoft\Windows\CurrentVersion\Uninstall\{F56E3490-42B2-4D08-A409-6216D54D9E36}_is1'


def uninstall_entry_exists():
    for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY, 0, winreg.KEY_READ | view):
                return True
        except FileNotFoundError:
            pass
    return False


def request(port, path, data=None):
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(f'http://127.0.0.1:{port}{path}', data=body,
                                 headers={'Content-Type': 'application/json', 'X-StreamChrome': '1'})
    with urllib.request.urlopen(req, timeout=3) as response:
        return response.read()


def available_port():
    with socket.socket() as server:
        server.bind(('127.0.0.1', 0))
        return server.getsockname()[1]


def check_console_free(exe):
    with exe.open('rb') as file:
        header = file.read(64)
        file.seek(struct.unpack_from('<I', header, 60)[0] + 24 + 68)
        assert struct.unpack('<H', file.read(2))[0] == 2, 'Application must use the Windows GUI subsystem'


def run_installer(command, timeout):
    process = subprocess.Popen(command, creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        result = process.wait(timeout=timeout)
        if result:
            raise subprocess.CalledProcessError(result, command)
    except BaseException:
        if process.poll() is None:
            subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                           check=True, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
            process.wait(timeout=10)
        raise


def smoke_mode(installer, mode, root):
    destination = root / mode
    destination.mkdir()
    sentinel = destination / 'existing-user-file.txt'
    sentinel.write_text('preserve-existing-files', encoding='utf-8')
    log = root / (mode + '-setup.log')
    command = [str(installer), '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-', '/NOICONS',
               f'/MODE={mode}', f'/DIR={destination}', f'/LOG={log}']
    print('INSTALL', mode, flush=True)
    run_installer(command, timeout=900)
    assert sentinel.read_text(encoding='utf-8') == 'preserve-existing-files'
    exe = destination / 'StreamChromeServer.exe'
    assert exe.is_file(), f'Installer ignored selected destination: {destination}'
    check_console_free(exe)
    if mode == 'portable':
        assert not (destination / 'unins000.exe').exists(), 'Portable mode created an uninstaller'
        assert not uninstall_entry_exists(), 'Portable mode registered an uninstaller'
        assert (destination / 'Movies').is_dir() and (destination / 'Series').is_dir()
        assert not (destination / 'Filmes').exists(), 'New installs must use English library folders'
        expected_data = destination / 'app/data'
        test_env = os.environ.copy()
        test_env['LOCALAPPDATA'] = str(root / 'profile')
        subprocess.run([str(exe), '--configure-integration', '{"drive_icon":false}'], env=test_env,
                       check=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        assert (destination / 'unins000.exe').is_file()
        assert uninstall_entry_exists()
        assert not (destination / 'app').exists(), 'Standard mode selected the portable runtime layout'
        library = root / 'standard-library'
        (destination / 'library_root.txt').write_text(str(library), encoding='utf-8')
        expected_data = root / 'profile/StreamChromeServer'
    env = os.environ.copy()
    env['LOCALAPPDATA'] = str(root / 'profile')
    port = available_port()
    jellyfin_port = available_port()
    app = subprocess.Popen([str(exe), '--local-only', '--no-browser', '--port', str(port), '--jellyfin-port', str(jellyfin_port)], env=env,
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        deadline = time.monotonic() + 35
        while True:
            assert app.poll() is None, f'Application exited during {mode} startup'
            try:
                status = json.loads(request(port, '/api/status'))
                if status.get('ffmpeg') and status.get('ffprobe'):
                    break
            except (OSError, urllib.error.URLError):
                pass
            if time.monotonic() >= deadline:
                raise AssertionError('Application did not become ready')
            time.sleep(0.25)
        while True:
            try:
                with urllib.request.urlopen(f'http://127.0.0.1:{jellyfin_port}/health', timeout=2) as response:
                    if response.read() == b'Healthy':
                        break
            except (OSError, urllib.error.URLError):
                pass
            if time.monotonic() >= deadline:
                raise AssertionError('Bundled Jellyfin did not become healthy')
            time.sleep(0.25)
        settings = json.loads(request(port, '/api/settings', {'subdl_key': 'fixture-key-not-real'}))
        assert settings['subdl'] is True
        credentials = expected_data / 'subtitle_settings.dat'
        assert credentials.is_file(), 'Credentials escaped the selected data directory'
        assert b'fixture-key-not-real' not in credentials.read_bytes(), 'Credentials stored in plaintext'
        application_settings = json.loads(request(port, '/api/app-settings'))
        assert application_settings['paths']['movies'].endswith('Movies')
        assert not application_settings['integration']['autorun']
        update = json.loads(request(port, '/api/updates/check', {}))
        assert update['supported'] and update['current_version'] == status['version']
        print('RUNNING', mode, status['version'], 'GUI subsystem, FFmpeg, FFprobe, Jellyfin and encrypted settings OK', flush=True)
        request(port, '/api/shutdown', {})
        app.wait(timeout=9)
        assert app.returncode == 0
        try:
            urllib.request.urlopen(f'http://127.0.0.1:{jellyfin_port}/health', timeout=1)
        except (OSError, urllib.error.URLError):
            pass
        else:
            raise AssertionError('Jellyfin survived application shutdown')
        print('STOPPED', mode, flush=True)
    finally:
        if app.poll() is None:
            try:
                request(port, '/api/shutdown', {})
                app.wait(timeout=9)
            except (OSError, subprocess.TimeoutExpired):
                subprocess.run(['taskkill', '/PID', str(app.pid), '/T', '/F'], capture_output=True,
                               creationflags=subprocess.CREATE_NO_WINDOW)
        if mode == 'standard' and (destination / 'unins000.exe').is_file():
            run_installer([str(destination / 'unins000.exe'), '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART'], timeout=60)
            assert not uninstall_entry_exists(), 'Test uninstaller registry entry remains'
            assert sentinel.is_file(), 'Uninstaller deleted a preexisting user file'
    print('VERIFIED', mode, flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('installer', type=Path)
    args = parser.parse_args()
    if sys.platform != 'win32':
        parser.error('Windows only')
    installer = args.installer.resolve()
    if not installer.is_relative_to(PROJECT / 'dist') or not installer.is_file():
        parser.error('Installer must be a local project build under dist/')
    if uninstall_entry_exists():
        parser.error('Existing installed StreamChrome Server detected; refusing to modify it')
    shortcut = Path(os.environ['APPDATA']) / 'Microsoft/Windows/Start Menu/Programs/StreamChrome Server.lnk'
    previous_shortcut = shortcut.read_bytes() if shortcut.exists() else None
    SCRATCH.mkdir(exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix='install-smoke-', dir=SCRATCH)).resolve()
    assert root.is_relative_to(SCRATCH.resolve()) and root.drive.upper() == 'C:'
    try:
        smoke_mode(installer, 'portable', root)
        smoke_mode(installer, 'standard', root)
    finally:
        uninstaller = root / 'standard/unins000.exe'
        if uninstall_entry_exists():
            if not uninstaller.is_file():
                raise RuntimeError(f'Test installation requires cleanup; preserved at {root}')
            run_installer([str(uninstaller), '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART'], timeout=60)
            if uninstall_entry_exists():
                raise RuntimeError(f'Test uninstall registration remains; preserved at {root}')
        for log in root.glob('*-setup.log'):
            shutil.copy2(log, SCRATCH / log.name)
        if root.is_relative_to(SCRATCH.resolve()) and root.name.startswith('install-smoke-'):
            shutil.rmtree(root)
        if previous_shortcut is not None:
            shortcut.write_bytes(previous_shortcut)
    assert not uninstall_entry_exists()
    assert shortcut.read_bytes() == previous_shortcut if previous_shortcut is not None else not shortcut.exists()
    print('CLEAN: test installations, test data and uninstall entries removed', flush=True)


if __name__ == '__main__':
    main()
