# Windows build and installation

The public release is one `StreamChromeServer-1.0.0-Setup.exe`, built with `scripts/build_online_installer.py`. It contains the application, runtime license notices and the source archive retained under `licenses/`. Third-party engines are downloaded from their upstream HTTPS URLs during installation and checked against the SHA-256 values in `installer/dependencies.json`.

## Build the public installer

Use Windows, Python with the packages in `requirements-build.txt`, and Inno Setup 6.7 or newer. Archive extraction and downloads use Inno Setup's native support.

```powershell
python -m pip install -r requirements-build.txt
python -m unittest discover -s tests
Get-ChildItem tests/test_*.cjs | ForEach-Object { node $_.FullName; if ($LASTEXITCODE -ne 0) { throw "JavaScript test failed" } }
python scripts/build_online_installer.py --output dist/public-1.0.0
```

The output directory must be new. The builder compiles `StreamChromeServer.exe` with PyInstaller `--windowed --onefile`, stages the repository's `licenses/` directory and `docs/THIRD_PARTY_LICENSES.md`, generates download entries from the dependency manifest, then compiles `installer/StreamChromeServer.iss` in online mode. Compilation does not download or embed the engine binaries.

The public release asset is `dist/public-1.0.0/StreamChromeServer-1.0.0-Setup.exe`. The adjacent `payload/` and `dependencies.iss` are build intermediates. Building does not install the application, publish files, register it with Windows or enable automatic startup.

An already verified application executable can be supplied with `--app-exe PATH`. It must include the current interface assets and runtime fixes. The normal build compiles those sources directly; the explicit asset list is maintained in `scripts/build_release.py`.

## Install in standard or portable mode

Run the public installer with internet access. It asks for the installation mode before the destination, downloads FFmpeg, Jellyfin, yt-dlp, Deno and cloudflared, and checks each download's pinned hash. Archives are extracted into the selected engine directory. See [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md) for the exact URLs and hashes.

| Mode | Application and engines | Data and media | Windows integration |
| --- | --- | --- | --- |
| Standard | `%LOCALAPPDATA%\Programs\StreamChromeServer` by default; engines beside the executable | Settings and catalog in `%LOCALAPPDATA%\StreamChromeServer`; media in `%USERPROFILE%\Videos\StreamChromeServer\Filmes` and `Series` | Start menu shortcut, optional desktop shortcut and uninstaller |
| Portable | Chosen folder; engines under its `app/` directory | Settings and catalog under `app/data/`; media in `Filmes/` and `Series/` beside the executable | No shortcuts, uninstall registration or uninstaller |

Portable mode can be installed to a selected disk or USB folder. Keep the executable and `app/` together. Neither mode registers browser native messaging hosts or enables automatic startup.

The downloaded FFmpeg archive keeps its versioned directory, so its executables are under `ffmpeg/ffmpeg-9.0.2-essentials_build/bin/`. Jellyfin's official archive is extracted with `jellyfin/jellyfin.exe`. Deno is installed beside `yt-dlp.exe`. Runtime notices are installed under `licenses/runtime/` in standard mode and `app/licenses/runtime/` in portable mode.

Silent installation accepts `/MODE=standard` or `/MODE=portable`, with `/DIR=` selecting the destination:

```powershell
.\StreamChromeServer-1.0.0-Setup.exe /VERYSILENT /MODE=portable /DIR="E:\StreamChromeServer"
```

Close the application before updating. Keep the same installation mode and destination to retain its data layout. Standard mode refuses a destination containing a portable `app/` directory. Distributed files are replaced; the installer has no instructions to delete media, settings, credentials or runtime databases. Standard uninstallation preserves profile data and the media library.

## Local development packages

`scripts/build_release.py` can stage trusted engines from `scratch/engines` into an offline portable directory, ZIP and installer. These artifacts are for local testing and are not public release assets. Private engine data and configuration are excluded by the staging allowlist; symlinks, junctions and runtime data inside the Jellyfin distribution tree are rejected.

```powershell
python scripts/build_release.py --skip-installer --output dist/local-test-1.0.0
python scripts/build_release.py --app-only --output dist/app-test-1.0.0
```

The offline test layout uses `app/jellyfin/system/`, while the online installer preserves the official archive layout. The runtime supports both. An app-only build contains no engines.

`scratch/`, `dist/`, installed engines and personal data must be excluded from the source repository. Before publishing the setup executable, verify the Python and JavaScript suites, inspect the final artifact, and test startup, playback, Exit and both installation modes. A compiler success alone does not verify installation behavior.
