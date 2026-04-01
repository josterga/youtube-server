"""yt-dlp wrapper: metadata, downloads, filesystem moves."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable

import yt_dlp
from yt_dlp.utils import DownloadError

from app import config

# Prefer DASH/audio-only; then any bestaudio; then progressive MP4 (format 18 etc.) when YouTube
# only offers combined A/V (common when DASH streams need PO tokens).
AUDIO_FORMAT = (
    "bestaudio[ext=m4a]/bestaudio[ext=mp3]"
    "/bestaudio[acodec^=aac]/bestaudio[acodec^=mp4a]/bestaudio[acodec=mp3]"
    "/bestaudio[ext=webm][acodec=opus]/bestaudio[ext=webm]"
    "/bestaudio"
    "/18/best[ext=mp4]/best"
)

VIDEO_FORMAT = (
    "bestvideo[ext=mp4][vcodec^=avc1][height<=1080]+bestaudio[ext=m4a]"
    "/best[ext=mp4][vcodec^=avc1][height<=1080]"
)


def _tmp_dir() -> Path:
    return Path(config.TMP_DIR)


def _youtube_ydl_fragment() -> dict[str, Any]:
    """Options that reduce YouTube HTTP 403 / SABR breakage; shared by info + download."""
    yt_ex: dict[str, Any] = {
        "player_client": list(config.YTDLP_YOUTUBE_PLAYER_CLIENTS),
    }
    if config.YTDLP_YOUTUBE_PO_TOKEN:
        yt_ex["po_token"] = config.YTDLP_YOUTUBE_PO_TOKEN
    frag: dict[str, Any] = {
        "extractor_args": {
            "youtube": yt_ex,
        },
    }
    if config.YTDLP_COOKIES_FILE:
        frag["cookiefile"] = config.YTDLP_COOKIES_FILE
    if config.YTDLP_COOKIES_BROWSER:
        # e.g. safari, chrome, chromium, firefox, brave, edge
        frag["cookiesfrombrowser"] = (config.YTDLP_COOKIES_BROWSER,)
    return frag


def _audio_opts(log_hook: Callable[[str], None] | None = None) -> dict[str, Any]:
    opts: dict[str, Any] = {
        "format": AUDIO_FORMAT,
        "outtmpl": str(_tmp_dir() / "%(id)s__%(title).80s.%(ext)s"),
        "writeinfojson": True,
        "writethumbnail": True,
        "writesubtitles": True,
        "subtitlesformat": "vtt",
        "subtitleslangs": ["en"],
        "concurrent_fragment_downloads": 1,
        "retries": 3,
        "fragment_retries": 3,
        "postprocessors": [
            {"key": "FFmpegMetadata", "add_metadata": True},
        ],
    }
    opts.update(_youtube_ydl_fragment())
    if log_hook:
        opts["logger"] = _Logger(log_hook)
    return opts


def _video_opts(log_hook: Callable[[str], None] | None = None) -> dict[str, Any]:
    opts: dict[str, Any] = {
        "format": VIDEO_FORMAT,
        "outtmpl": str(_tmp_dir() / "%(id)s__%(title).80s.%(ext)s"),
        "writeinfojson": True,
        "writethumbnail": True,
        "writesubtitles": True,
        "subtitlesformat": "vtt",
        "subtitleslangs": ["en"],
        "concurrent_fragment_downloads": 1,
        "retries": 3,
        "fragment_retries": 3,
        "merge_output_format": "mp4",
        "postprocessors": [
            {"key": "FFmpegVideoRemuxer", "preferedformat": "mp4"},
            {"key": "FFmpegMetadata", "add_metadata": True},
        ],
        "postprocessor_args": {"ffmpeg": ["-c", "copy", "-movflags", "+faststart"]},
    }
    opts.update(_youtube_ydl_fragment())
    if log_hook:
        opts["logger"] = _Logger(log_hook)
    return opts


def _info_opts() -> dict[str, Any]:
    return {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "writeinfojson": False,
        **_youtube_ydl_fragment(),
    }


class _Logger:
    def __init__(self, sink: Callable[[str], None]) -> None:
        self._sink = sink

    def debug(self, msg: str) -> None:
        self._sink(msg + "\n")

    def warning(self, msg: str) -> None:
        self._sink(msg + "\n")

    def error(self, msg: str) -> None:
        self._sink(msg + "\n")


def sanitize_title(title: str) -> str:
    s = re.sub(r"[^0-9a-zA-Z]+", "_", (title or "").strip())
    s = s.strip("_")
    return (s[:80] if s else "untitled") or "untitled"


def fetch_info(url: str) -> dict[str, Any]:
    with yt_dlp.YoutubeDL(_info_opts()) as ydl:
        return ydl.extract_info(url, download=False)


def _mp4_has_video_stream(mp4: Path) -> bool:
    if shutil.which("ffprobe") is None:
        return True
    r = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_type",
            "-of",
            "csv=p=0",
            str(mp4),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    return bool((r.stdout or "").strip())


def _demux_audio_copy_from_mp4(mp4: Path, log_hook: Callable[[str], None] | None) -> Path | None:
    """Strip video with ffmpeg stream copy (-c:a copy). No re-encode."""
    if shutil.which("ffmpeg") is None:
        if log_hook:
            log_hook("ffmpeg not found; cannot strip video from MP4 for audio-only job\n")
        return None
    out = mp4.with_suffix(".m4a")
    r = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(mp4),
            "-vn",
            "-c:a",
            "copy",
            "-movflags",
            "+faststart",
            str(out),
        ],
        capture_output=True,
        text=True,
        timeout=3600,
    )
    if r.returncode != 0 or not out.is_file():
        if log_hook:
            log_hook((r.stderr or r.stdout or "ffmpeg demux failed") + "\n")
        return None
    try:
        mp4.unlink()
    except OSError:
        pass
    return out


def _postprocess_audio_only_file(source_id: str, log_hook: Callable[[str], None] | None) -> None:
    """If audio job landed on a combined MP4, extract AAC into .m4a via copy."""
    paths = _glob_tmp(source_id) + list(_tmp_dir().glob(f"{source_id}.*"))
    mp4s = [p for p in paths if p.suffix.lower() == ".mp4" and p.is_file()]
    if not mp4s:
        return
    main = max(mp4s, key=lambda p: p.stat().st_size)
    if not _mp4_has_video_stream(main):
        return
    _demux_audio_copy_from_mp4(main, log_hook)


def download(
    url: str,
    media_type: str,
    log_hook: Callable[[str], None] | None = None,
    *,
    source_id: str | None = None,
) -> None:
    if media_type == "audio":
        opts = _audio_opts(log_hook)
    elif media_type == "video":
        opts = _video_opts(log_hook)
    else:
        raise ValueError(f"Unknown media_type: {media_type}")
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])
    if media_type == "audio" and source_id:
        _postprocess_audio_only_file(source_id, log_hook)


def _glob_tmp(source_id: str) -> list[Path]:
    base = _tmp_dir()
    if not base.is_dir():
        return []
    return sorted(base.glob(f"{source_id}__*"))


def _find_main_media(paths: list[Path], media_type: str) -> Path | None:
    if media_type == "audio":
        for ext in (".m4a", ".mp3", ".webm", ".opus", ".ogg"):
            c = [p for p in paths if p.suffix.lower() == ext and p.is_file()]
            if c:
                return max(c, key=lambda p: p.stat().st_size)
        mp4s = [p for p in paths if p.suffix.lower() == ".mp4" and p.is_file()]
        if mp4s:
            return max(mp4s, key=lambda p: p.stat().st_size)
        return None
    c = [p for p in paths if p.suffix.lower() == ".mp4" and p.is_file()]
    return max(c, key=lambda p: p.stat().st_size) if c else None


def _find_info_json(paths: list[Path], source_id: str) -> Path | None:
    for p in paths:
        if p.suffix.lower() == ".json" and source_id in p.name and "info" in p.name.lower():
            return p
    for p in paths:
        if p.name.endswith(".info.json") or (p.suffix.lower() == ".json" and p.is_file()):
            return p
    direct = _tmp_dir() / f"{source_id}.info.json"
    if direct.is_file():
        return direct
    return None


def _find_thumbnail(paths: list[Path]) -> Path | None:
    for ext in (".jpg", ".jpeg", ".png", ".webp"):
        for p in paths:
            if p.is_file() and p.suffix.lower() == ext:
                return p
    return None


def _find_subtitle(paths: list[Path], source_id: str) -> Path | None:
    for p in paths:
        n = p.name.lower()
        if n.endswith(".en.vtt") or n.endswith(".en-us.vtt"):
            return p
    for p in _tmp_dir().glob(f"{source_id}*.vtt"):
        return p
    return None


def move_to_final(
    source_id: str,
    media_type: str,
    info: dict[str, Any],
) -> dict[str, Any]:
    """Move artifacts from tmp to MEDIA_ROOT / META_ROOT. Returns paths dict for DB."""
    paths = _glob_tmp(source_id) + list(_tmp_dir().glob(f"{source_id}.*"))
    media_path = _find_main_media(paths, media_type)
    if not media_path:
        raise FileNotFoundError(f"No media file found in tmp for {source_id}")

    title = info.get("title") or "untitled"
    safe = sanitize_title(str(title))
    ext = media_path.suffix.lower().lstrip(".")
    subdir = "audio" if media_type == "audio" else "video"
    dest_dir = Path(config.MEDIA_ROOT) / subdir
    dest_dir.mkdir(parents=True, exist_ok=True)
    final_name = f"{source_id}__{safe}.{ext}"
    final_media = dest_dir / final_name
    shutil.move(str(media_path), str(final_media))

    meta_info = Path(config.META_ROOT) / "info"
    meta_thumbs = Path(config.META_ROOT) / "thumbs"
    meta_subs = Path(config.META_ROOT) / "subs"
    meta_info.mkdir(parents=True, exist_ok=True)
    meta_thumbs.mkdir(parents=True, exist_ok=True)
    meta_subs.mkdir(parents=True, exist_ok=True)

    info_json_path: str | None = None
    thumb_path: str | None = None
    subs_path: str | None = None

    ij = _find_info_json(paths, source_id)
    if ij and ij.is_file() and ij.resolve() != final_media.resolve():
        dest_ij = meta_info / f"{source_id}.info.json"
        try:
            shutil.move(str(ij), str(dest_ij))
        except OSError:
            shutil.copy2(str(ij), str(dest_ij))
        info_json_path = str(dest_ij)

    th = _find_thumbnail(paths)
    if th and th.is_file():
        suf = th.suffix.lower() or ".jpg"
        dest_th = meta_thumbs / f"{source_id}{suf}"
        shutil.move(str(th), str(dest_th))
        thumb_path = str(dest_th)

    sub = _find_subtitle(paths, source_id)
    if sub and sub.is_file():
        dest_sub = meta_subs / f"{source_id}.en.vtt"
        shutil.move(str(sub), str(dest_sub))
        subs_path = str(dest_sub)

    filesize = final_media.stat().st_size if final_media.is_file() else None

    return {
        "file_path": str(final_media),
        "file_ext": ext,
        "filesize_bytes": filesize,
        "thumbnail_path": thumb_path,
        "subs_path": subs_path,
        "info_json_path": info_json_path,
    }


def cleanup_tmp(source_id: str) -> None:
    base = _tmp_dir()
    if not base.is_dir():
        return
    for pattern in (f"{source_id}__*", f"{source_id}.*"):
        for p in base.glob(pattern):
            try:
                if p.is_dir():
                    shutil.rmtree(p, ignore_errors=True)
                else:
                    p.unlink(missing_ok=True)
            except OSError:
                pass


def load_info_json(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def is_no_compatible_format_error(exc: DownloadError) -> bool:
    return "Requested format is not available" in str(exc)


__all__ = [
    "fetch_info",
    "download",
    "move_to_final",
    "cleanup_tmp",
    "sanitize_title",
    "load_info_json",
    "DownloadError",
    "is_no_compatible_format_error",
]
