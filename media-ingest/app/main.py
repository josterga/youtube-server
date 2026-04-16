"""FastAPI app: HTML UI and form actions. Media bytes are served by Caddy."""

from __future__ import annotations  # PEP 563 — required for Python 3.9 + FastAPI union syntax

import os
import urllib.parse
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from app import config, db, models, searcher

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

app = FastAPI(title="Media Ingest")


def _format_duration(sec: int | None) -> str:
    if sec is None:
        return "—"
    sec = int(sec)
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _job_duration_display(row: dict[str, Any]) -> str:
    if row.get("item_duration") is not None:
        return _format_duration(row["item_duration"])
    return "—"


def _job_status_label(status: str) -> str:
    if status == "failed_no_compatible_format":
        return "No compatible format"
    if status == "failed_duration_exceeded":
        return "Duration exceeded"
    if status == "failed_duplicate":
        return "Duplicate"
    return status.replace("_", " ").title()


def _error_message(msg_key: Optional[str]) -> Optional[str]:
    if not msg_key:
        return None
    messages = {
        "invalid_youtube": "Enter a valid single-video YouTube URL (no playlists).",
        "duplicate": "This video is already in your library.",
        "bad_type": "Choose audio or video.",
        "not_found": "Not found.",
    }
    return messages.get(msg_key, urllib.parse.unquote_plus(msg_key))


templates.env.filters["duration_fmt"] = _format_duration
templates.env.filters["urlencode"] = lambda s: urllib.parse.quote_plus(s or "")
templates.env.globals.update(
    job_duration_display=_job_duration_display,
    job_status_label=_job_status_label,
)


def _ensure_layout_dirs() -> None:
    for p in (
        config.MEDIA_ROOT,
        config.META_ROOT,
        Path(config.META_ROOT) / "thumbs",
        Path(config.META_ROOT) / "subs",
        Path(config.MEDIA_ROOT) / "audio",
        Path(config.MEDIA_ROOT) / "video",
        config.TMP_DIR,
        str(Path(config.DB_PATH).parent),
    ):
        Path(p).mkdir(parents=True, exist_ok=True)


@app.on_event("startup")
async def _startup() -> None:
    await run_in_threadpool(db.init_db)
    await run_in_threadpool(_ensure_layout_dirs)


@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse({"status": "ok"})


@app.get("/", response_class=HTMLResponse)
async def root(request: Request) -> Any:
    return RedirectResponse("/search", status_code=302)


@app.get("/submit", response_class=HTMLResponse)
async def index(request: Request, error: Optional[str] = None) -> Any:
    recent = await run_in_threadpool(db.list_jobs_recent, 5)
    poll = await run_in_threadpool(db.any_active_jobs)
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "recent_jobs": recent,
            "error_message": _error_message(error),
            "poll_jobs": poll,
        },
    )


@app.post("/submit")
async def submit(
    url: str = Form(...),
    media_type: str = Form(...),
) -> RedirectResponse:
    mt = models.normalize_media_type(media_type)
    if not mt:
        q = urllib.parse.urlencode({"error": "bad_type"})
        return RedirectResponse(f"/submit?{q}", status_code=303)
    u = (url or "").strip()
    if not models.looks_like_youtube_video_url(u):
        return RedirectResponse("/submit?error=invalid_youtube", status_code=303)
    vid = models.extract_video_id(u)
    if vid and await run_in_threadpool(db.media_item_exists, vid):
        return RedirectResponse("/submit?error=duplicate", status_code=303)
    await run_in_threadpool(db.create_job, u, mt)
    return RedirectResponse("/jobs", status_code=303)


@app.get("/jobs", response_class=HTMLResponse)
async def jobs_page(
    request: Request,
    status: Optional[str] = None,
    page: int = 1,
) -> Any:
    per_page = 50
    page = max(1, page)
    offset = (page - 1) * per_page
    tab = status if status in ("active", "failed", "completed") else None
    rows, total = await run_in_threadpool(
        db.list_jobs_page, tab, per_page, offset
    )
    poll = await run_in_threadpool(db.any_active_jobs)
    pages = (total + per_page - 1) // per_page
    return templates.TemplateResponse(
        request,
        "jobs.html",
        {
            "jobs": rows,
            "filter": tab or "all",
            "page": page,
            "pages": max(1, pages),
            "total": total,
            "poll_jobs": poll,
        },
    )


@app.post("/jobs/{job_id}/retry")
async def job_retry(job_id: str) -> RedirectResponse:
    ok = await run_in_threadpool(db.reset_job_for_retry, job_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Job not retryable")
    return RedirectResponse("/jobs", status_code=303)


@app.post("/jobs/{job_id}/delete")
async def job_delete(job_id: str) -> RedirectResponse:
    ok = await run_in_threadpool(db.delete_job, job_id)
    if not ok:
        raise HTTPException(status_code=400, detail="Cannot delete this job")
    return RedirectResponse("/jobs", status_code=303)


@app.get("/library", response_class=HTMLResponse)
async def library_page(
    request: Request,
    q: Optional[str] = None,
    kind: Optional[str] = None,
    page: int = 1,
) -> Any:
    per_page = 48
    page = max(1, page)
    offset = (page - 1) * per_page
    mt = kind if kind in ("audio", "video") else None
    rows, total = await run_in_threadpool(
        db.list_media_items, q, mt, per_page, offset
    )
    pages = (total + per_page - 1) // per_page
    return templates.TemplateResponse(
        request,
        "library.html",
        {
            "items": rows,
            "q": q or "",
            "type_filter": kind or "all",
            "page": page,
            "pages": max(1, pages),
            "total": total,
        },
    )


@app.get("/item/{source_id}", response_class=HTMLResponse)
async def item_page(request: Request, source_id: str) -> Any:
    row = await run_in_threadpool(db.get_media_item, source_id)
    if not row:
        raise HTTPException(status_code=404, detail="Not found")
    sub = Path(row["file_path"]).name
    audio_dir = "audio" if row["media_type"] == "audio" else "video"
    media_url = f"/media/{audio_dir}/{sub}"
    thumb_url = None
    if row.get("thumbnail_path"):
        thumb_url = f"/thumbs/{Path(row['thumbnail_path']).name}"
    track_url = None
    track_label = None
    if row.get("subs_path") and os.path.isfile(row["subs_path"]):
        track_url = f"/subs/{Path(row['subs_path']).name}"
        track_label = "English"
    return templates.TemplateResponse(
        request,
        "item.html",
        {
            "item": row,
            "media_url": media_url,
            "thumb_url": thumb_url,
            "track_url": track_url,
            "track_label": track_label,
        },
    )


@app.post("/item/{source_id}/delete")
async def item_delete(source_id: str) -> RedirectResponse:
    row = await run_in_threadpool(db.get_media_item, source_id)
    if not row:
        raise HTTPException(status_code=404, detail="Not found")

    def _unlink() -> None:
        for key in ("file_path", "thumbnail_path", "subs_path", "info_json_path"):
            p = row.get(key)
            if p and isinstance(p, str):
                try:
                    Path(p).unlink(missing_ok=True)
                except OSError:
                    pass

    await run_in_threadpool(_unlink)
    await run_in_threadpool(db.delete_media_item, source_id)
    return RedirectResponse("/library", status_code=303)


@app.get("/search", response_class=HTMLResponse)
async def search_page(
    request: Request,
    q: Optional[str] = None,
    order: Optional[str] = None,
) -> Any:
    results = []
    search_error: Optional[str] = None
    order = order if order in ("relevance", "date") else "relevance"
    if q and q.strip():
        if not config.YOUTUBE_API_KEY:
            search_error = "YouTube API key not configured. Set YOUTUBE_API_KEY in your environment."
        else:
            try:
                results = await searcher.search_videos(q.strip(), order=order)
            except searcher.SearchError as exc:
                search_error = str(exc)
    return templates.TemplateResponse(
        request,
        "search.html",
        {"q": q or "", "results": results, "search_error": search_error, "order": order},
    )


# Byte-range–capable static files so <video>/<audio> work on :8080 without Caddy.
# When using Caddy on :8081, it serves these paths first; this mount is unused for /media then.
_media_root = Path(config.MEDIA_ROOT).resolve()
_thumbs_dir = (Path(config.META_ROOT) / "thumbs").resolve()
_subs_dir = (Path(config.META_ROOT) / "subs").resolve()
app.mount("/media", StaticFiles(directory=str(_media_root)), name="media")
app.mount("/thumbs", StaticFiles(directory=str(_thumbs_dir)), name="thumbs")
app.mount("/subs", StaticFiles(directory=str(_subs_dir)), name="subs")
