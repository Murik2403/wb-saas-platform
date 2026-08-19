"""Shared test scaffolding for the money-critical db/ layer.

Deliberately stdlib-only (unittest, sqlite3, tempfile) so it runs with a plain
``python -m unittest discover`` even where pytest/streamlit cannot be
installed (no network access to PyPI). The same test modules also collect
fine under pytest if it's available in your environment -- pytest happily
runs unittest.TestCase subclasses.

Each test gets its own throwaway sqlite file with a freshly-initialized
schema (via db.core.init_db()), so tests never touch data/wb_dashboard.sqlite3
and can't leak state into each other.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import db.core as core  # noqa: E402


class DbTestCase(unittest.TestCase):
    """Base class: fresh isolated sqlite db per test, schema initialized."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "test_wb_dashboard.sqlite3"
        self._original_db_path = core.DB_PATH
        core.DB_PATH = self.db_path
        core.init_db()

    def tearDown(self) -> None:
        core.DB_PATH = self._original_db_path
        self._tmpdir.cleanup()

    # -- small helpers shared by several test modules -----------------
    def now(self) -> str:
        return datetime.now().isoformat(timespec="seconds")

    def set_material_physical_stock(self, conn, material_key: str, material_name: str,
                                      full_rolls: int = 0, partial_meters: float = 0.0,
                                      roll_length: float = 25.5, balance_known: int = 1) -> None:
        conn.execute(
            """
            INSERT INTO material_inventory_color(
                material_key,material_name,balance_known,full_rolls,partial_meters,roll_length,note,updated_at
            ) VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(material_key) DO UPDATE SET
                balance_known=excluded.balance_known,full_rolls=excluded.full_rolls,
                partial_meters=excluded.partial_meters,roll_length=excluded.roll_length,
                updated_at=excluded.updated_at
            """,
            (material_key, material_name, balance_known, full_rolls, partial_meters, roll_length,
             "", self.now()),
        )

    def set_stock_snapshot(self, conn, nm_id: int, quantity: int, snapshot_at: str | None = None) -> str:
        """Insert a minimal WB stocks snapshot row (needed by the incident safety guard)."""
        snapshot_at = snapshot_at or self.now()
        conn.execute(
            """
            INSERT INTO stocks(
                snapshot_at,nm_id,chrt_id,warehouse_id,warehouse_name,region_name,
                quantity,in_way_to_client,in_way_from_client,raw_json
            ) VALUES (?,?,0,0,'Test WH','Test region',?,0,0,'{}')
            """,
            (snapshot_at, nm_id, quantity),
        )
        return snapshot_at
