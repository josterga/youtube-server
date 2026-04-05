# Media Ingest Service

Private YouTube-to-library pipeline: paste a URL, download browser-compatible audio or video with **yt-dlp** (no transcoding beyond remux + `faststart`), browse and play in the browser. Designed for a single **Raspberry Pi**, **SQLite**, **Caddy** for byte-range media, and **Tailscale Serve** (no public exposure).

## Requirements

- **Python 3.11+** on the Pi (3.9+ may work for local testing; yt-dlp warns below 3.10).
- **ffmpeg** and **Caddy** on the system PATH / apt packages.
- **yt-dlp** (pinned via `requirements.txt`).

## Layout

Application code lives under `app/`. **Default data paths** are relative to the repo root: `data/media`, `data/meta`, `data/tmp`, `data/db/app.db` (see `app/config.py`). Override with env vars on a Pi (`/srv/media-ingest/...`) so they match your `Caddyfile` roots.

## Quick dev check

```bash
cd media-ingest
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
./venv/bin/python -c "from app import config, db; db.init_db(); print('db ok', config.DB_PATH)"
./venv/bin/uvicorn app.main:app --reload --host 127.0.0.1 --port 8080
# Second shell, same directory (same defaults)
./venv/bin/python -m app.worker
```

Then open `http://127.0.0.1:8080`. The app serves `/media`, `/thumbs`, and `/subs` from `data/` on that port so **playback works without Caddy**. Optional: `export MEDIA_ROOT=...` etc. if you want paths outside `data/`.

For real playback and seeking, run **Caddy** on port **8081** with this repo’s `Caddyfile`. It expects **`MEDIA_INGEST_ROOT`** set to the **repository root** (same folder as `app/` and `data/`):

```bash
cd media-ingest && export MEDIA_INGEST_ROOT="$PWD"
caddy run --config Caddyfile
```

That matches the default paths in `app/config.py` (`data/media`, `data/meta/...`).

## Production (Pi)

1. Copy the tree to `/srv/media-ingest`, create user `media`, own dirs under `/srv/media-ingest`.
2. Create `/srv/media-ingest/.env` with the same variables as `app/config.py` (optional overrides).
3. `venv` at `/srv/media-ingest/venv`, install `requirements.txt`.
4. Install `systemd/media-app.service` and `systemd/media-worker.service` into `/etc/systemd/system/`, `daemon-reload`, `enable --now` both.
5. Run Caddy with the provided `Caddyfile` (adjust paths if needed), listening on `:8081`.
6. On the Pi: `tailscale serve --bg http://localhost:8081` (tailnet only; do not enable Funnel).

## YouTube `403 Forbidden` or stuck formats

YouTube changes often; yt-dlp must stay **current**:

```bash
./venv/bin/pip install -U yt-dlp
```

This project defaults to **multiple YouTube player clients** (`android`, `web`, `ios`) to avoid web-only **SABR** breakage (see [yt-dlp#12482](https://github.com/yt-dlp/yt-dlp/issues/12482)). Override with comma-separated names:

```bash
export YTDLP_YOUTUBE_PLAYER_CLIENTS="android,web"
```

If you still get **403**, pass **browser cookies** (logged into YouTube in that browser):

```bash
# macOS example: Safari cookie jar (may need Full Disk Access for Terminal)
export YTDLP_COOKIES_BROWSER=safari
# or Chrome/Chromium
# export YTDLP_COOKIES_BROWSER=chrome
```

Or a Netscape cookies file:

```bash
export YTDLP_COOKIES_FILE=/path/to/cookies.txt
```

Restart the **worker** after changing env vars. **Video** downloads require **`ffmpeg`** on your PATH (`brew install ffmpeg` on Mac).

Use **Python 3.10+** when possible; yt-dlp deprecates 3.9.

**Audio-only jobs:** some videos only expose a **combined progressive** stream (e.g. itag **18**) instead of separate DASH audio (especially when higher-quality streams need **PO tokens**). The app’s audio format chain falls back to those, then uses **`ffmpeg -vn -c:a copy`** to mux a **`.m4a`** from the MP4 when needed (still no audio re-encode). If demux fails but the MP4 downloaded, the item may stay as **`.mp4`** in your audio library.

## Verify

- **Health:** `GET /health` → `{"status":"ok"}`.
- **Flow:** Submit a short video URL → **Jobs** shows completion → **Library** lists item → **Item** plays in browser through Caddy.
- **Seeking:** scrub `<video>` / `<audio>` mid-file; no full reload if ranges work.

## License

Use and modify for private/self-hosted use.

./.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8080
./.venv/bin/python -m app.worker                             
export MEDIA_INGEST_ROOT="$PWD"
caddy run --config Caddyfile                                 

## Updating 

rsync -av --exclude='.venv' --exclude='data' \
    /Users/parcl0/Documents/parcl0/youtube-server/media-ingest/ \
    parcl0@10.42.0.214:/home/parcl0/Documents/youtube-server/media-ingest

## Restart - I think
sudo fuser -k 8080/tcp; sudo systemctl restart media-ingest;  
  sleep 2; systemctl status media-ingest --no-pager -n 5