"""Exercise the packaged Windows update helper in a disposable C: installation."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.request

PROJECT = Path(__file__).resolve().parents[1]


def request(port, shutdown=False):
    with urllib.request.urlopen(urllib.request.Request(f'http://127.0.0.1:{port}/api/' + ('shutdown' if shutdown else 'status'),
            data=b'{}' if shutdown else None, headers={'X-StreamChrome':'1','Content-Type':'application/json'}), timeout=2) as response:
        return json.loads(response.read())


def ready(port):
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        try:
            return request(port)
        except OSError:
            time.sleep(.25)
    raise AssertionError('Packaged application did not start')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('exe', type=Path)
    args=parser.parse_args()
    source=args.exe.resolve()
    assert source.is_relative_to(PROJECT/'dist') and source.is_file()
    root=Path(tempfile.mkdtemp(prefix='update-smoke-',dir=PROJECT/'scratch')).resolve()
    assert root.is_relative_to((PROJECT/'scratch').resolve()) and root.drive.upper()=='C:'
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));jellyfin_port=sock.getsockname()[1]
    destination=root/'install';(destination/'app/data').mkdir(parents=True)
    media=destination/'Movies/existing-user-file.txt';media.parent.mkdir();media.write_bytes(b'preserve-media')
    settings=destination/'app/data/existing-settings.txt';settings.write_bytes(b'preserve-settings')
    target=destination/'StreamChromeServer.exe';shutil.copy2(source,target)
    helper=root/'helper.exe';shutil.copy2(source,helper)
    candidate=root/'candidate.exe';shutil.copy2(source,candidate)
    launch_args=['--no-browser','--local-only','--port',str(port),'--jellyfin-port',str(jellyfin_port)]
    process=subprocess.Popen([str(target),*launch_args],creationflags=subprocess.CREATE_NO_WINDOW)
    updater=None
    try:
        before=ready(port)
        # The packaged process has a launcher and a server child; production waits for the latter.
        probe=subprocess.run(['powershell','-NoProfile','-Command',f'(Get-CimInstance Win32_Process -Filter "ParentProcessId = {process.pid}").ProcessId'],capture_output=True,text=True,check=True)
        child=int(probe.stdout.strip())
        spec=root/'update.json'
        spec.write_text(json.dumps({'target':str(target),'candidate':str(candidate),'pid':child,
            'sha256':hashlib.sha256(candidate.read_bytes()).hexdigest(),
            'restart_args':launch_args}))
        updater=subprocess.Popen([str(helper),'--apply-update',str(spec)],creationflags=subprocess.CREATE_NO_WINDOW)
        request(port,True);process.wait(timeout=12);updater.wait(timeout=45)
        assert updater.returncode==0, f'Native helper failed with {updater.returncode}'
        after=ready(port)
        assert before['version']==after['version'] and after['lan'] is False
        assert media.read_bytes()==b'preserve-media' and settings.read_bytes()==b'preserve-settings'
        assert not target.with_name(target.name+'.previous').exists()
        assert not target.with_name(target.name+'.update').exists()
        print('Native application helper: replacement, restart, port/local-only and user files verified',flush=True)
    finally:
        try: request(port,True)
        except OSError: pass
        if process.poll() is None:
            process.wait(timeout=12)
        if updater and updater.poll() is None:
            updater.wait(timeout=45)
        for _ in range(40):
            try:
                shutil.rmtree(root)
                break
            except PermissionError:
                time.sleep(.25)
        else:
            raise RuntimeError(f'Test installation cleanup failed: {root}')
    print('CLEAN: native update test installation removed',flush=True)


if __name__=='__main__':
    main()
