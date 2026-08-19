from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

GATEWAY_ROOT = Path(__file__).resolve().parents[1]
if str(GATEWAY_ROOT) not in sys.path:
    sys.path.insert(0, str(GATEWAY_ROOT))

from logic import db as control_db  # noqa: E402


class GatewayDbTestCase(unittest.TestCase):
    """Fresh isolated control-plane sqlite db per test."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "test_gateway.sqlite3"

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def connect(self):
        return control_db.connect(self.db_path)

    def init_db(self) -> None:
        with self.connect() as conn:
            control_db.init_db(conn)
