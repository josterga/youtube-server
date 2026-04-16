"""Settings from environment variables.

Defaults are under the repository root (parent of ``app/``), e.g. ``data/media``,
so Mac/local runs work without env. On a Pi, set ``MEDIA_ROOT`` / ``DB_PATH``
etc. to ``/srv/media-ingest/...`` in ``.env`` or systemd.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env")


def _default(rel: str) -> str:
    return str(_PROJECT_ROOT / rel)


MEDIA_ROOT = os.environ.get("MEDIA_ROOT", _default("data/media"))
META_ROOT = os.environ.get("META_ROOT", _default("data/meta"))
TMP_DIR = os.environ.get("TMP_DIR", _default("data/tmp"))
DB_PATH = os.environ.get("DB_PATH", _default("data/db/app.db"))
APP_HOST = os.environ.get("APP_HOST", "127.0.0.1")
APP_PORT = int(os.environ.get("APP_PORT", "8080"))
MAX_DURATION_SEC = int(os.environ.get("MAX_DURATION_SEC", "0"))

# yt-dlp / YouTube: PO tokens, SABR, and client availability change often.
# Defaults try clients that often work without android/ios GVS PO tokens first.
_pc = os.environ.get(
    "YTDLP_YOUTUBE_PLAYER_CLIENTS",
    "web_creator,mweb,web,android,ios",
).strip()
YTDLP_YOUTUBE_PLAYER_CLIENTS = [x.strip() for x in _pc.split(",") if x.strip()]

# Optional: https://github.com/yt-dlp/yt-dlp/wiki/PO-Token-Guide
# Example: export YTDLP_YOUTUBE_PO_TOKEN="web.gvs+xxx" (or multiple space-separated tokens)
_pt = os.environ.get("YTDLP_YOUTUBE_PO_TOKEN", "").strip()
YTDLP_YOUTUBE_PO_TOKEN = _pt if _pt else None

_cf = os.environ.get("YTDLP_COOKIES_FILE", "").strip()
YTDLP_COOKIES_FILE = _cf if _cf else None

_cb = os.environ.get("YTDLP_COOKIES_BROWSER", "").strip().lower()
YTDLP_COOKIES_BROWSER = _cb if _cb else None

_yk = os.environ.get("YOUTUBE_API_KEY", "").strip()
YOUTUBE_API_KEY = _yk if _yk else None
