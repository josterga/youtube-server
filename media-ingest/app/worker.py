"""Single-process job worker: poll DB, run yt-dlp, update state."""

from __future__ import annotations

import sys
import time
import traceback

from app import config, db, downloader
from yt_dlp.utils import DownloadError


def _ensure_dirs() -> None:
    from pathlib import Path

    Path(config.MEDIA_ROOT).mkdir(parents=True, exist_ok=True)
    Path(config.META_ROOT).mkdir(parents=True, exist_ok=True)
    Path(config.TMP_DIR).mkdir(parents=True, exist_ok=True)
    Path(config.DB_PATH).parent.mkdir(parents=True, exist_ok=True)


def _log(job_id: str, line: str) -> None:
    db.append_job_log(job_id, line)


def process_job(job_row: dict) -> None:
    job_id = job_row["job_id"]
    source_url = job_row["source_url"]
    media_type = job_row["media_type"]

    try:
        def sink(msg: str) -> None:
            _log(job_id, msg)

        info = downloader.fetch_info(source_url)
        source_id = info.get("id")
        if not source_id:
            db.fail_job(job_id, "failed", "Could not resolve video id")
            return

        if db.media_item_exists(source_id):
            db.fail_job(job_id, "failed_duplicate", "Already in library")
            return

        duration = info.get("duration")
        if config.MAX_DURATION_SEC > 0 and duration is not None and float(duration) > config.MAX_DURATION_SEC:
            db.fail_job(
                job_id,
                "failed_duration_exceeded",
                f"Duration {int(duration)}s exceeds limit of {config.MAX_DURATION_SEC}s",
            )
            return

        try:
            downloader.download(
                source_url, media_type, log_hook=sink, source_id=source_id
            )
        except DownloadError as e:
            if downloader.is_no_compatible_format_error(e):
                db.fail_job(
                    job_id,
                    "failed_no_compatible_format",
                    "No browser-compatible format available for this video.",
                    log_extra=str(e) + "\n",
                )
            else:
                db.fail_job(job_id, "failed", str(e), log_extra=str(e) + "\n")
            downloader.cleanup_tmp(source_id)
            return
        except Exception as e:
            db.fail_job(job_id, "failed", str(e), log_extra=traceback.format_exc())
            downloader.cleanup_tmp(source_id)
            return

        try:
            paths_meta = downloader.move_to_final(source_id, media_type, info)
        except Exception as e:
            db.fail_job(job_id, "failed", str(e), log_extra=traceback.format_exc())
            downloader.cleanup_tmp(source_id)
            return

        merged_info = dict(info)
        merged_info.update(
            downloader.load_info_json(paths_meta.get("info_json_path"))
        )

        title = merged_info.get("title") or "untitled"
        uploader = merged_info.get("uploader")
        publish_date = merged_info.get("upload_date") or merged_info.get("release_date")
        if publish_date and len(str(publish_date)) == 8:
            pd = str(publish_date)
            publish_date = f"{pd[0:4]}-{pd[4:6]}-{pd[6:8]}"
        dur = merged_info.get("duration")
        duration_sec = int(dur) if dur is not None else None

        db.create_media_item(
            source_id=source_id,
            title=str(title),
            uploader=str(uploader) if uploader else None,
            duration_sec=duration_sec,
            publish_date=str(publish_date) if publish_date else None,
            media_type=media_type,
            file_path=paths_meta["file_path"],
            file_ext=paths_meta["file_ext"],
            filesize_bytes=paths_meta.get("filesize_bytes"),
            thumbnail_path=paths_meta.get("thumbnail_path"),
            subs_path=paths_meta.get("subs_path"),
            info_json_path=paths_meta.get("info_json_path"),
            source_url=source_url,
        )

        downloader.cleanup_tmp(source_id)
        db.update_job_status(
            job_id,
            "completed",
            finished_at=db.utcnow_iso(),
            media_item_id=source_id,
        )
    except Exception as e:
        db.fail_job(job_id, "failed", str(e), log_extra=traceback.format_exc())
        try:
            sid = locals().get("source_id")
            if sid:
                downloader.cleanup_tmp(str(sid))
        except Exception:
            pass


def main() -> None:
    db.init_db()
    _ensure_dirs()
    n = db.requeue_interrupted_jobs()
    if n:
        print(f"Re-queued {n} interrupted job(s)", file=sys.stderr)

    while True:
        try:
            job = db.claim_next_pending_job()
            if not job:
                time.sleep(0.75)
                continue
            process_job(job)
        except KeyboardInterrupt:
            raise
        except Exception:
            traceback.print_exc()
            time.sleep(2)


if __name__ == "__main__":
    main()
