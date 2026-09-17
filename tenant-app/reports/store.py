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


def _ensure_column(conn: sqlite3.Connection, table: str, name: str, ddl: str) -> None:
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if name in existing:
        return
    try:
        conn.execute(ddl)
    except sqlite3.OperationalError as exc:
        # Parallel startup can execute schema upgrades twice.
        if "duplicate column name" not in str(exc).lower():
            raise

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
    email_enabled INTEGER NOT NULL DEFAULT 0,     -- также отправлять на email аккаунта при генерации
    telegram_enabled INTEGER NOT NULL DEFAULT 0,  -- также отправлять в привязанный Telegram-чат
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
    error TEXT,
    period_days INTEGER,
    delivery_requested INTEGER NOT NULL DEFAULT 0,
    delivery_status TEXT NOT NULL DEFAULT 'not_requested',
    delivery_error TEXT
);
"""


class ReportStore:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.executescript(SCHEMA)
            # CREATE TABLE IF NOT EXISTS above is a no-op against a
            # report_definitions table that predates email_enabled -- needs
            # a real ALTER TABLE for upgrades in place (same pattern as
            # gateway/logic/db.py's pdn_consent_at migration).
            _ensure_column(
                conn,
                "report_definitions",
                "email_enabled",
                "ALTER TABLE report_definitions ADD COLUMN email_enabled INTEGER NOT NULL DEFAULT 0",
            )
            _ensure_column(
                conn,
                "report_definitions",
                "telegram_enabled",
                "ALTER TABLE report_definitions ADD COLUMN telegram_enabled INTEGER NOT NULL DEFAULT 0",
            )
            _ensure_column(
                conn,
                "report_runs",
                "period_days",
                "ALTER TABLE report_runs ADD COLUMN period_days INTEGER",
            )
            _ensure_column(
                conn,
                "report_runs",
                "delivery_requested",
                "ALTER TABLE report_runs ADD COLUMN delivery_requested INTEGER NOT NULL DEFAULT 0",
            )
            _ensure_column(
                conn,
                "report_runs",
                "delivery_status",
                "ALTER TABLE report_runs ADD COLUMN delivery_status TEXT NOT NULL DEFAULT 'not_requested'",
            )
            _ensure_column(
                conn,
                "report_runs",
                "delivery_error",
                "ALTER TABLE report_runs ADD COLUMN delivery_error TEXT",
            )

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
        email_enabled: bool = False, telegram_enabled: bool = False,
    ) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO report_definitions
                   (name, metrics, schedule_type, schedule_time, schedule_weekday, schedule_day, enabled,
                    email_enabled, telegram_enabled, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?)""",
                (name, json.dumps(metrics), schedule_type, schedule_time, schedule_weekday, schedule_day,
                 int(email_enabled), int(telegram_enabled), _now_msk_naive_iso()),
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

    def update_definition(self, definition_id: int, *, name: str, metrics: list[str], schedule_type: str, schedule_time: str,
                          schedule_weekday: int | None = None, schedule_day: int | None = None,
                          email_enabled: bool = False, telegram_enabled: bool = False) -> None:
        with self._conn() as conn:
            cur = conn.execute(
                """UPDATE report_definitions SET name = ?, metrics = ?, schedule_type = ?, schedule_time = ?,
                   schedule_weekday = ?, schedule_day = ?, email_enabled = ?, telegram_enabled = ? WHERE id = ?""",
                (name, json.dumps(metrics), schedule_type, schedule_time, schedule_weekday, schedule_day,
                 int(email_enabled), int(telegram_enabled), definition_id),
            )
            if cur.rowcount == 0:
                raise KeyError(definition_id)

    def set_last_run(self, definition_id: int, when: str) -> None:
        with self._conn() as conn:
            conn.execute("UPDATE report_definitions SET last_run_at = ? WHERE id = ?", (when, definition_id))

    def set_enabled(self, definition_id: int, enabled: bool) -> None:
        with self._conn() as conn:
            cur = conn.execute("UPDATE report_definitions SET enabled = ? WHERE id = ?", (int(enabled), definition_id))
            if cur.rowcount == 0:
                raise KeyError(definition_id)

    def start_run(self, definition_id: int, *, delivery_requested: bool = False) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO report_runs (definition_id, started_at, status, delivery_requested, delivery_status) VALUES (?, ?, 'running', ?, ?)",
                (definition_id, _now_msk_naive_iso(), int(delivery_requested), "queued" if delivery_requested else "not_requested"),
            )
            return cur.lastrowid

    def enqueue_run(self, definition_id: int, *, period_days: int | None = None, delivery_requested: bool = False) -> int:
        """Persist a manual generation request for the report worker.

        The web process only enqueues work, so a large report cannot exhaust
        the API process and leave the browser waiting on a dead request.
        """
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT id FROM report_runs WHERE definition_id=? AND period_days IS ? AND delivery_requested=? AND (status IN ('queued','running') OR (status='ok' AND delivery_status='queued')) ORDER BY id DESC LIMIT 1",
                (definition_id, period_days, int(delivery_requested)),
            ).fetchone()
            if existing:
                return int(existing[0])
            cur = conn.execute(
                """INSERT INTO report_runs
                   (definition_id, started_at, status, period_days, delivery_requested, delivery_status)
                   VALUES (?, ?, 'queued', ?, ?, ?)""",
                (
                    definition_id,
                    _now_msk_naive_iso(),
                    period_days,
                    int(delivery_requested),
                    "queued" if delivery_requested else "not_requested",
                ),
            )
            return cur.lastrowid

    def claim_next_queued(self) -> dict | None:
        """Atomically claim the oldest queued report for one worker."""
        with self._conn() as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """SELECT r.id AS run_id, r.period_days, r.delivery_requested,
                          d.*
                   FROM report_runs r
                   JOIN report_definitions d ON d.id = r.definition_id
                   WHERE r.status = 'queued'
                   ORDER BY r.id ASC
                   LIMIT 1"""
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                """UPDATE report_runs
                   SET status = 'running', started_at = ?, finished_at = NULL, error = NULL
                   WHERE id = ? AND status = 'queued'""",
                (_now_msk_naive_iso(), int(row["run_id"])),
            )
            return dict(row)

    def requeue_interrupted_runs(self) -> int:
        """Recover jobs abandoned when the container or worker restarted."""
        with self._conn() as conn:
            # A provider may have accepted the previous request. Do not resend blindly.
            conn.execute("UPDATE report_runs SET delivery_status='error', delivery_error='Доставка прервана. Проверьте получение перед повторной отправкой.' WHERE status='ok' AND delivery_status='queued'")
            cur = conn.execute(
                """UPDATE report_runs
                   SET status = 'queued', finished_at = NULL,
                       error = 'Предыдущая сборка была прервана; запуск повторён автоматически'
                   WHERE status = 'running'"""
            )
            return int(cur.rowcount)

    def finish_run(self, run_id: int, *, status: str, file_path: str | None = None, error: str | None = None) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE report_runs SET finished_at = ?, status = ?, file_path = ?, error = ? WHERE id = ?",
                (_now_msk_naive_iso(), status, file_path, error, run_id),
            )

    def finish_delivery(self, run_id: int, *, status: str, error: str | None = None) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE report_runs SET delivery_status = ?, delivery_error = ? WHERE id = ?",
                (status, error, run_id),
            )

    def list_runs(self, *, definition_id: int | None = None, limit: int = 50) -> list[dict]:
        query = """SELECT report_runs.*, report_definitions.name AS definition_name,
                          report_definitions.metrics AS definition_metrics
                   FROM report_runs
                   LEFT JOIN report_definitions ON report_definitions.id = report_runs.definition_id"""
        params: tuple[Any, ...] = ()
        if definition_id is not None:
            query += " WHERE report_runs.definition_id = ?"
            params = (definition_id,)
        query += " ORDER BY report_runs.id DESC LIMIT ?"
        params = params + (limit,)
        with self._conn() as conn:
            conn.row_factory = sqlite3.Row
            return [dict(r) for r in conn.execute(query, params).fetchall()]
