"""SQLite schema and queries."""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS media_items (
    id            TEXT PRIMARY KEY,
    title         TEXT NOT NULL,
    uploader      TEXT,
    duration_sec  INTEGER,
    publish_date  TEXT,
    media_type    TEXT NOT NULL,
    file_path     TEXT,
    file_ext      TEXT,
    filesize_bytes INTEGER,
    thumbnail_path TEXT,
    subs_path     TEXT,
    info_json_path TEXT,
    source_url    TEXT NOT NULL,
    added_at      TEXT NOT NULL,
    status        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    job_id        TEXT PRIMARY KEY,
    source_url    TEXT NOT NULL,
    media_type    TEXT NOT NULL,
    status        TEXT NOT NULL,
    media_item_id TEXT,
    submitted_at  TEXT NOT NULL,
    started_at    TEXT,
    finished_at   TEXT,
    error_message TEXT,
    log           TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_media_type ON media_items(media_type);
CREATE INDEX IF NOT EXISTS idx_media_added ON media_items(added_at DESC);
"""


def _connect() -> sqlite3.Connection:
    Path(config.DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.executescript(SCHEMA)


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def media_item_exists(source_id: str) -> bool:
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM media_items WHERE id = ?",
            (source_id,),
        ).fetchone()
        return row is not None


def create_job(source_url: str, media_type: str) -> str:
    job_id = str(uuid.uuid4())
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO jobs (
                job_id, source_url, media_type, status,
                submitted_at, log
            ) VALUES (?, ?, ?, 'submitted', ?, '')
            """,
            (job_id, source_url, media_type, utcnow_iso()),
        )
    return job_id


def get_job(job_id: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
    return dict(row) if row else None


def claim_next_pending_job() -> dict[str, Any] | None:
    """Atomically pick the oldest pending job and mark it running."""
    now = utcnow_iso()
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """
            SELECT * FROM jobs
            WHERE status IN ('submitted', 'queued')
            ORDER BY submitted_at ASC
            LIMIT 1
            """
        ).fetchone()
        if not row:
            conn.commit()
            return None
        job_id = row["job_id"]
        conn.execute(
            """
            UPDATE jobs
            SET status = 'running', started_at = ?
            WHERE job_id = ? AND status IN ('submitted', 'queued')
            """,
            (now, job_id),
        )
        conn.commit()
        row2 = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
    return dict(row2) if row2 else None


def update_job_status(
    job_id: str,
    status: str,
    *,
    started_at: str | None = None,
    finished_at: str | None = None,
    media_item_id: str | None = None,
    error_message: str | None = None,
) -> None:
    fields: list[str] = ["status = ?"]
    values: list[Any] = [status]
    if started_at is not None:
        fields.append("started_at = ?")
        values.append(started_at)
    if finished_at is not None:
        fields.append("finished_at = ?")
        values.append(finished_at)
    if media_item_id is not None:
        fields.append("media_item_id = ?")
        values.append(media_item_id)
    if error_message is not None:
        fields.append("error_message = ?")
        values.append(error_message)
    values.append(job_id)
    with _connect() as conn:
        conn.execute(
            f"UPDATE jobs SET {', '.join(fields)} WHERE job_id = ?",
            values,
        )


def append_job_log(job_id: str, text: str) -> None:
    with _connect() as conn:
        conn.execute(
            """
            UPDATE jobs SET log = COALESCE(log, '') || ?
            WHERE job_id = ?
            """,
            (text, job_id),
        )


def fail_job(job_id: str, status: str, message: str, log_extra: str = "") -> None:
    now = utcnow_iso()
    if log_extra:
        append_job_log(job_id, log_extra)
    update_job_status(job_id, status, finished_at=now, error_message=message)


def requeue_interrupted_jobs() -> int:
    with _connect() as conn:
        cur = conn.execute(
            """
            UPDATE jobs
            SET status = 'submitted', started_at = NULL
            WHERE status IN ('queued', 'running')
            """
        )
        return cur.rowcount


def list_jobs_recent(limit: int = 5) -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT j.*, m.duration_sec AS item_duration
            FROM jobs j
            LEFT JOIN media_items m ON j.media_item_id = m.id
            ORDER BY j.submitted_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def list_jobs_page(
    status_filter: str | None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    active_statuses = ("submitted", "queued", "running")
    where = ""
    params: list[Any] = []
    if status_filter == "active":
        placeholders = ",".join("?" * len(active_statuses))
        where = f"WHERE j.status IN ({placeholders})"
        params.extend(active_statuses)
    elif status_filter == "failed":
        where = "WHERE j.status LIKE 'failed%'"
    elif status_filter == "completed":
        where = "WHERE j.status = 'completed'"
    else:
        where = ""

    with _connect() as conn:
        count_row = conn.execute(
            f"SELECT COUNT(*) AS c FROM jobs j {where}",
            params,
        ).fetchone()
        total = int(count_row["c"]) if count_row else 0
        rows = conn.execute(
            f"""
            SELECT j.*, m.duration_sec AS item_duration
            FROM jobs j
            LEFT JOIN media_items m ON j.media_item_id = m.id
            {where}
            ORDER BY j.submitted_at DESC
            LIMIT ? OFFSET ?
            """,
            [*params, limit, offset],
        ).fetchall()
    return [dict(r) for r in rows], total


def delete_job(job_id: str) -> bool:
    with _connect() as conn:
        row = conn.execute(
            "SELECT status FROM jobs WHERE job_id = ?",
            (job_id,),
        ).fetchone()
        if not row:
            return False
        st = row["status"]
        if st in ("submitted", "queued", "running"):
            return False
        conn.execute("DELETE FROM jobs WHERE job_id = ?", (job_id,))
    return True


def reset_job_for_retry(job_id: str) -> bool:
    with _connect() as conn:
        row = conn.execute(
            "SELECT status FROM jobs WHERE job_id = ?",
            (job_id,),
        ).fetchone()
        if not row or not str(row["status"]).startswith("failed"):
            return False
        conn.execute(
            """
            UPDATE jobs SET
                status = 'submitted',
                error_message = NULL,
                started_at = NULL,
                finished_at = NULL,
                media_item_id = NULL,
                log = ''
            WHERE job_id = ?
            """,
            (job_id,),
        )
    return True


def create_media_item(
    source_id: str,
    title: str,
    uploader: str | None,
    duration_sec: int | None,
    publish_date: str | None,
    media_type: str,
    file_path: str,
    file_ext: str,
    filesize_bytes: int | None,
    thumbnail_path: str | None,
    subs_path: str | None,
    info_json_path: str | None,
    source_url: str,
) -> None:
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO media_items (
                id, title, uploader, duration_sec, publish_date,
                media_type, file_path, file_ext, filesize_bytes,
                thumbnail_path, subs_path, info_json_path,
                source_url, added_at, status
            ) VALUES (
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?,
                ?, ?, 'completed'
            )
            """,
            (
                source_id,
                title,
                uploader,
                duration_sec,
                publish_date,
                media_type,
                file_path,
                file_ext,
                filesize_bytes,
                thumbnail_path,
                subs_path,
                info_json_path,
                source_url,
                utcnow_iso(),
            ),
        )


def delete_media_item(source_id: str) -> bool:
    with _connect() as conn:
        row = conn.execute(
            "SELECT id FROM media_items WHERE id = ?",
            (source_id,),
        ).fetchone()
        if not row:
            return False
        conn.execute("DELETE FROM media_items WHERE id = ?", (source_id,))
    return True


def get_media_item(source_id: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM media_items WHERE id = ?",
            (source_id,),
        ).fetchone()
    return dict(row) if row else None


def list_media_items(
    q: str | None,
    media_type: str | None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    clauses = ["status = 'completed'"]
    params: list[Any] = []
    if media_type in ("audio", "video"):
        clauses.append("media_type = ?")
        params.append(media_type)
    if q:
        like = f"%{q}%"
        clauses.append("(title LIKE ? OR IFNULL(uploader, '') LIKE ?)")
        params.extend([like, like])
    where = " AND ".join(clauses)

    with _connect() as conn:
        count_row = conn.execute(
            f"SELECT COUNT(*) AS c FROM media_items WHERE {where}",
            params,
        ).fetchone()
        total = int(count_row["c"]) if count_row else 0
        rows = conn.execute(
            f"""
            SELECT * FROM media_items
            WHERE {where}
            ORDER BY added_at DESC
            LIMIT ? OFFSET ?
            """,
            [*params, limit, offset],
        ).fetchall()
    return [dict(r) for r in rows], total


def any_active_jobs() -> bool:
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT 1 FROM jobs
            WHERE status IN ('submitted', 'queued', 'running')
            LIMIT 1
            """
        ).fetchone()
    return row is not None
