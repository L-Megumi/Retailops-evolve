from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


class TaskStore:
    """SQLite task ledger with leased claims and append-only, resumable events."""

    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY, request TEXT NOT NULL, status TEXT NOT NULL,
                    created REAL NOT NULL, updated REAL NOT NULL, owner TEXT,
                    result TEXT, error TEXT);
                CREATE TABLE IF NOT EXISTS events (
                    task_id TEXT NOT NULL REFERENCES tasks(id), seq INTEGER NOT NULL,
                    payload TEXT NOT NULL, PRIMARY KEY(task_id, seq));
                CREATE INDEX IF NOT EXISTS task_status ON tasks(status, updated);
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=15000")
        try:
            with db:
                yield db
        finally:
            db.close()

    def create(self, request: dict) -> str:
        task_id, now = uuid.uuid4().hex, time.time()
        with self.connect() as db:
            db.execute("INSERT INTO tasks(id,request,status,created,updated) VALUES(?,?,?,?,?)",
                       (task_id, json.dumps(request, ensure_ascii=False), "queued", now, now))
        return task_id

    def get(self, task_id: str) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        for key in ("request", "result"):
            result[key] = json.loads(result[key]) if result[key] else None
        result.pop("owner", None)
        return result

    def queued(self) -> list[str]:
        with self.connect() as db:
            return [row[0] for row in db.execute("SELECT id FROM tasks WHERE status='queued' ORDER BY created LIMIT 256")]

    def claim(self, task_id: str, owner: str) -> bool:
        with self.connect() as db:
            return db.execute("UPDATE tasks SET status='running',owner=?,updated=? WHERE id=? AND status='queued'",
                              (owner, time.time(), task_id)).rowcount == 1

    def heartbeat(self, task_id: str, owner: str) -> None:
        with self.connect() as db:
            db.execute("UPDATE tasks SET updated=? WHERE id=? AND owner=? AND status='running'",
                       (time.time(), task_id, owner))

    def append(self, task_id: str, payload: dict, owner: str | None = None) -> None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status,owner FROM tasks WHERE id=?", (task_id,)).fetchone()
            if row is None or (owner is not None and (row["owner"] != owner or row["status"] != "running")):
                raise RuntimeError("task event writer does not own an active lease")
            seq = db.execute("SELECT COALESCE(MAX(seq),0)+1 FROM events WHERE task_id=?", (task_id,)).fetchone()[0]
            db.execute("INSERT INTO events VALUES(?,?,?)", (task_id, seq, json.dumps(payload, ensure_ascii=False)))

    def events(self, task_id: str, after: int = 0) -> list[dict]:
        with self.connect() as db:
            rows = db.execute("SELECT seq,payload FROM events WHERE task_id=? AND seq>? ORDER BY seq LIMIT 256",
                              (task_id, after)).fetchall()
        return [{"id": row["seq"], "event": json.loads(row["payload"])} for row in rows]

    def finish(self, task_id: str, owner: str, *, result: dict | None = None,
               error: str | None = None) -> bool:
        status = "succeeded" if result is not None and result.get("ok") else "failed"
        with self.connect() as db:
            return db.execute("UPDATE tasks SET status=?,result=?,error=?,updated=? WHERE id=? AND owner=? AND status='running'",
                              (status, json.dumps(result, ensure_ascii=False) if result else None,
                               error, time.time(), task_id, owner)).rowcount == 1

    def recover_stale(self, lease_seconds: float = 120) -> int:
        # An interrupted job may have published a capability: never blindly replay it.
        with self.connect() as db:
            return db.execute("UPDATE tasks SET status='interrupted',owner=NULL,error=?,updated=? "
                              "WHERE status='running' AND updated<?",
                              ("worker lease expired; inspect trace before retrying", time.time(),
                               time.time() - lease_seconds)).rowcount
