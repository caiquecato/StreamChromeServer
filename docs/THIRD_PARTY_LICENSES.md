# Third-party components

The public `StreamChromeServer-1.0.0-Setup.exe` includes the StreamChrome application and the repository's `licenses/` files. It downloads the engines listed below from upstream during installation; those engine binaries are not embedded in the public setup executable. The offline packages produced by `scripts/build_release.py` are local test artifacts, not public release assets.

## Engine downloads and pinned hashes

`installer/dependencies.json` is the build manifest. `scripts/build_online_installer.py` passes these URLs and SHA-256 values to Inno Setup, which checks the downloaded files before installing them. These values pin file identity; this document does not claim an independent upstream checksum audit for every entry.

| Component | Version | Upstream download | SHA-256 of the downloaded file |
| --- | --- | --- | --- |
| FFmpeg / FFprobe | 9.0.2 essentials build | [FFmpeg ZIP](https://github.com/GyanD/codexffmpeg/releases/download/9.0.2/ffmpeg-9.0.2-essentials_build.zip) | `60f467265b1e312373dbcd92200c2618a74850f98d3d078e94296bb3fa2047ba` |
| Jellyfin | 12.4 | [Windows amd64 ZIP](https://repo.jellyfin.org/files/server/windows/stable/v12.4/amd64/jellyfin_12.4-amd64.zip) | `d62b5b30390213680fd1fae5484082ae27b2a778cfd40a473ad7b1e4147d75a6` |
| yt-dlp | 2026.10.26 | [Windows executable](https://github.com/yt-dlp/yt-dlp/releases/download/2026.10.26/yt-dlp.exe) | `f5389d3698ecde4ef06f4e6b1ce0294fe9ac1396228c8990b8d3416d6d177a7d` |
| Deno | 2.9.8 | [Windows x86-64 ZIP](https://github.com/denoland/deno/releases/download/v2.9.8/deno-x86_64-pc-windows-msvc.zip) | `cba84b26e0df5ae7f74fc66b86a95b866872a86292ebd84fa1e959927edd93bf` |
| cloudflared | 2026.11.2 | [Windows amd64 executable](https://github.com/cloudflare/cloudflared/releases/download/2026.11.2/cloudflared-windows-amd64.exe) | `555b46c85582461d189cac30d14ad05480d612613801be9f110b8ce08c5b2113` |

FFmpeg, Jellyfin and Deno are extracted from their upstream archives. yt-dlp and cloudflared are installed as downloaded executables. Deno supplies the JavaScript runtime used by yt-dlp.

## Upstream licenses and source references

The downloaded packages remain subject to their upstream licenses and dependency notices. The following links identify the projects and versioned source references; they are not a claim that a complete corresponding-source set has been assembled or audited.

| Component | License and project information | Versioned source reference |
| --- | --- | --- |
| FFmpeg build | The Gyan essentials build retains its GPLv3 license and build README. The [distribution page](https://www.gyan.dev/ffmpeg/builds/) identifies its configuration and linked libraries. | [FFmpeg 9.0.2 source](https://ffmpeg.org/releases/ffmpeg-9.0.2.tar.xz) |
| Jellyfin | Server and web distributions include their license texts and dependency notices. | [Server v12.4](https://github.com/jellyfin/jellyfin/tree/v12.4), [Web v12.4](https://github.com/jellyfin/jellyfin-web/tree/v12.4) |
| yt-dlp | The main project uses Unlicense; the executable includes dependencies with additional licenses. See [upstream licensing](https://github.com/yt-dlp/yt-dlp#license). | [2026.10.26 source archive](https://github.com/yt-dlp/yt-dlp/releases/download/2026.10.26/yt-dlp.tar.gz) |
| Deno | MIT; see the [project license](https://github.com/denoland/deno/blob/v2.9.8/LICENSE.md). | [v2.9.8 source](https://github.com/denoland/deno/tree/v2.9.8) |
| cloudflared | Apache 2.0; see the [project license](https://github.com/cloudflare/cloudflared/blob/2026.11.2/LICENSE). | [2026.11.2 source](https://github.com/cloudflare/cloudflared/tree/2026.11.2) |

The main FFmpeg archive alone does not establish correspondence for every statically linked external library in the Gyan build. A complete mapping of Jellyfin's bundled .NET and web dependencies to source and notices has not been audited. The project does not describe its retained files as a verified complete GPL corresponding-source distribution.

## Application runtime notices included in setup

`licenses/runtime/` retains notices for Python 3.14.4, Pillow 12.2.0, pystray 0.19.5, six 1.17.0, PyInstaller 6.22.3, Tcl 8.6.15 and Tk. It also contains 21 Pillow dependency notices, including libjpeg, libtiff, libwebp, FreeType, LittleCMS, OpenJPEG, libavif, AOM, dav1d and zlib. Some notices describe upstream wheel dependencies that may be absent from this Windows build.

The public builder copies this directory and `licenses/pystray-0.19.5-source.tar.gz` into its payload. Standard installation places them under `licenses/` beside the executable; portable installation uses `app/licenses/`. pystray's GPL and LGPL texts are retained. Its source reference is [v0.19.5](https://github.com/moses-palmer/pystray/tree/v0.19.5).

The [PyInstaller exception](https://pyinstaller.org/en/stable/license.html) addresses distribution of generated applications subject to dependency licenses. The [Inno Setup license](https://jrsoftware.org/files/is/license.txt) applies to the installer compiler. Retaining these notices and source references does not replace the licenses attached to the components themselves.
