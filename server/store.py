import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

_lock = threading.Lock()


def db_path() -> str:
    directory = os.getenv("DATA_DIR", "data")
    os.makedirs(directory, exist_ok=True)
    return os.path.join(directory, "applications.sqlite3")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(db_path())
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS applications (
            id TEXT PRIMARY KEY,
            exchange TEXT NOT NULL,
            uid TEXT NOT NULL,
            qq TEXT,
            telegram TEXT,
            source TEXT,
            source_other TEXT,
            status TEXT NOT NULL,
            relation TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    return conn


def save_application(row: dict) -> None:
    with _lock:
        conn = _connect()
        try:
            conn.execute(
                """
                INSERT INTO applications (
                    id, exchange, uid, qq, telegram, source, source_other,
                    status, relation, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["id"],
                    row["exchange"],
                    row["uid"],
                    row.get("qq") or "",
                    row.get("telegram") or "",
                    row.get("source") or "",
                    row.get("source_other") or "",
                    row["status"],
                    row.get("relation") or "",
                    row["created_at"],
                ),
            )
            conn.commit()
        finally:
            conn.close()


def reuse_recent(exchange: str, uid: str, contact: dict, within_seconds: int = 600) -> dict | None:
    """Return a recent verified/rejected result and refresh the contact fields."""
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=within_seconds)
    with _lock:
        conn = _connect()
        try:
            found = conn.execute(
                """
                SELECT * FROM applications
                WHERE exchange = ? AND uid = ? AND status IN ('verified', 'rejected')
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (exchange, uid),
            ).fetchone()
            if found is None:
                return None
            created = datetime.fromisoformat(found["created_at"])
            if created < cutoff:
                return None
            conn.execute(
                """
                UPDATE applications
                SET qq = ?, telegram = ?, source = ?, source_other = ?
                WHERE id = ?
                """,
                (
                    contact.get("qq") or "",
                    contact.get("telegram") or "",
                    contact.get("source") or "",
                    contact.get("source_other") or "",
                    found["id"],
                ),
            )
            conn.commit()
            return {
                "id": found["id"],
                "status": found["status"],
                "relation": found["relation"] or "",
            }
        finally:
            conn.close()


def list_applications(limit: int = 200) -> list[dict]:
    with _lock:
        conn = _connect()
        try:
            rows = conn.execute(
                """
                SELECT id, exchange, uid, qq, telegram, source, source_other,
                       status, relation, created_at
                FROM applications
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()
