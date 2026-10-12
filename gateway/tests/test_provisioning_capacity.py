"""A full server must make a new cabinet wait, not fail the signup; disposable DB, no containers, no sleeping."""
import threading
from unittest import mock

import requests
from starlette.requests import Request

import app as gateway
import provisioning
from logic import accounts, db as control_db

from .base import GatewayDbTestCase

REAL_CONNECT = control_db.connect


def refusal(code="capacity_exhausted", status=503):
    response = mock.Mock(status_code=status)
    response.json.return_value = {"detail": {"code": code, "reason": "budget", "free_mib": 0}}
    return requests.HTTPError("refused", response=response)


class CapacityWaitTests(GatewayDbTestCase):
    def setUp(self):
        super().setUp()
        self.init_db()
        with self.connect() as conn:
            self.account_id = accounts.create_account(conn, "wait@example.test", "safe-password-123", email_verified=True)
            accounts.set_account_status(conn, self.account_id, "trialing")
            self.tenant_id = accounts.create_tenant_instance(conn, self.account_id, "wait-test")
        self.patches = [
            mock.patch.object(provisioning.control_db, "connect", side_effect=lambda *a, **k: REAL_CONNECT(self.db_path)),
            mock.patch.object(provisioning, "_wait_until_healthy", return_value=True),
            mock.patch.object(provisioning, "_wait_for_tenant_cert", return_value=True),
            mock.patch.object(provisioning.config, "PROVISION_CAPACITY_RETRY_SECONDS", 0),
        ]
        for patch in self.patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.sleep = mock.patch.object(provisioning.time, "sleep")
        self.sleep.start()
        self.addCleanup(self.sleep.stop)

    def tenant(self):
        with self.connect() as conn:
            return accounts.get_tenant_by_id(conn, self.tenant_id)

    def test_waits_through_refusals_then_starts_without_ever_failing(self):
        seen = []

        def run(slug):
            seen.append((self.tenant()["status"], self.tenant()["note"]))
            if len(seen) < 3:
                raise refusal()
        with mock.patch.object(provisioning, "_run_tenant_container", side_effect=run) as launch, \
             mock.patch.object(provisioning, "_cleanup_failed_tenant") as cleanup:
            provisioning.provision_tenant_background(self.account_id, self.tenant_id, "wait-test")
        assert launch.call_count == 3
        cleanup.assert_not_called()
        assert seen[0] == ("provisioning", "")
        assert seen[1] == ("provisioning", accounts.TENANT_NOTE_WAITING_CAPACITY)  # visible while it waits
        assert self.tenant()["status"] == "running"

    def test_gives_up_with_a_clear_reason_when_the_server_stays_full(self):
        with mock.patch.object(provisioning.config, "PROVISION_CAPACITY_WAIT_SECONDS", 0), \
             mock.patch.object(provisioning, "_run_tenant_container", side_effect=refusal()), \
             mock.patch.object(provisioning, "_cleanup_failed_tenant") as cleanup:
            provisioning.provision_tenant_background(self.account_id, self.tenant_id, "wait-test")
        cleanup.assert_called_once_with("wait-test")
        assert self.tenant()["status"] == "failed"
        assert self.tenant()["note"] == accounts.TENANT_NOTE_NO_CAPACITY
        with self.connect() as conn:
            assert accounts.get_account_by_id(conn, self.account_id)["status"] == "trialing"  # no trial side effects here

    def test_other_errors_still_fail_at_once(self):
        for error in (refusal(code="something_else"), refusal(status=502), RuntimeError("docker down")):
            with mock.patch.object(provisioning, "_run_tenant_container", side_effect=error) as launch, \
                 mock.patch.object(provisioning, "_cleanup_failed_tenant"):
                provisioning.provision_tenant_background(self.account_id, self.tenant_id, "wait-test")
            assert launch.call_count == 1
            assert self.tenant()["status"] == "failed"
            assert self.tenant()["note"].startswith("Provisioner error")

    def test_a_repeated_trigger_while_one_is_running_does_nothing(self):
        entered, release = threading.Event(), threading.Event()

        def slow(slug):
            entered.set()
            release.wait(timeout=5)
        with mock.patch.object(provisioning, "_run_tenant_container", side_effect=slow) as launch, \
             mock.patch.object(provisioning, "_cleanup_failed_tenant") as cleanup:
            first = threading.Thread(target=provisioning.provision_tenant_background,
                                     args=(self.account_id, self.tenant_id, "wait-test"))
            first.start()
            assert entered.wait(timeout=5)
            provisioning.provision_tenant_background(self.account_id, self.tenant_id, "wait-test")  # second login
            release.set()
            first.join(timeout=5)
        assert launch.call_count == 1
        cleanup.assert_not_called()
        assert self.tenant()["status"] == "running"
        provisioning.provision_tenant_background(self.account_id, self.tenant_id, "wait-test")  # allowed again later

    def page(self):
        with self.connect() as conn:
            account = accounts.get_account_by_id(conn, self.account_id)
        request = Request({"type": "http", "method": "GET", "path": "/provisioning", "headers": []})
        with mock.patch.object(gateway, "_current_account", return_value=account), \
             mock.patch.object(gateway.control_db, "connect", side_effect=lambda: REAL_CONNECT(self.db_path)):
            return gateway.provisioning_status(request).body.decode()

    def test_the_page_explains_waiting_and_giving_up(self):
        with self.connect() as conn:
            accounts.set_tenant_status(conn, self.tenant_id, "provisioning", note=accounts.TENANT_NOTE_WAITING_CAPACITY)
        assert "стоит в очереди" in self.page()
        with self.connect() as conn:
            accounts.set_tenant_status(conn, self.tenant_id, "failed", note=accounts.TENANT_NOTE_NO_CAPACITY)
        html = self.page()
        assert "нет свободных ресурсов" in html and "Не получилось развернуть" not in html
        with self.connect() as conn:
            accounts.set_tenant_status(conn, self.tenant_id, "failed", note="Provisioner error: x")
        html = self.page()
        assert "Не получилось развернуть" in html and "нет свободных ресурсов" not in html
