# StreamChrome Server

Self-hosted Windows media server, browser player, Stremio addon bridge, and Jellyfin TV companion built on Python standard library, FFmpeg, `yt-dlp`, and vanilla HTML/CSS/JS.

## Features

- **Local Catalog (`Local`):** Indexes movies (`Filmes/<Movie (Year)>`) and TV series (`Series/<Show (Year)>/Season XX`) on disk with IMDb, Rotten Tomatoes, TVMaze posters, FFprobe technical specs (resolution, bitrate Mbps, codec/profile, FPS, audio tracks), and watch progress.
- **Discover & Stremio Addons (`Descobrir`):** Browse Trending and Top IMDb catalogs, paste any HTTPS Stremio addon `manifest.json` URL (including AIOStreams / Debrid addons), filter sources (`4K`, `1080p`, `PT / Dual`), stream directly in the browser player, or queue direct downloads straight into `Filmes` / `Series`.
- **Direct Downloader & `yt-dlp` Integration:** Supports concurrent HTTPS/Debrid direct downloads with `.part` HTTP Range resume and `yt-dlp` links, organizing downloaded files into `<Title (Year)>` folders and triggering instant local + Jellyfin library scans.
- **Player & Live Floating Miniplayer:** FFmpeg remux (`-c:v copy` with NVIDIA RTX VSR compatibility) or NVENC/x264 transcoding, multi-audio track switching, SubDL + OpenSubtitles + addon subtitles, and an active 16:9 floating miniplayer while browsing tabs.
- **Smart TV & Away-from-Home Access (`TV & Remoto`):** Automatically starts and links a bundled portable Jellyfin server (`:8096`) alongside the web hub (`:8780`) with LAN QR codes, M3U playlist (`/tv.m3u`), Windows Firewall rule helper, and optional PIN-protected Cloudflare Tunnel (`cloudflared`) HTTPS access without Tailscale or router port forwarding.
- **Windows Integration:** System tray control, optional automatic startup with Windows (`HKCU\...\Run`), and zero `.cmd` or `.bat` clutter.

## Security & Credentials

- Personal subtitle API keys, Stremio addon manifest URLs (which may contain private addon or Debrid tokens), remote PINs, and Jellyfin link credentials are encrypted locally per Windows user via **DPAPI** inside `data/server_settings.dat` (outside version control).
- Browser APIs expose only masked addon URLs and opaque per-session tokens (`local:`, `addon:`, `remote:`).

## Running from Source

1. Install Python 3.11+ and place portable `ffmpeg.exe`, `ffprobe.exe`, `yt-dlp.exe`, `cloudflared.exe`, and `jellyfin` under `D:\StreamServer\app\`.
2. Start the server:
   ```powershell
   python src\server_pro.py
   ```
3. Run tests:
   ```powershell
   python -m unittest discover -s tests -v
   Get-ChildItem tests\*.cjs | ForEach-Object { node $_.FullName }
   ```
