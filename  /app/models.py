"""Shared types / validation helpers."""

from __future__ import annotations

import re
from typing import Literal

MediaType = Literal["audio", "video"]

PLAYLIST_RE = re.compile(r"[?&]list=", re.I)

# youtu.be/ID or youtube.com/watch?v=ID or youtube.com/shorts/ID (optional other query params if no list=)
_YOUTUBE_PATTERNS = [
    re.compile(r"^https?://(?:www\.)?youtu\.be/([a-zA-Z0-9_-]{11})(?:\?[^#]*)?(?:#.*)?$", re.I),
    re.compile(
        r"^https?://(?:www\.)?youtube\.com/watch\?(?:[^#]*&)?v=([a-zA-Z0-9_-]{11})(?:[&][^#]*)?(?:#.*)?$",
        re.I,
    ),
    re.compile(
        r"^https?://(?:www\.)?youtube\.com/shorts/([a-zA-Z0-9_-]{11})(?:\?[^#]*)?(?:#.*)?$",
        re.I,
    ),
]


def looks_like_youtube_video_url(url: str) -> bool:
    u = (url or "").strip()
    if not u:
        return False
    if PLAYLIST_RE.search(u):
        return False
    return any(p.match(u) for p in _YOUTUBE_PATTERNS)


def extract_video_id(url: str) -> str | None:
    u = (url or "").strip()
    if not u or PLAYLIST_RE.search(u):
        return None
    for p in _YOUTUBE_PATTERNS:
        m = p.match(u)
        if m:
            return m.group(1)
    return None


def normalize_media_type(value: str) -> MediaType | None:
    v = (value or "").lower().strip()
    if v in ("audio", "video"):
        return v  # type: ignore[return-value]
    return None
