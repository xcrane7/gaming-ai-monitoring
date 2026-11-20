"""Mémoire persistante SQLite (une connexion par thread, mode WAL)."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS videos (
    id INTEGER PRIMARY KEY,
    path TEXT NOT NULL,
    fingerprint TEXT NOT NULL UNIQUE,
    size INTEGER, mtime REAL,
    duration REAL, width INTEGER, height INTEGER, fps REAL, has_audio INTEGER,
    status TEXT NOT NULL DEFAULT 'new',
    analysis_path TEXT,
    added_at REAL NOT NULL, analyzed_at REAL
);
CREATE TABLE IF NOT EXISTS highlights (
    id INTEGER PRIMARY KEY,
    video_id INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    start REAL NOT NULL, end REAL NOT NULL, peak REAL NOT NULL,
    score REAL NOT NULL, label TEXT, source TEXT NOT NULL DEFAULT 'local', meta TEXT
);
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY,
    video_id INTEGER REFERENCES videos(id) ON DELETE CASCADE,
    kind TEXT NOT NULL DEFAULT 'single',
    status TEXT NOT NULL DEFAULT 'queued',
    stage TEXT, progress REAL DEFAULT 0, error TEXT, params TEXT,
    created_at REAL NOT NULL, started_at REAL, finished_at REAL
);
CREATE TABLE IF NOT EXISTS renders (
    id INTEGER PRIMARY KEY,
    job_id INTEGER REFERENCES jobs(id) ON DELETE SET NULL,
    video_id INTEGER REFERENCES videos(id) ON DELETE SET NULL,
    format TEXT NOT NULL, path TEXT NOT NULL,
    duration REAL, width INTEGER, height INTEGER,
    encoder TEXT, render_seconds REAL,
    qa_status TEXT, qa_report TEXT, plan TEXT,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS skills (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL, name TEXT NOT NULL, version TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'candidate',
    params TEXT, license TEXT, origin TEXT, score REAL, metrics TEXT,
    created_at REAL NOT NULL, activated_at REAL,
    UNIQUE(kind, name, version)
);
CREATE TABLE IF NOT EXISTS skill_history (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL, skill_id INTEGER, prev_skill_id INTEGER,
    action TEXT NOT NULL, reason TEXT, at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS discoveries (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL, name TEXT NOT NULL, version TEXT,
    license TEXT, license_ok INTEGER, url TEXT, summary TEXT, requires TEXT,
    status TEXT NOT NULL DEFAULT 'discovered',
    first_seen REAL NOT NULL, last_seen REAL NOT NULL,
    UNIQUE(source, name)
);
CREATE TABLE IF NOT EXISTS research_runs (
    id INTEGER PRIMARY KEY,
    started_at REAL NOT NULL, finished_at REAL, status TEXT NOT NULL, summary TEXT
);
CREATE TABLE IF NOT EXISTS benchmarks (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, at REAL NOT NULL, results TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY, at REAL NOT NULL, level TEXT NOT NULL,
    source TEXT NOT NULL, message TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ai_calls (
    id INTEGER PRIMARY KEY, at REAL NOT NULL, provider TEXT NOT NULL,
    purpose TEXT, allowed INTEGER NOT NULL, cost_class TEXT, detail TEXT
);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT);
CREATE INDEX IF NOT EXISTS idx_highlights_video ON highlights(video_id);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
"""


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._write_lock = threading.RLock()
        with self._write_lock:
            self.conn.executescript(SCHEMA)
            self.conn.commit()

    @property
    def conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(str(self.path), timeout=30)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=30000")
            self._local.conn = conn
        return conn

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    # --- primitives -------------------------------------------------------
    def execute(self, sql: str, params: Iterable[Any] = ()) -> int:
        with self._write_lock:
            cur = self.conn.execute(sql, tuple(params))
            self.conn.commit()
            return cur.lastrowid or cur.rowcount

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[dict]:
        return [dict(r) for r in self.conn.execute(sql, tuple(params)).fetchall()]

    def one(self, sql: str, params: Iterable[Any] = ()) -> dict | None:
        row = self.conn.execute(sql, tuple(params)).fetchone()
        return dict(row) if row else None

    def insert(self, table: str, **fields: Any) -> int:
        cols = ", ".join(fields)
        marks = ", ".join("?" for _ in fields)
        return self.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", fields.values())

    def update(self, table: str, row_id: int, **fields: Any) -> None:
        sets = ", ".join(f"{k}=?" for k in fields)
        self.execute(f"UPDATE {table} SET {sets} WHERE id=?", [*fields.values(), row_id])

    # --- clé/valeur ---------------------------------------------------------
    def kv_get(self, key: str, default: Any = None) -> Any:
        row = self.one("SELECT value FROM kv WHERE key=?", (key,))
        if not row:
            return default
        try:
            return json.loads(row["value"])
        except (TypeError, json.JSONDecodeError):
            return default

    def kv_set(self, key: str, value: Any) -> None:
        self.execute(
            "INSERT INTO kv(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value)),
        )

    # --- journal ------------------------------------------------------------
    def log(self, source: str, message: str, level: str = "info") -> None:
        self.insert("events", at=time.time(), level=level, source=source, message=message)
        print(f"[{time.strftime('%H:%M:%S')}] [{source}] {message}", flush=True)
