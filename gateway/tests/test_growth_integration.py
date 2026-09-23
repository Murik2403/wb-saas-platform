from datetime import datetime, timedelta, timezone
from unittest import mock

from fastapi.testclient import TestClient
import app
import config
import growth_web
from internal_auth import tenant_credential
from logic import accounts, db as control_db, growth
from .base import GatewayDbTestCase

REAL_CONNECT = control_db.connect


class GrowthIntegrationTests(GatewayDbTestCase):
    def setUp(self):
        super().setUp()
        self.init_db()
        self.patchers = [
            mock.patch.object(config, "GROWTH_COOKIE_SECRET", "test-cookie-key-not-production"),
            mock.patch.object(config, "INTERNAL_API_SECRET", "test-master-not-production"),
            mock.patch.object(config, "YANDEX_METRIKA_ID", "12345678"),
            mock.patch.object(control_db, "connect", side_effect=lambda *a, **kw: REAL_CONNECT(self.db_path)),
        ]
        for patcher in self.patchers:
            patcher.start()
        self.client = TestClient(app.app, base_url="http://testserver")

    def tearDown(self):
        self.client.close()
        for patcher in reversed(self.patchers):
            patcher.stop()
        super().tearDown()

    def choose(self, choice, **utm):
        return self.client.post("/privacy/analytics-consent", json={"choice": choice, **utm}, headers={"Origin": "http://testserver"})

    def test_no_optional_cookie_or_tracker_before_consent(self):
        response = self.client.get("/?utm_source=yandex&utm_campaign=pilot")
        self.assertNotIn(growth_web.SOURCE_COOKIE, self.client.cookies)
        self.assertNotIn('src="https://mc.yandex.ru', response.text)
        self.assertIn('data-choice=""', response.text)
        self.assertEqual(response.headers["cache-control"], "private, no-store")

    def test_tenant_tracker_config_is_consent_gated_and_cors_scoped(self):
        response = self.client.get("/privacy/analytics-config", headers={"Origin": "https://shop.app.marketshelper.ru"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"enabled": False, "counter": ""})
        self.choose("analytics")
        response = self.client.get("/privacy/analytics-config", headers={"Origin": "https://shop.app.marketshelper.ru"})
        self.assertEqual(response.json(), {"enabled": True, "counter": "12345678"})
        self.assertEqual(response.headers["access-control-allow-origin"], "https://shop.app.marketshelper.ru")
        self.assertEqual(response.headers["access-control-allow-credentials"], "true")
        denied = self.client.get("/privacy/analytics-config", headers={"Origin": "https://evil.example"})
        self.assertEqual(denied.status_code, 403)

    def test_oversized_consent_body_is_rejected_without_cookie(self):
        response = self.client.post("/privacy/analytics-consent", content='{"choice":"analytics","extra":"' + 'x' * 4096 + '"}', headers={"Origin": "http://testserver", "Content-Type": "application/json"})
        self.assertEqual(response.status_code, 413)
        self.assertNotIn(growth_web.CONSENT_COOKIE, self.client.cookies)

    def test_consent_captures_only_allowlisted_source_and_revocation_removes_it(self):
        self.assertEqual(self.choose("analytics", utm_source="Yandex", utm_campaign="pilot", token="do-not-store").status_code, 200)
        cookie = self.client.cookies[growth_web.SOURCE_COOKIE]
        payload = growth_web._read(cookie, "source")
        self.assertEqual(payload["utm"]["utm_source"], "yandex")
        self.assertNotIn("token", payload["utm"])
        self.assertEqual(self.choose("essential").status_code, 200)
        self.assertNotIn(growth_web.SOURCE_COOKIE, self.client.cookies)
        self.client.get("/?utm_source=other")
        self.assertNotIn(growth_web.SOURCE_COOKIE, self.client.cookies)

    def test_cross_origin_cannot_force_consent(self):
        response = self.client.post("/privacy/analytics-consent", json={"choice": "analytics"}, headers={"Origin": "https://other.example"})
        self.assertEqual(response.status_code, 403)
        self.assertNotIn(growth_web.CONSENT_COOKIE, self.client.cookies)

    def test_tampered_or_expired_consent_is_rejected(self):
        token = growth_web._sign({"choice": "analytics"}, "consent", 60)
        self.assertEqual(growth_web._read(token + "a", "consent"), {})
        expired = growth_web._sign({"choice": "analytics"}, "consent", -1)
        self.assertEqual(growth_web._read(expired, "consent"), {})
        self.assertEqual(growth_web._read(token, "source"), {})

    def test_private_pages_do_not_get_a_counter(self):
        self.choose("analytics")
        for path in ("/register", "/login", "/verify-email-sent", "/forgot-password"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn('data-counter=""', response.text)
                self.assertNotIn("reachGoal", response.text)

    def test_accounting_landing_page_keeps_attribution_after_consent(self):
        self.choose("analytics", utm_source="yandex", utm_campaign="wb_pilot_01", utm_term="accounting_01")
        response = self.client.get("/vs-1c?utm_source=yandex&utm_campaign=wb_pilot_01&utm_term=accounting_01")
        self.assertEqual(response.status_code, 200)
        self.assertIn('data-counter="12345678"', response.text)

    def test_direct_visit_does_not_overwrite_attribution(self):
        self.choose("analytics", utm_source="partner", utm_campaign="production")
        original = self.client.cookies[growth_web.SOURCE_COOKIE]
        self.client.get("/pricing")
        self.assertEqual(self.client.cookies[growth_web.SOURCE_COOKIE], original)

    def test_new_external_touch_updates_only_after_consent(self):
        self.choose("analytics", utm_source="partner")
        self.client.get("/pricing?utm_source=yandex&utm_campaign=pilot")
        data = growth_web._read(self.client.cookies[growth_web.SOURCE_COOKIE], "source")
        self.assertEqual(data["utm"]["utm_source"], "yandex")

    def _tenant(self, email, slug):
        with self.connect() as conn:
            account_id = accounts.create_account(conn, email, "safe-pass-123")
            accounts.create_tenant_instance(conn, account_id, slug)
            growth.capture_registration_source(conn, account_id)
            growth.record_trial_started(conn, account_id)
        return account_id

    def _event(self, slug, *, credential_slug=None, event_name="first_useful_report", **changes):
        payload = {"slug": slug, "event_name": event_name, "event_key": event_name, "occurred_at": datetime.now(timezone.utc).isoformat(), **changes}
        return self.client.post("/internal/growth/event", json=payload, headers={"X-Internal-Secret": tenant_credential(config.INTERNAL_API_SECRET, credential_slug or slug)})

    def test_scoped_event_rejects_other_tenant_and_extra_account_id(self):
        self._tenant("one@example.test", "one")
        self._tenant("two@example.test", "two")
        self.assertEqual(self._event("two", credential_slug="one").status_code, 403)
        self.assertEqual(self._event("one", account_id=2).status_code, 422)

    def test_first_value_is_idempotent_and_occurrence_time_survives_retry(self):
        account_id = self._tenant("one@example.test", "one")
        stamp = datetime.now(timezone.utc).isoformat()
        self.assertEqual(self._event("one", occurred_at=stamp).status_code, 204)
        self.assertEqual(self._event("one", occurred_at=stamp).status_code, 204)
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM growth_events WHERE account_id=? AND event_name='first_useful_report'", (account_id,)).fetchall()
        self.assertEqual(len(rows), 1)

    def test_first_value_rejects_future_and_naive_time(self):
        self._tenant("one@example.test", "one")
        self.assertEqual(self._event("one", occurred_at=(datetime.now(timezone.utc)+timedelta(days=1)).isoformat()).status_code, 422)
        self.assertEqual(self._event("one", occurred_at="2026-09-18T12:00:00").status_code, 422)

    def test_connection_sync_and_production_events_are_durable_and_idempotent(self):
        account_id = self._tenant("one@example.test", "one")
        for name in ("wb_connected", "first_sync_succeeded", "first_production_result"):
            self.assertEqual(self._event("one", event_name=name).status_code, 204)
            self.assertEqual(self._event("one", event_name=name).status_code, 204)
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT event_name FROM growth_events WHERE account_id=? ORDER BY event_name", (account_id,)
            ).fetchall()
        self.assertEqual(
            [row[0] for row in rows],
            ["account_created", "first_production_result", "first_sync_succeeded", "trial_started", "wb_connected"],
        )
        with self.connect() as conn:
            activation = conn.execute(
                "SELECT metadata_json FROM growth_events WHERE account_id=? AND event_name='first_production_result'",
                (account_id,),
            ).fetchone()
        self.assertEqual(activation[0], '{"scenario": "production"}')
