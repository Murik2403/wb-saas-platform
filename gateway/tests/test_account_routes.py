from __future__ import annotations

import asyncio
from unittest import mock

from fastapi import Request

import app
from logic import accounts, db as control_db

from .base import GatewayDbTestCase


REAL_CONNECT = control_db.connect


def _request(path: str, method: str = "GET") -> Request:
    return Request({
        "type": "http",
        "headers": [],
        "method": method,
        "path": path,
        "client": ("198.51.100.10", 12345),
    })


class AccountDataRoutesTests(GatewayDbTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.init_db()
        with self.connect() as conn:
            self.account_id = accounts.create_account(conn, "seller@example.test", "safe-password-123")
            accounts.create_tenant_instance(conn, self.account_id, "seller")
            self.account = accounts.get_account_by_id(conn, self.account_id)

    def _app_connect(self, *args, **kwargs):
        return REAL_CONNECT(self.db_path)

    def test_export_stream_is_owned_by_current_account(self) -> None:
        class Upstream:
            status_code = 200
            headers = {"content-type": "application/gzip", "content-disposition": 'attachment; filename="seller.tar.gz"'}

            def iter_content(self, chunk_size):
                assert chunk_size == 64 * 1024
                yield b"archive"

            def close(self):
                self.closed = True

        upstream = Upstream()
        with (
            mock.patch.object(app, "_current_account", return_value=self.account),
            mock.patch.object(app.control_db, "connect", side_effect=self._app_connect),
            mock.patch("provisioning.export_tenant", return_value=upstream) as export,
        ):
            response = app.account_export(_request("/account/export"))
        export.assert_called_once_with("seller")
        assert response.media_type == "application/gzip"

        async def read_body():
            return b"".join([chunk async for chunk in response.body_iterator])

        assert asyncio.run(read_body()) == b"archive"
        assert upstream.closed is True

    def test_delete_requires_password_and_confirmation_then_cleans_control_plane(self) -> None:
        with (
            mock.patch.object(app, "_current_account", return_value=self.account),
            mock.patch.object(app.csrf, "verify", return_value=True),
            mock.patch.object(app.control_db, "connect", side_effect=self._app_connect),
            mock.patch("provisioning.remove_tenant") as remove_tenant,
        ):
            response = app.account_delete(
                _request("/account/delete", "POST"), "safe-password-123", "УДАЛИТЬ", "csrf"
            )
        assert response.status_code == 303
        assert response.headers["location"] == "/"
        remove_tenant.assert_called_once_with("seller", purge_volume=True)
        with self.connect() as conn:
            assert accounts.get_account_by_id(conn, self.account_id) is None
