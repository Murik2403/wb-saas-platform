"""SQLite storage for agent recommendations/decisions/audit trail.

Same schema is used standalone (own .sqlite3 file) and inside a
MARKETSHELPER tenant container (would live alongside wb_dashboard.sqlite3
once wired into db/core.py in a later phase).
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    agent TEXT NOT NULL,           -- "price" | "ad"
    marketplace TEXT NOT NULL,     -- "wb" | "ozon"
    target_id TEXT NOT NULL,       -- sku or campaign_id
    target_name TEXT,
    action TEXT NOT NULL,          -- "set_price" | "set_budget" | "pause" | "resume"
    current_value TEXT,
    proposed_value TEXT,
    reason TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'  -- "pending" | "applied" | "rejected"
);

CREATE TABLE IF NOT EXISTS agent_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id INTEGER NOT NULL REFERENCES agent_candidates(id),
    decided_at TEXT NOT NULL,
    decided_by TEXT NOT NULL,      -- "human" | "auto"
    outcome TEXT NOT NULL          -- "applied" | "rejected"
);

CREATE TABLE IF NOT EXISTS agent_audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    agent TEXT NOT NULL,
    marketplace TEXT NOT NULL,
    dry_run INTEGER NOT NULL,
    ok INTEGER NOT NULL,
    detail TEXT NOT NULL
);
"""


class AgentStore:
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

    def add_candidate(
        self, *, agent: str, marketplace: str, target_id: str, target_name: str | None,
        action: str, current_value: Any, proposed_value: Any, reason: str,
    ) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO agent_candidates
                   (created_at, agent, marketplace, target_id, target_name, action,
                    current_value, proposed_value, reason, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')""",
                (
                    datetime.now(timezone.utc).isoformat(), agent, marketplace, target_id, target_name,
                    action, json.dumps(current_value), json.dumps(proposed_value), reason,
                ),
            )
            return cur.lastrowid

    def list_candidates(self, *, status: str | None = "pending") -> list[dict]:
        query = "SELECT * FROM agent_candidates"
        params: tuple = ()
        if status:
            query += " WHERE status = ?"
            params = (status,)
        query += " ORDER BY created_at DESC"
        with self._conn() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(query, params).fetchall()
            return [dict(r) for r in rows]

    def record_decision(self, candidate_id: int, *, decided_by: str, outcome: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE agent_candidates SET status = ? WHERE id = ?", (outcome, candidate_id)
            )
            conn.execute(
                "INSERT INTO agent_decisions (candidate_id, decided_at, decided_by, outcome) VALUES (?, ?, ?, ?)",
                (candidate_id, datetime.now(timezone.utc).isoformat(), decided_by, outcome),
            )

    def log_apply(self, *, agent: str, marketplace: str, dry_run: bool, ok: bool, detail: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO agent_audit_log (at, agent, marketplace, dry_run, ok, detail) VALUES (?, ?, ?, ?, ?, ?)",
                (datetime.now(timezone.utc).isoformat(), agent, marketplace, int(dry_run), int(ok), detail),
            )
