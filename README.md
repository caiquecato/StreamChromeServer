# StreamChrome Server

![StreamChrome Server: Windows media server and browser player](docs/figures/figura_01_capa.svg)

A Windows media server with a browser player, local movie and series catalog, Stremio addon sources, downloads, and a Jellyfin companion for TVs. Built with Python, FFmpeg, and vanilla HTML, CSS, and JavaScript.

[Download the Windows installer](https://github.com/caiquecato/StreamChromeServer/releases/latest) · [Build documentation](docs/BUILD.md) · [Component notices](docs/THIRD_PARTY_LICENSES.md)

## Install

Open the latest release and download `StreamChromeServer-<version>-Setup.exe`. Windows x64 and an internet connection are required. Python does not need to be installed separately.

The public installer downloads pinned components over HTTPS and checks their SHA-256 hashes before installation: FFmpeg/FFprobe, Jellyfin, yt-dlp, Deno, and cloudflared. Their URLs and expected hashes are recorded in [installer/dependencies.json](installer/dependencies.json). FFmpeg is obtained from Gyan's Windows builds; the other components come from their upstream release repositories.

Choose an installation mode in the wizard:

| Mode | Application and data |
| --- | --- |
| Standard | Installs under `%LOCALAPPDATA%\Programs\StreamChromeServer`, with a Start menu shortcut and uninstaller. Settings and runtime data use `%LOCALAPPDATA%\StreamChromeServer`; the default media library is `%USERPROFILE%\Videos\StreamChromeServer`. |
| Portable | Installs into a folder you choose on an HD or USB drive. Engines and data stay in `app/`, with `Filmes/` and `Series/` beside the executable. No shortcuts or uninstall registration are created. Internet access is still required during installation. |

The public release uses this online installer for both modes. Close the application before updating it, then keep the same mode and destination. Standard uninstallation preserves profile data and the media library.

## Start watching

1. Launch **StreamChrome Server** and open the hub at [localhost:8780](http://localhost:8780).
2. Add your media under the library's `Filmes/` or `Series/` folders, then select **Scan library** in **Local**.
3. Open a title to play it or inspect its details. For a series, open **Episodes** and choose an episode.
4. Use **Discover** to browse catalogs or search by title or IMDb ID. Open **Addons** to add an HTTPS Stremio `manifest.json` URL.

Example library structure:

```text
Filmes/
  Movie Title (2026)/
    Movie Title (2026).mkv
Series/
  Show Title (2026)/
    Season 01/
      Show Title S01E01.mkv
```

The interface supports English, Portuguese, and Spanish. Open **Appearance** to change the language, light/dark theme, or layout.

## Six browsing layouts

Each layout changes the catalog composition as well as its colors. Figure 1 is the cover above; Figures 2–7 show the six browsing layouts below.

| Neutral | Cinema |
| --- | --- |
| A portrait gallery and split feature. | A larger feature and landscape browsing shelves. |
| ![Figure 2: Neutral layout](docs/figures/figura_02_layout_neutral.jpg) | ![Figure 3: Cinema layout](docs/figures/figura_03_layout_cinema.jpg) |

| Classic | Studio |
| --- | --- |
| Compact horizontal cards with metadata beside each thumbnail. | A broad artwork panel and a four-column browsing board. |
| ![Figure 4: Classic layout](docs/figures/figura_04_layout_classic.jpg) | ![Figure 5: Studio layout](docs/figures/figura_05_layout_studio.jpg) |

| Library | Quiet Glass |
| --- | --- |
| One row per title, with a separate metadata column. | A centered collection with larger portrait artwork and rounded controls. |
| ![Figure 6: Library layout](docs/figures/figura_06_layout_library.jpg) | ![Figure 7: Quiet Glass layout](docs/figures/figura_07_layout_glass.jpg) |

The featured title carousel has previous, next, and pause controls. Watch progress appears on catalog cards, and **Continue watching** returns to unfinished titles.

## Discover and episode sources

Discover includes trending and top IMDb catalogs for movies and series, plus genre filters. Figure 8 shows a series catalog filtered by genre.

![Figure 8: Discover series by genre](docs/figures/figura_08_descobrir_generos.jpg)

Open an episode to inspect its available addon sources in a separate overlay. Filter by available quality or language tags, then choose **Watch now** or **Download to library**. Figure 9 shows this source overlay; available playback and download actions depend on the source returned by the addon.

![Figure 9: Episode source overlay](docs/figures/figura_09_fontes_episodio.jpg)

**Download season** lets you select episodes and choose a source for each before submitting the download queue. Figure 10 shows two selected episodes and one excluded episode.

![Figure 10: Season download selection](docs/figures/figura_10_download_temporada.jpg)

The catalog adapts to narrow screens. Figure 11 shows a series feature with its playback, episode, detail, and carousel controls.

<img src="docs/figures/figura_11_serie_mobile.jpg" alt="Figure 11: Series feature and catalog on a narrow screen" width="320">

## Playback and downloads

The browser player supports FFmpeg remuxing or transcoding, audio track selection, and subtitles from local files, SubDL, OpenSubtitles, and addons. NVIDIA NVENC is used when available; codec and hardware support determine which playback modes can be used.

An active floating miniplayer keeps the video visible while you browse **Local** or **Discover**. Figure 12 shows a local video playing above the catalog.

![Figure 12: Active floating miniplayer](docs/figures/figura_12_miniplayer.jpg)

Title details include FFprobe-derived resolution, bitrate, duration, codec/profile, frame rate, audio tracks, and file size when available. Figure 13 shows specifications read from the demonstration video file.

![Figure 13: Local file technical specifications](docs/figures/figura_13_detalhes_tecnicos.jpg)

The download queue supports direct HTTPS sources and yt-dlp links. Direct downloads can resume partial files through HTTP Range requests when the source supports them. Completed library downloads are organized into title folders and trigger local and Jellyfin scans.

## TV and remote access

Open **TV & Remote** for LAN addresses, QR codes, the M3U playlist, and Jellyfin access. The hub uses port `8780`; Jellyfin uses port `8096`. A Windows Firewall helper is available for private-network access.

Optional Cloudflare Tunnel access provides an HTTPS address with a server PIN. Use the system tray controls or the hub's **Exit** button to stop the server and Jellyfin. Installation does not enable automatic startup.

## Settings and credentials

Subtitle API keys, addon configuration that may include private tokens, and Jellyfin link credentials are encrypted for the current Windows user with DPAPI. Remote access stores a salted PIN hash. Browser-facing addon information uses masked URLs and session tokens.

Keep media, runtime data, and credentials out of version control. The repository's ignore rules exclude application data and local build outputs.

## Development

The application runs from `src/server_pro.py`. Source checkouts use the project root for media and data; engine folders follow the paths resolved in that file. See [BUILD.md](docs/BUILD.md) for packaging and [THIRD_PARTY_LICENSES.md](docs/THIRD_PARTY_LICENSES.md) for component notices and provenance.

```powershell
python src/server_pro.py --local-only
```

Run the Python and JavaScript checks from the repository root:

```powershell
python -m unittest discover -s tests -v
Get-ChildItem tests/*.cjs | ForEach-Object { node $_.FullName }
```

To build the public online installer, install the build requirements and Inno Setup 6.7 or later, then run:

```powershell
python -m pip install -r requirements-build.txt
python scripts/build_online_installer.py
```

The builder requires a new output directory and refuses to overwrite an existing one. Dependency versions and SHA-256 pins are maintained in `installer/dependencies.json`.

## Screenshots and credits

Orbit, The Ridge, Tides, and After Hours are fictional demonstration titles. Their artwork is licensed photography, not official movie posters. The small demonstration video and its metadata illustrate playback and file inspection; screenshots do not represent a supplied media collection.

See [visual_credits.md](docs/visual_credits.md) for image authors, licenses, and source pages. Application by **OhCato**. [Support the project](https://buymeacoffee.com/caique1704u).
