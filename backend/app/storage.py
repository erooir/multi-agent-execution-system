"""Small transactional JSON-record store. Never includes reference documents."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path


def now() -> str:
    return datetime.now(UTC).isoformat()


class Store:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        with self.connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS records (kind TEXT NOT NULL,id TEXT NOT NULL,data TEXT NOT NULL,updated_at TEXT NOT NULL,PRIMARY KEY(kind,id))"
            )

    def connect(self):
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def list(self, kind: str) -> list[dict]:
        with self.connect() as conn:
            return [
                json.loads(row[0])
                for row in conn.execute(
                    "SELECT data FROM records WHERE kind=? ORDER BY updated_at DESC", (kind,)
                )
            ]

    def get(self, kind: str, record_id: str) -> dict | None:
        with self.connect() as conn:
            row = conn.execute("SELECT data FROM records WHERE kind=? AND id=?", (kind, record_id)).fetchone()
            return json.loads(row[0]) if row else None

    def save(self, kind: str, item: dict, *, allow_cancelled_resume: bool = False) -> dict:
        with self.lock, self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            result = dict(item)
            result.setdefault("id", uuid.uuid4().hex[:16])
            if kind == "runs" and not allow_cancelled_resume:
                previous_row = conn.execute(
                    "SELECT data FROM records WHERE kind=? AND id=?", (kind, result["id"])
                ).fetchone()
                if previous_row:
                    previous = json.loads(previous_row[0])
                    if previous.get("status") == "cancelled" and result.get("status") != "cancelled":
                        return previous
            result.setdefault("created_at", now())
            result["updated_at"] = now()
            conn.execute(
                "INSERT INTO records(kind,id,data,updated_at) VALUES(?,?,?,?) ON CONFLICT(kind,id) DO UPDATE SET data=excluded.data,updated_at=excluded.updated_at",
                (
                    kind,
                    result["id"],
                    json.dumps(result, ensure_ascii=False, default=str),
                    result["updated_at"],
                ),
            )
            return result

    def delete(self, kind: str, record_id: str) -> bool:
        with self.lock, self.connect() as conn:
            return conn.execute("DELETE FROM records WHERE kind=? AND id=?", (kind, record_id)).rowcount > 0

    def audit(self, action: str, entity: str, detail, user="system"):
        return self.save("audits", {"action": action, "entity": entity, "detail": detail, "user": user})


ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("WORKBENCH_DATA_DIR", str(ROOT / ".local")))
store = Store(DATA_DIR / "workbench.sqlite3")
