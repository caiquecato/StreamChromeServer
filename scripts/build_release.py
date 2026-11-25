"""Build a clean Windows release; never read mutable app/data or existing config."""
import argparse
import os
from datetime import datetime
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

PROJECT = Path(__file__).resolve().parents[1]
ENGINE_FILES = ('ffmpeg/ffmpeg.exe', 'ffmpeg/ffprobe.exe', 'yt-dlp/yt-dlp.exe',
                'cloudflared/cloudflared.exe')
OPTIONAL_FILES = ('ffmpeg/LICENSE', 'ffmpeg/README.txt', 'yt-dlp/LICENSE',
                  'yt-dlp/THIRD_PARTY_LICENSES.txt', 'yt-dlp/SHA2-256SUMS',
                  'yt-dlp/yt-dlp.tar.gz', 'yt-dlp/deno.exe', 'yt-dlp/DENO-LICENSE.md',
                  'cloudflared/LICENSE', 'cloudflared/NOTICE',
                  'licenses/ffmpeg-source.tar.gz', 'licenses/jellyfin-source.tar.gz',
                  'licenses/ffmpeg-source.tar.xz',
                  'licenses/pystray-source.tar.gz', 'licenses/pillow-source.tar.gz',
                  'licenses/jellyfin-web-source.tar.gz', 'licenses/cloudflared-source.tar.gz',
                  'licenses/jellyfin-LICENSE', 'licenses/jellyfin-web-LICENSE',
                  'licenses/download-manifest.json')
RUNTIME_NOTICES = ('Python-LICENSE.txt', 'pillow-LICENSE', 'pystray-COPYING',
                   'pystray-COPYING.LGPL', 'six-LICENSE', 'pyinstaller-COPYING.txt',
                   'Tk-license.terms', 'Tcl-license.terms')
PILLOW_NOTICES = ('AOM', 'BROTLI', 'BZIP2', 'DAV1D', 'FREETYPE2', 'HARFBUZZ', 'LCMS2',
                  'LIBAVIF', 'LIBJPEG', 'LIBLZMA', 'LIBPNG', 'LIBTIFF', 'LIBWEBP',
                  'LIBYUV', 'OPENJPEG', 'RAQM', 'XAU', 'XCB', 'XDMCP', 'ZLIB', 'ZSTD')
ASSETS = ('app.ico', 'appearance.js', 'downloads.js', 'hub.css', 'hub.js', 'i18n.js',
          'index.html', 'layouts.css', 'manifest.webmanifest', 'player_helpers.js',
          'player.css', 'presentation.js', 'qrcode.js', 'season_queue.js', 'series.css', 'settings.js', 'settings.css')
SYSTEM_DIRS = {'jellyfin-web', 'Resources', 'ServerSetupApp', 'wwwroot'}
SYSTEM_JSON = {'jellyfin.deps.json', 'jellyfin.runtimeconfig.json',
               'jellyfin.staticwebassets.endpoints.json'}
PRIVATE_DIRS = {'data', 'config', 'cache', 'logs', 'plugins', 'metadata'}


def stage_release(executable, binaries_root, destination, notice):
    """Copy only listed tools and the validated Jellyfin distribution system tree."""
    executable, binaries_root, destination, notice = map(Path, (executable, binaries_root, destination, notice))
    if destination.exists():
        raise FileExistsError(f'Refusing to modify existing release: {destination}')
    system = binaries_root / 'jellyfin/system'
    copies = [(executable, Path('StreamChromeServer.exe')),
              (notice, Path('THIRD_PARTY_LICENSES.md')),
              (PROJECT / 'assets/app.ico', Path('app.ico'))]
    copies.extend((binaries_root / item, Path('app') / item) for item in ENGINE_FILES)
    copies.extend((binaries_root / item, Path('app') / item) for item in OPTIONAL_FILES
                  if (binaries_root / item).is_file())
    copies.extend((binaries_root / 'licenses/runtime' / name,
                   Path('app/licenses/runtime') / name) for name in
                  (*RUNTIME_NOTICES, *(f'Pillow-{item}.txt' for item in PILLOW_NOTICES))
                  if (binaries_root / 'licenses/runtime' / name).is_file())
    if not (system / 'jellyfin.exe').is_file():
        raise FileNotFoundError(system / 'jellyfin.exe')
    for source in system.rglob('*'):
        relative = source.relative_to(system)
        if any(part.lower() in PRIVATE_DIRS for part in relative.parts):
            raise ValueError(f'Runtime data found in Jellyfin distribution: {relative}')
        if source.is_symlink() or source.is_junction():
            raise ValueError(f'Links are not permitted in releases: {source}')
        if source.is_dir():
            if relative.parts[0] not in SYSTEM_DIRS:
                raise ValueError(f'Unexpected Jellyfin distribution directory: {relative}')
            continue
        if len(relative.parts) == 1 and not (
            source.suffix.lower() in {'.exe', '.dll', '.pdb', '.xml'}
            or source.name in SYSTEM_JSON or source.name == 'web.config'
            or source.name.lower().startswith(('license', 'copying', 'notice', 'third-party', 'third_party'))
        ):
            raise ValueError(f'Unexpected Jellyfin distribution file: {relative}')
        copies.append((source, Path('app/jellyfin/system') / relative))
    for source, _ in copies:
        if not source.is_file():
            raise FileNotFoundError(source)
        # Check every ancestor too: Windows junctions can redirect an otherwise ordinary file.
        if source.is_symlink() or any(p.is_symlink() or p.is_junction() for p in source.parents):
            raise ValueError(f'Links are not permitted in releases: {source}')
    destination.mkdir(parents=True)
    for source, relative in copies:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    (destination / 'app/data').mkdir()
    (destination / 'Movies').mkdir()
    (destination / 'Series').mkdir()
    return destination


def find_iscc():
    found = shutil.which('ISCC.exe')
    if found:
        return Path(found)
    for base in ('C:/Program Files (x86)', 'C:/Program Files',
                 str(Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'Programs'),
                 str(Path.home() / 'AppData/Local/Programs')):
        for version in ('6', '7'):
            candidate = Path(base) / f'Inno Setup {version}' / 'ISCC.exe'
            if candidate.is_file():
                return candidate
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binaries-root', type=Path, default=PROJECT / 'scratch/engines',
                        help='Trusted engines directory; only allowlisted distribution files are read')
    parser.add_argument('--output', type=Path,
                        help='New output directory; an existing directory is never overwritten')
    parser.add_argument('--version', default='1.1.2')
    parser.add_argument('--iscc', type=Path, help='Inno Setup compiler path')
    parser.add_argument('--skip-installer', action='store_true')
    parser.add_argument('--app-only', action='store_true',
                        help='Build just the application executable without reading engine files')
    args = parser.parse_args()
    if sys.platform != 'win32':
        parser.error('The Windows executable must be built on Windows.')
    if not args.version or any(c not in '0123456789.-' for c in args.version):
        parser.error('Version must contain only digits, periods and hyphens.')
    output = (args.output or PROJECT / 'dist' / datetime.now().strftime('%Y%m%d-%H%M%S')).resolve()
    if output.exists():
        parser.error(f'Output must be a new directory: {output}')
    compiler = args.iscc or find_iscc()
    scratch = PROJECT / 'scratch'
    scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='build-release-', dir=scratch) as work:
        work = Path(work)
        command = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onefile',
                   '--windowed', '--name', 'StreamChromeServer', '--paths', str(PROJECT / 'src'),
                   '--icon', str(PROJECT / 'assets/app.ico'), '--distpath', str(work / 'dist'),
                   '--workpath', str(work / 'build'), '--specpath', str(work)]
        for asset in ASSETS:
            path = PROJECT / 'assets' / asset
            if not path.is_file():
                raise FileNotFoundError(path)
            command.extend(('--add-data', f'{path};assets'))
        command.append(str(PROJECT / 'src/server_pro.py'))
        subprocess.run(command, check=True, cwd=PROJECT)
        if args.app_only:
            output.mkdir(parents=True)
            shutil.copy2(work / 'dist/StreamChromeServer.exe', output / 'StreamChromeServer.exe')
            shutil.copy2(PROJECT / 'assets/app.ico', output / 'app.ico')
            print(f'Application only: {output / "StreamChromeServer.exe"}; external engines not included.')
            return
        portable = stage_release(work / 'dist/StreamChromeServer.exe', args.binaries_root.resolve(),
                                 output / 'portable', PROJECT / 'docs/THIRD_PARTY_LICENSES.md')
    archive = shutil.make_archive(str(output / f'StreamChromeServer-{args.version}-portable'),
                                  'zip', root_dir=portable)
    print(f'Portable ZIP: {archive}')
    if not args.skip_installer and compiler:
        subprocess.run([str(compiler), f'/DReleaseRoot={portable}', f'/DReleaseVersion={args.version}',
                        f'/O{output}', str(PROJECT / 'installer/StreamChromeServer.iss')], check=True)
    elif not args.skip_installer:
        print('Installer not built: Inno Setup ISCC.exe was not found. Portable ZIP is ready.')
    print('Local build only. Review third-party source and license obligations before distribution.')


if __name__ == '__main__':
    main()
