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
            created_at TEXT NOT NULL,
            updated_at TEXT
        )
        """
    )
    columns = {row[1] for row in conn.execute("PRAGMA table_info(applications)")}
    if "updated_at" not in columns:
        conn.execute("ALTER TABLE applications ADD COLUMN updated_at TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_applications_lookup ON applications(exchange, uid, created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_applications_status ON applications(status, created_at)")
    return conn


def _optional(value) -> str | None:
    text = str(value or "").strip()
    return text or None


def save_application(row: dict) -> None:
    with _lock:
        conn = _connect()
        try:
            conn.execute(
                """
                INSERT INTO applications (
                    id, exchange, uid, qq, telegram, source, source_other,
                    status, relation, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["id"],
                    row["exchange"],
                    row["uid"],
                    _optional(row.get("qq")),
                    _optional(row.get("telegram")),
                    _optional(row.get("source")),
                    _optional(row.get("source_other")),
                    row["status"],
                    row.get("relation") or "",
                    row["created_at"],
                    row.get("updated_at") or row["created_at"],
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
                SET qq = ?, telegram = ?, source = ?, source_other = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    _optional(contact.get("qq")),
                    _optional(contact.get("telegram")),
                    _optional(contact.get("source")),
                    _optional(contact.get("source_other")),
                    datetime.now(timezone.utc).isoformat(),
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


def query_applications(
    exchange: str = "",
    status: str = "",
    uid: str = "",
    q: str = "",
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """Filter stored applications for a later admin console."""
    limit = min(max(int(limit), 1), 200)
    offset = max(int(offset), 0)
    clauses: list[str] = []
    params: list[str] = []
    if exchange:
        clauses.append("exchange = ?")
        params.append(exchange)
    if status:
        clauses.append("status = ?")
        params.append(status)
    if uid:
        clauses.append("uid = ?")
        params.append(uid)
    if q:
        clauses.append("(id LIKE ? OR uid LIKE ? OR IFNULL(qq, '') LIKE ? OR IFNULL(telegram, '') LIKE ? OR IFNULL(source, '') LIKE ?)")
        like = f"%{q.replace('%', '').replace('_', '')}%"
        params.extend([like, like, like, like, like])
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    with _lock:
        conn = _connect()
        try:
            total = conn.execute(f"SELECT COUNT(*) FROM applications{where}", params).fetchone()[0]
            rows = conn.execute(
                f"""
                SELECT id, exchange, uid, qq, telegram, source, source_other,
                       status, relation, created_at, updated_at
                FROM applications
                {where}
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?
                """,
                [*params, limit, offset],
            ).fetchall()
            return {"total": total, "applications": [dict(row) for row in rows]}
        finally:
            conn.close()
