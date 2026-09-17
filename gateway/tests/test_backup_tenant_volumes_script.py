"""Regression tests for control-plane + tenant backup orchestration script."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from unittest import TestCase
from unittest.mock import Mock, patch


SCRIPTS_ROOT = Path(__file__).resolve().parents[1].parent / "scripts"
if str(SCRIPTS_ROOT.parent) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT.parent))

import scripts.backup_tenant_volumes as script  # noqa: E402


def _make_db(slugs: list[str]):
    class FakeCursor:
        def __init__(self, rows: list[dict[str, Any]]):
            self._rows = rows

        def fetchall(self) -> list[dict[str, Any]]:
            return self._rows

    class FakeConn:
        def __init__(self, rows: list[dict[str, Any]]):
            self._rows = rows

        def __enter__(self) -> "FakeConn":
            return self

        def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
            return None

        def execute(self, *_args, **_kwargs) -> FakeCursor:
            return FakeCursor([{"slug": slug} for slug in self._rows])

    return FakeConn(slugs)


def _fake_response(payload: dict[str, Any]) -> Mock:
    response = Mock()
    response.raise_for_status = Mock()
    response.json.return_value = payload
    return response


class BackupTenantVolumesScriptTests(TestCase):
    @patch("scripts.backup_tenant_volumes.requests.post")
    @patch("scripts.backup_tenant_volumes.control_db.connect")
    def test_backup_script_backups_control_plane_and_all_tenants(
        self,
        connect_mock,
        requests_post_mock,
    ) -> None:
        connect_mock.return_value = _make_db(["alpha", "beta"])
        requests_post_mock.side_effect = [
            _fake_response({"archive": "control-plane.tar.gz.enc", "pruned": 1}),
            _fake_response({"archive": "alpha.tar.gz.enc", "pruned": 0}),
            _fake_response({"archive": "beta.tar.gz.enc", "pruned": 0}),
        ]

        with patch.object(sys, "argv", ["script"]):
            with patch("scripts.backup_tenant_volumes.config") as config_mock:
                config_mock.PROVISIONER_URL = "https://prov"
                config_mock.BACKUP_KEEP_DAYS = 7
                result = script.main()

        self.assertEqual(result, 0)
        self.assertEqual(requests_post_mock.call_count, 3)
        self.assertIn("v1/control-plane/backup", str(requests_post_mock.call_args_list[0]))
        self.assertIn("v1/tenants/alpha/backup", str(requests_post_mock.call_args_list[1]))
        self.assertIn("v1/tenants/beta/backup", str(requests_post_mock.call_args_list[2]))

    @patch("scripts.backup_tenant_volumes.requests.post")
    @patch("scripts.backup_tenant_volumes.control_db.connect")
    def test_control_plane_error_does_not_block_tenant_backup(
        self,
        connect_mock,
        requests_post_mock,
    ) -> None:
        connect_mock.return_value = _make_db(["alpha"])
        requests_post_mock.side_effect = [
            RuntimeError("control-plane endpoint unavailable"),
            _fake_response({"archive": "alpha.tar.gz.enc", "pruned": 0}),
        ]
        with patch.object(sys, "argv", ["script"]):
            with patch("scripts.backup_tenant_volumes.config") as config_mock:
                config_mock.PROVISIONER_URL = "https://prov"
                config_mock.BACKUP_KEEP_DAYS = 7
                result = script.main()

        self.assertEqual(result, 1)
        self.assertEqual(requests_post_mock.call_count, 2)

    @patch("scripts.backup_tenant_volumes.requests.post")
    @patch("scripts.backup_tenant_volumes.control_db.connect")
    def test_only_filter_restricts_tenants(
        self,
        connect_mock,
        requests_post_mock,
    ) -> None:
        connect_mock.return_value = _make_db(["alpha", "beta"])
        requests_post_mock.side_effect = [
            _fake_response({"archive": "control-plane.tar.gz.enc", "pruned": 1}),
            _fake_response({"archive": "beta.tar.gz.enc", "pruned": 0}),
        ]
        test_args = ["script", "--only", "beta", "--keep-days", "1"]
        with patch.object(sys, "argv", test_args):
            with patch("scripts.backup_tenant_volumes.config") as config_mock:
                config_mock.PROVISIONER_URL = "https://prov"
                config_mock.BACKUP_KEEP_DAYS = 7
                result = script.main()

        self.assertEqual(result, 0)
        self.assertEqual(requests_post_mock.call_count, 2)
        self.assertIn("v1/tenants/beta/backup", str(requests_post_mock.call_args_list[1]))
