"""Build the public installer; engines are downloaded from their pinned origins."""
import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
from urllib.parse import urlparse

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / 'scripts'))
from build_release import find_iscc


def load_dependencies(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def dependency_entries(entries):
    lines = []
    for item in entries:
        url = urlparse(item['url'])
        if (url.scheme != 'https' or url.hostname not in {'www.gyan.dev', 'repo.jellyfin.org', 'github.com'}
                or url.username or url.password or any(c in item['url'] for c in '\r\n";')):
            raise ValueError('Dependency URL must use a pinned official HTTPS origin.')
        if not re.fullmatch(r'[a-f0-9]{64}', item['sha256']):
            raise ValueError('Dependency SHA-256 is required.')
        if (not re.fullmatch(r'[A-Za-z0-9._-]+', item['filename'])
                or item['destination'] not in {'', 'ffmpeg', 'yt-dlp', 'cloudflared'}
                or not isinstance(item['external_size'], int) or item['external_size'] <= 0):
            raise ValueError('Invalid dependency filename, destination or size.')
        destination = '{code:EngineDir}' + ('\\' + item['destination'] if item['destination'] else '')
        flags = 'external download ignoreversion'
        if item['archive']:
            flags += ' extractarchive recursesubdirs createallsubdirs'
        lines.append(f'Source: "{item["url"]}"; DestName: "{item["filename"]}"; DestDir: "{destination}"; '
                     f'Hash: "{item["sha256"]}"; ExternalSize: {item["external_size"]}; Flags: {flags}')
    return '\n'.join(lines) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=PROJECT / 'dist/public-1.0.0')
    parser.add_argument('--app-exe', type=Path, help='Use an already verified application build')
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        parser.error('Choose a new output directory; existing files are never overwritten.')
    compiler = find_iscc()
    if not compiler:
        parser.error('Inno Setup 6.7+ is required.')
    entries = load_dependencies(PROJECT / 'installer/dependencies.json')
    include_text = dependency_entries(entries)
    output.mkdir(parents=True)
    payload = output / 'payload'
    if args.app_exe:
        payload.mkdir()
        shutil.copy2(args.app_exe.resolve(), payload / 'StreamChromeServer.exe')
    else:
        subprocess.run([sys.executable, str(PROJECT / 'scripts/build_release.py'), '--app-only', '--output', str(payload)], check=True)
    shutil.copy2(PROJECT / 'docs/THIRD_PARTY_LICENSES.md', payload / 'THIRD_PARTY_LICENSES.md')
    shutil.copytree(PROJECT / 'licenses', payload / 'app/licenses')
    include = output / 'dependencies.iss'
    include.write_text(include_text, encoding='utf-8')
    subprocess.run([str(compiler), '/DOnlineMode=1', f'/DReleaseRoot={payload}', f'/DDependencyInclude={include}',
                    f'/O{output}', str(PROJECT / 'installer/StreamChromeServer.iss')], check=True)
    print(f'Public installer ready under {output}; no engine binaries embedded.')


if __name__ == '__main__':
    main()
