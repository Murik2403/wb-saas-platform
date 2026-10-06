from unittest import mock
from starlette.requests import Request
import app as gateway
from logic import accounts
from logic import db as control_db
from .base import GatewayDbTestCase
REAL_CONNECT = control_db.connect


class ProvisioningStatusTests(GatewayDbTestCase):
    def setUp(self):
        super().setUp(); self.init_db()
        with self.connect() as conn:
            self.account_id = accounts.create_account(conn,"status@example.test","safe-password-123",email_verified=True)
            accounts.set_account_status(conn,self.account_id,"trialing")
            self.tenant_id = accounts.create_tenant_instance(conn,self.account_id,"status-test")

    def request(self):
        return Request({"type":"http","method":"GET","path":"/provisioning","headers":[]})

    def test_running_redirect_checks_access_before_connection_closes(self):
        with self.connect() as conn:
            accounts.set_tenant_status(conn,self.tenant_id,"running")
            account=accounts.get_account_by_id(conn,self.account_id)
        with mock.patch.object(gateway,"_current_account",return_value=account),mock.patch.object(gateway.control_db,"connect",side_effect=lambda:REAL_CONNECT(self.db_path)):
            response=gateway.provisioning_status(self.request())
        assert response.status_code==303
        assert "status-test" in response.headers["location"]

    def test_running_expired_owner_does_not_redirect_into_cabinet(self):
        with self.connect() as conn:
            accounts.set_tenant_status(conn,self.tenant_id,"running")
            accounts.set_account_status(conn,self.account_id,"canceled")
            account=accounts.get_account_by_id(conn,self.account_id)
        with mock.patch.object(gateway,"_current_account",return_value=account),mock.patch.object(gateway.control_db,"connect",side_effect=lambda:REAL_CONNECT(self.db_path)):
            response=gateway.provisioning_status(self.request())
        assert response.status_code==200
