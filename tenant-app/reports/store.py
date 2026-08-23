"""SQLite storage for scheduled report definitions and their generated runs.

Separate schema from agents/store.py -- reports have nothing to do with
marketplace candidates -- but same idiom: point at config.DB_PATH (the
tenant's own wb_dashboard.sqlite3), CREATE TABLE IF NOT EXISTS on init.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator
from zoneinfo import ZoneInfo


def _now_msk_naive_iso() -> str:
    """Moscow-local naive timestamp -- this project's convention for every
    stored timestamp (server OS clock is UTC; readers assume МСК). See
    db/core.py's _now_msk_naive_iso for the same helper and its history."""
    return datetime.now(ZoneInfo("Europe/Moscow")).replace(tzinfo=None).isoformat(timespec="seconds")

SCHEMA = """
CREATE TABLE IF NOT EXISTS report_definitions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    metrics TEXT NOT NULL,          -- JSON list of metric codes, e.g. ["sales_orders","ads"]
    schedule_type TEXT NOT NULL,    -- "daily" | "weekly" | "monthly"
    schedule_time TEXT NOT NULL,    -- "HH:MM", МСК-naive (проектная конвенция)
    schedule_weekday INTEGER,       -- 0-6, только для weekly
    schedule_day INTEGER,           -- 1-31, только для monthly
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    last_run_at TEXT
);

CREATE TABLE IF NOT EXISTS report_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    definition_id INTEGER NOT NULL REFERENCES report_definitions(id),
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,           -- "ok" | "error"
    file_path TEXT,
    error TEXT
);
"""


class ReportStore:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def add_definition(
        self, *, name: str, metrics: list[str], schedule_type: str, schedule_time: str,
        schedule_weekday: int | None = None, schedule_day: int | None = None,
    ) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO report_definitions
                   (name, metrics, schedule_type, schedule_time, schedule_weekday, schedule_day, enabled, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, 1, ?)""",
                (name, json.dumps(metrics), schedule_type, schedule_time, schedule_weekday, schedule_day,
                 _now_msk_naive_iso()),
            )
            return cur.lastrowid

    def list_definitions(self, *, enabled_only: bool = False) -> list[dict]:
        query = "SELECT * FROM report_definitions"
        if enabled_only:
            query += " WHERE enabled = 1"
        query += " ORDER BY created_at DESC"
        with self._conn() as conn:
            conn.row_factory = sqlite3.Row
            return [dict(r) for r in conn.execute(query).fetchall()]

    def delete_definition(self, definition_id: int) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM report_definitions WHERE id = ?", (definition_id,))
            conn.execute("DELETE FROM report_runs WHERE definition_id = ?", (definition_id,))

    def set_last_run(self, definition_id: int, when: str) -> None:
        with self._conn() as conn:
            conn.execute("UPDATE report_definitions SET last_run_at = ? WHERE id = ?", (when, definition_id))

    def start_run(self, definition_id: int) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO report_runs (definition_id, started_at, status) VALUES (?, ?, 'running')",
                (definition_id, _now_msk_naive_iso()),
            )
            return cur.lastrowid

    def finish_run(self, run_id: int, *, status: str, file_path: str | None = None, error: str | None = None) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE report_runs SET finished_at = ?, status = ?, file_path = ?, error = ? WHERE id = ?",
                (_now_msk_naive_iso(), status, file_path, error, run_id),
            )

    def list_runs(self, *, definition_id: int | None = None, limit: int = 50) -> list[dict]:
        query = "SELECT * FROM report_runs"
        params: tuple[Any, ...] = ()
        if definition_id is not None:
            query += " WHERE definition_id = ?"
            params = (definition_id,)
        query += " ORDER BY started_at DESC LIMIT ?"
        params = params + (limit,)
        with self._conn() as conn:
            conn.row_factory = sqlite3.Row
            return [dict(r) for r in conn.execute(query, params).fetchall()]
