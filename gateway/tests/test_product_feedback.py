from unittest import mock
from fastapi.testclient import TestClient
import app
import config
import product_feedback_routes as routes
from logic import accounts, csrf, db as control_db, product_feedback, growth
from .base import GatewayDbTestCase

REAL_CONNECT = control_db.connect

class FeedbackTests(GatewayDbTestCase):
    def setUp(self):
        super().setUp(); self.init_db()
        self.patch = mock.patch.object(control_db,"connect",side_effect=lambda *a,**kw: REAL_CONNECT(self.db_path)); self.patch.start()
        self.rate = mock.patch.object(routes.limiter,"is_allowed",return_value=True); self.rate.start()
        self.client = TestClient(app.app,base_url="http://testserver")
        with self.connect() as conn:
            self.owner=accounts.create_account(conn,"owner@example.test","safe-pass-123")
            accounts.create_tenant_instance(conn,self.owner,"own")
            self.session=accounts.create_session(conn,self.owner)
        self.client.cookies.set(config.SESSION_COOKIE_NAME,self.session)

    def tearDown(self):
        self.client.close(); self.rate.stop(); self.patch.stop(); super().tearDown()

    def payload(self,**changes):
        return {"scenario":"wb_finance","period_start":"2026-09-01","period_end":"2026-09-30","reason":"none","actual_minutes":"0","csrf_token":self.client.cookies.get(csrf.CSRF_COOKIE_NAME,""),**changes}

    def count(self):
        with self.connect() as conn:
            return conn.execute("SELECT COUNT(*) AS n FROM product_feedback").fetchone()["n"]

    def test_private_owner_page_no_tracker_no_public_identity_and_login_next(self):
        page=self.client.get("/feedback")
        self.assertEqual(page.status_code,200); self.assertIn("no-store",page.headers["cache-control"])
        self.assertIn('noindex, nofollow',page.text); self.assertNotIn("owner@example.test",page.text)
        self.assertNotIn('src="https://mc.yandex.ru',page.text)
        self.client.cookies.clear()
        anonymous=self.client.get("/feedback",follow_redirects=False)
        self.assertEqual(anonymous.headers["location"],"/login?next=%2Ffeedback")

    def test_csrf_required_and_retry_upsert_keeps_zero_observed(self):
        self.client.get("/feedback")
        self.assertEqual(self.client.post("/feedback",data=self.payload(csrf_token="bad")).status_code,403)
        self.assertEqual(self.client.post("/feedback",data=self.payload(csrf_token="☃")).status_code,403)
        self.assertEqual(self.count(),0)
        for _ in range(2):
            self.assertEqual(self.client.post("/feedback",data=self.payload(),follow_redirects=False).status_code,303)
        self.assertEqual(self.count(),1)
        with self.connect() as conn:
            result=product_feedback.summary(conn)
        self.assertEqual(result["scenarios"][0]["median_minutes"],0)
        self.assertEqual(result["organizations"],1)

    def test_optional_minutes_refusal_enum_and_invalid_input_do_not_write(self):
        self.client.get("/feedback")
        for changes in ({"reason":"secret-token"},{"actual_minutes":"-1"},{"actual_minutes":"1.5"},{"actual_minutes":"10081"},{"period_end":"2999-01-01"},{"period_start":"2026-10-01","period_end":"2026-09-01"},{"actual_minutes":""}):
            with self.subTest(changes=changes):
                self.assertEqual(self.client.post("/feedback",data=self.payload(**changes)).status_code,422)
                self.assertEqual(self.count(),0)
        self.assertEqual(self.client.post("/feedback",data=self.payload(reason="price",actual_minutes=""),follow_redirects=False).status_code,303)
        with self.connect() as conn:
            self.assertIsNone(product_feedback.summary(conn)["scenarios"][0]["median_minutes"])

    def test_non_owner_and_demo_cannot_submit_and_rate_limit_blocks_write(self):
        self.client.get("/feedback")
        with mock.patch.object(routes.limiter,"is_allowed",return_value=False):
            self.assertEqual(self.client.post("/feedback",data=self.payload()).status_code,429)
        with self.connect() as conn:
            member=accounts.create_account(conn,"member@example.test","safe-pass-123")
            token=accounts.create_session(conn,member)
        self.client.cookies.set(config.SESSION_COOKIE_NAME,token)
        self.assertEqual(self.client.get("/feedback").status_code,403)
        self.assertEqual(self.count(),0)

    def test_account_owned_delete_and_admin_service_exclusions(self):
        self.client.get("/feedback")
        self.client.post("/feedback",data=self.payload())
        with self.connect() as conn:
            other=accounts.create_account(conn,"other@example.test","safe-pass-123")
            accounts.create_tenant_instance(conn,other,"other")
            product_feedback.save(conn,other,"wb_finance","2026-09-01","2026-09-30","price","20")
            growth.set_account_exclusion(conn,other,"test")
            self.assertEqual(product_feedback.summary(conn)["responses"],1)
            with mock.patch.object(config,"ADMIN_EMAILS",["owner@example.test"]):
                self.assertEqual(product_feedback.summary(conn)["responses"],0)
        self.client.post("/feedback",data=self.payload(action="delete"))
        self.assertEqual(self.count(),1)
        with self.connect() as conn:
            accounts.delete_account(conn,other)
        self.assertEqual(self.count(),0)

    def test_demo_and_admin_are_excluded_and_other_account_payload_cannot_spoof_owner(self):
        self.client.get("/feedback")
        with self.connect() as conn:
            demo=accounts.create_account(conn,"demo@example.test","safe-pass-123")
            accounts.create_tenant_instance(conn,demo,"demo")
            demo_token=accounts.create_session(conn,demo)
            product_feedback.save(conn,demo,"wb_finance","2026-09-01","2026-09-30","price","20")
            self.assertEqual(product_feedback.summary(conn)["responses"],0)
        self.client.post("/feedback",data=self.payload(account_id=str(demo), email="ignored@example.test"))
        with self.connect() as conn:
            own=conn.execute("SELECT account_id FROM product_feedback WHERE reason='none'").fetchone()
            self.assertEqual(own["account_id"],self.owner)
        self.client.cookies.set(config.SESSION_COOKIE_NAME,demo_token)
        self.assertEqual(self.client.get("/feedback").status_code,403)
        self.assertEqual(self.client.post("/feedback",data=self.payload()).status_code,403)

    def test_schema_repeat_and_median_observed_only_survive_zero_missing(self):
        with self.connect() as conn:
            control_db.init_db(conn)
            product_feedback.save(conn,self.owner,"wb_finance","2026-09-01","2026-09-30","none","0")
            product_feedback.save(conn,self.owner,"wb_finance","2026-08-01","2026-08-31","price","")
            product_feedback.save(conn,self.owner,"wb_finance","2026-07-01","2026-07-31","none","20")
            result=product_feedback.summary(conn)
            self.assertEqual(result["scenarios"][0]["minutes_observed"],2)
            self.assertEqual(result["scenarios"][0]["median_minutes"],10)
            self.assertEqual(result["organizations"],1)
            self.assertEqual(result["responses"],3)
