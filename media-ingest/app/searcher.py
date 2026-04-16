"""YouTube Data API v3 search helper."""

from __future__ import annotations

import re
from typing import Any

import httpx

from app import config

__all__ = ["search_videos", "SearchError"]

_ISO_RE = re.compile(
    r"P(?:(?P<days>\d+)D)?"
    r"(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?)?"
)


class SearchError(Exception):
    pass


def _parse_duration(iso: str) -> tuple[int, str]:
    """Return (total_seconds, human_readable). Returns (0, 'Live') for zero/unparseable."""
    m = _ISO_RE.fullmatch(iso.strip()) if iso else None
    if not m:
        return 0, "Live"
    h = int(m.group("hours") or 0) + int(m.group("days") or 0) * 24
    mins = int(m.group("minutes") or 0)
    secs = int(m.group("seconds") or 0)
    total = h * 3600 + mins * 60 + secs
    if total == 0:
        return 0, "Live"
    if h:
        return total, f"{h}:{mins:02d}:{secs:02d}"
    return total, f"{mins}:{secs:02d}"


async def search_videos(query: str, max_results: int = 12, order: str = "relevance") -> list[dict[str, Any]]:
    api_key = config.YOUTUBE_API_KEY
    if not api_key:
        raise SearchError("YouTube API key is not configured.")

    async with httpx.AsyncClient(timeout=10.0) as client:
        # 1. Search for video IDs
        r = await client.get(
            "https://www.googleapis.com/youtube/v3/search",
            params={
                "part": "snippet",
                "type": "video",
                "maxResults": max_results,
                "q": query,
                "order": order,
                "key": api_key,
            },
        )
        if r.status_code != 200:
            raise SearchError(f"YouTube search API error: {r.status_code}")
        data = r.json()

        items = [i for i in data.get("items", []) if i.get("id", {}).get("videoId")]
        if not items:
            return []

        video_ids = [item["id"]["videoId"] for item in items]

        # 2. Fetch durations via videos.list (search.list doesn't include contentDetails)
        r2 = await client.get(
            "https://www.googleapis.com/youtube/v3/videos",
            params={
                "part": "contentDetails",
                "id": ",".join(video_ids),
                "key": api_key,
            },
        )
        if r2.status_code != 200:
            raise SearchError(f"YouTube videos API error: {r2.status_code}")
        details = {v["id"]: v for v in r2.json().get("items", [])}

    results = []
    for item in items:
        vid = item["id"]["videoId"]
        snippet = item["snippet"]
        thumbs = snippet.get("thumbnails", {})
        thumb_url = thumbs.get("medium", thumbs.get("default", {})).get("url", "")
        iso_dur = details.get(vid, {}).get("contentDetails", {}).get("duration", "")
        dur_sec, dur_str = _parse_duration(iso_dur)
        results.append({
            "video_id": vid,
            "title": snippet.get("title", ""),
            "channel": snippet.get("channelTitle", ""),
            "thumbnail_url": thumb_url,
            "duration_sec": dur_sec,
            "duration_str": dur_str,
            "youtube_url": f"https://www.youtube.com/watch?v={vid}",
        })
    return results
