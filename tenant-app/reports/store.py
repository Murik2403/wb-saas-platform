"""SQLite storage for scheduled report definitions and their generated runs.

Separate schema from agents/store.py -- reports have nothing to do with
marketplace candidates -- but same idiom: point at config.DB_PATH (the
tenant's own wb_dashboard.sqlite3), CREATE TABLE IF NOT EXISTS on init.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterator
from zoneinfo import ZoneInfo


LEASE_SECONDS = 300
MAX_ATTEMPTS = 3
_ABANDONED = "Сборка была прервана и повторена автоматически"
_POISON = "Сборка прервана {n} раза подряд (возможно, нехватка памяти). Запустите отчёт вручную или уменьшите период."


def _lease_until(seconds: int) -> str:
    return (datetime.now(ZoneInfo("Europe/Moscow")).replace(tzinfo=None) + timedelta(seconds=seconds)).isoformat(timespec="seconds")


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
        self._owned_leases: dict[int, tuple[str, int]] = {}
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
            # Durable lease: who is building this run and until when. attempts caps a run that keeps killing its worker.
            for column, ddl in (("attempts", "INTEGER NOT NULL DEFAULT 0"), ("lease_owner", "TEXT"), ("lease_expires_at", "TEXT")):
                _ensure_column(conn, "report_runs", column, f"ALTER TABLE report_runs ADD COLUMN {column} {ddl}")

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

    def start_run(self, definition_id: int, *, delivery_requested: bool = False, owner: str = "direct") -> int:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO report_runs (definition_id, started_at, status, delivery_requested, delivery_status, attempts, lease_owner, lease_expires_at) "
                "VALUES (?, ?, 'running', ?, ?, 1, ?, ?)",
                (definition_id, _now_msk_naive_iso(), int(delivery_requested), "queued" if delivery_requested else "not_requested",
                 owner, _lease_until(LEASE_SECONDS)),
            )
            run_id = cur.lastrowid
            self._owned_leases[run_id] = (owner, 1)
            return run_id

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

    def claim_next_queued(self, owner: str = "worker", lease_seconds: int = LEASE_SECONDS) -> dict | None:
        """Atomically claim the oldest queued report for one worker and lease it.

        The lease is renewed by ``heartbeat`` while the report is built; if the worker dies it expires and
        ``requeue_expired_leases`` puts the run back, so a crash never strands a run in "running".
        """
        with self._conn() as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """SELECT r.id AS run_id, r.period_days, r.delivery_requested, r.attempts AS previous_attempts,
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
                   SET status = 'running', started_at = ?, finished_at = NULL, error = NULL,
                       attempts = attempts + 1, lease_owner = ?, lease_expires_at = ?
                   WHERE id = ? AND status = 'queued'""",
                (_now_msk_naive_iso(), owner, _lease_until(lease_seconds), int(row["run_id"])),
            )
            job = dict(row)
            self._owned_leases[int(row["run_id"])] = (owner, int(job.pop("previous_attempts")) + 1)
            return job

    def has_queued(self) -> bool:
        with self._conn() as conn:
            return conn.execute("SELECT 1 FROM report_runs WHERE status = 'queued' LIMIT 1").fetchone() is not None

    def has_pending_generation(self, definition_id: int) -> bool:
        with self._conn() as conn:
            return conn.execute("SELECT 1 FROM report_runs WHERE definition_id=? AND status IN ('queued','running') LIMIT 1",
                                (definition_id,)).fetchone() is not None

    def lease_token(self, run_id: int) -> tuple[str, int]:
        """Use the captured claim token; adopt only a genuinely pre-lease running row."""
        if run_id in self._owned_leases:
            return self._owned_leases[run_id]
        with self._conn() as conn:
            owner = "legacy-direct"
            cur = conn.execute(
                "UPDATE report_runs SET lease_owner=?, attempts=1, lease_expires_at=? "
                "WHERE id=? AND status='running' AND attempts=0 AND lease_owner IS NULL AND lease_expires_at IS NULL",
                (owner, _lease_until(LEASE_SECONDS), run_id),
            )
            if cur.rowcount != 1:
                raise RuntimeError("Report lease is not owned by this builder")
        self._owned_leases[run_id] = (owner, 1)
        return self._owned_leases[run_id]

    def heartbeat(self, run_id: int, owner: str | None = None, lease_seconds: int = LEASE_SECONDS,
                  *, attempt: int | None = None) -> bool:
        """Extend the lease of a running run (of this owner when given); False when the run is no longer running."""
        captured = self._owned_leases.get(run_id)
        if owner is None:
            if captured is None:
                return False
            owner = captured[0]
        if attempt is None:
            if captured is None:
                return False
            attempt = captured[1]
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE report_runs SET lease_expires_at = ? WHERE id = ? AND status = 'running' AND lease_owner = ? AND attempts = ?",
                (_lease_until(lease_seconds), run_id, owner, attempt),
            )
            return cur.rowcount == 1

    def _recover(self, conn: sqlite3.Connection, where: str, params: tuple = ()) -> int:
        """Requeue interrupted runs; a run that already used up its attempts is closed with an error instead."""
        # Match ordinary generation-error policy: after three worker crashes,
        # an automatic due schedule must not create a new run and restart the
        # same poison cycle. Explicit manual requests remain available.
        conn.execute(
            f"UPDATE report_definitions SET last_run_at=? WHERE id IN "
            f"(SELECT definition_id FROM report_runs WHERE status='running' AND attempts>=? AND ({where}))",
            (_now_msk_naive_iso(), MAX_ATTEMPTS, *params),
        )
        poisoned = conn.execute(
            f"UPDATE report_runs SET status='error', finished_at=?, lease_owner=NULL, lease_expires_at=NULL, error=? "
            f"WHERE status='running' AND attempts >= ? AND ({where})",
            (_now_msk_naive_iso(), _POISON.format(n=MAX_ATTEMPTS), MAX_ATTEMPTS, *params),
        ).rowcount
        requeued = conn.execute(
            f"UPDATE report_runs SET status='queued', finished_at=NULL, lease_owner=NULL, lease_expires_at=NULL, error=? "
            f"WHERE status='running' AND ({where})",
            (_ABANDONED, *params),
        ).rowcount
        return int(requeued)

    def requeue_expired_leases(self, now: str | None = None) -> int:
        """Recover runs whose owner stopped renewing the lease (worker or container died) without waiting for a restart."""
        with self._conn() as conn:
            return self._recover(conn, "lease_expires_at IS NOT NULL AND lease_expires_at < ?", (now or _now_msk_naive_iso(),))

    def requeue_interrupted_runs(self) -> int:
        """Recover jobs abandoned when the container or worker restarted."""
        with self._conn() as conn:
            # A provider may have accepted the previous request. Do not resend blindly.
            conn.execute("UPDATE report_runs SET delivery_status='error', delivery_error='Доставка прервана. Проверьте получение перед повторной отправкой.' WHERE status='ok' AND delivery_status='queued'")
            return self._recover(conn, "lease_expires_at IS NULL OR lease_expires_at < ?", (_now_msk_naive_iso(),))

    def finish_run(self, run_id: int, *, status: str, file_path: str | None = None, error: str | None = None,
                   owner: str | None = None, attempt: int | None = None) -> bool:
        captured = self._owned_leases.get(run_id)
        if owner is None:
            if captured is None:
                return False
            owner = captured[0]
        if attempt is None:
            if captured is None:
                return False
            attempt = captured[1]
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE report_runs SET finished_at = ?, status = ?, file_path = ?, error = ?, lease_owner = NULL, lease_expires_at = NULL "
                "WHERE id = ? AND status='running' AND lease_owner = ? AND attempts = ?",
                (_now_msk_naive_iso(), status, file_path, error, run_id, owner, attempt),
            )
            return cur.rowcount == 1

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
