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
    def setUp(self):
        self.enterContext(patch('scripts.backup_tenant_volumes.provisioner_headers',return_value={'Authorization':'Bearer synthetic-purpose-key'}))
        self.alert_mock=self.enterContext(patch('scripts.backup_tenant_volumes.alert',return_value='accepted'))

    @patch('scripts.backup_tenant_volumes.control_db.connect')
    def test_public_demo_is_backed_up_without_a_registry_row(self,connect_mock):
        connect_mock.return_value=_make_db(['new-client'])
        self.assertEqual(script._all_slugs(),['demo','new-client'])
        connect_mock.return_value=_make_db([])
        self.assertEqual(script._all_slugs(),['demo'])

    @patch('scripts.backup_tenant_volumes.control_db.connect')
    def test_demo_and_registry_slugs_are_unique(self,connect_mock):
        connect_mock.return_value=_make_db(['alpha','demo','alpha'])
        self.assertEqual(script._all_slugs(),['demo','alpha'])

    @patch('scripts.backup_tenant_volumes.requests.post')
    @patch('scripts.backup_tenant_volumes.control_db.connect')
    def test_inventory_failure_is_not_treated_as_empty_registry(self,connect_mock,requests_post_mock):
        import io
        from contextlib import redirect_stdout
        connect_mock.side_effect=RuntimeError('private-database-credential')
        output=io.StringIO()
        with patch.object(sys,'argv',['script']),redirect_stdout(output):
            self.assertEqual(script.main(),1)
        requests_post_mock.assert_not_called()
        self.assertEqual(self.alert_mock.call_args.args[0]['code'],'backup_inventory_unavailable')
        self.assertNotIn('private-database-credential',output.getvalue())

    @patch('scripts.backup_tenant_volumes.control_db.connect')
    def test_invalid_registry_slug_is_not_interpolated_into_provider_path(self,connect_mock):
        connect_mock.return_value=_make_db(['../private'])
        with self.assertRaisesRegex(RuntimeError,'backup_inventory_unavailable'):
            script._all_slugs()

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
            _fake_response({"archive": "demo.tar.gz.enc", "pruned": 0}),
            _fake_response({"archive": "alpha.tar.gz.enc", "pruned": 0}),
            _fake_response({"archive": "beta.tar.gz.enc", "pruned": 0}),
        ]

        with patch.object(sys, "argv", ["script"]):
            with patch("scripts.backup_tenant_volumes.config") as config_mock:
                config_mock.PROVISIONER_URL = "https://prov"
                config_mock.BACKUP_KEEP_DAYS = 7
                result = script.main()

        self.assertEqual(result, 0)
        self.assertEqual(requests_post_mock.call_count, 4)
        self.assertIn("v1/control-plane/backup", str(requests_post_mock.call_args_list[0]))
        self.assertIn("v1/tenants/demo/backup", str(requests_post_mock.call_args_list[1]))
        self.assertIn("v1/tenants/alpha/backup", str(requests_post_mock.call_args_list[2]))
        self.assertIn("v1/tenants/beta/backup", str(requests_post_mock.call_args_list[3]))

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
            _fake_response({"archive": "demo.tar.gz.enc", "pruned": 0}),
            _fake_response({"archive": "alpha.tar.gz.enc", "pruned": 0}),
        ]
        with patch.object(sys, "argv", ["script"]):
            with patch("scripts.backup_tenant_volumes.config") as config_mock:
                config_mock.PROVISIONER_URL = "https://prov"
                config_mock.BACKUP_KEEP_DAYS = 7
                result = script.main()

        self.assertEqual(result, 1)
        self.assertEqual(requests_post_mock.call_count, 3)

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
