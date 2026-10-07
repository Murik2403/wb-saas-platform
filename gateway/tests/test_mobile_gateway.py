"""Mobile-app support: store-build mode, app-link files, delete-account page, SEO fixes."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

GATEWAY_ROOT = Path(__file__).resolve().parents[1]
if str(GATEWAY_ROOT) not in sys.path:
    sys.path.insert(0, str(GATEWAY_ROOT))

import app as gateway_app  # noqa: E402
import billing_routes  # noqa: E402
import config  # noqa: E402
from logic import accounts, db as control_db  # noqa: E402

from .base import GatewayDbTestCase  # noqa: E402

APP_UA = {"User-Agent": "Mozilla/5.0 (Linux; Android 14) MarketsHelperApp/1.0"}
REAL_CONNECT = control_db.connect


class StoreModeTests(GatewayDbTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.init_db()
        with self.connect() as conn:
            aid = accounts.create_account(conn, "seller@example.test", "safe-password-123")
            accounts.create_tenant_instance(conn, aid, "seller")
            self.account = accounts.get_account_by_id(conn, aid)
        connect = lambda *a, **k: REAL_CONNECT(self.db_path)  # noqa: E731
        for patcher in (
            mock.patch.object(gateway_app, "_current_account", return_value=self.account),
            mock.patch.object(billing_routes, "_current_account", return_value=self.account),
            mock.patch.object(gateway_app.control_db, "connect", connect),
            mock.patch.object(billing_routes.control_db, "connect", connect),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = TestClient(gateway_app.app, follow_redirects=False)

    def test_app_ua_sees_no_payment_ui(self) -> None:
        for path in ("/pricing", "/billing", "/billing/cancel", "/billing/return", "/account", "/dlya-proizvoditeley-wb"):
            body = self.client.get(path, headers=APP_UA)
            self.assertEqual(body.status_code, 200, path)
            for word in ("Оплатить", "2990", "Подписка и оплата", "/billing/checkout"):
                self.assertNotIn(word, body.text, (path, word))
        self.assertIn("Управление подпиской доступно на сайте marketshelper.ru", self.client.get("/billing", headers=APP_UA).text)
        self.assertNotIn("<a ", self.client.get("/pricing", headers=APP_UA).text.split("<body", 1)[1].split("legal-footer")[0])

    def test_app_ua_home_has_no_price(self) -> None:
        gateway_app._current_account.return_value = None
        text = self.client.get("/", headers=APP_UA).text
        self.assertNotIn("2990", text)
        self.assertNotIn('href="/pricing"', text)
        for block in re.findall(r'application/ld\+json">(.*?)</script>', text, re.S):
            json.loads(block)

    def test_app_ua_cannot_start_checkout(self) -> None:
        with mock.patch.object(billing_routes.yookassa_client, "create_payment") as create:
            response = self.client.post("/billing/checkout", headers=APP_UA, data={"csrf_token": "x"})
        self.assertEqual(response.status_code, 200)
        create.assert_not_called()

    def test_regular_ua_unchanged(self) -> None:
        self.assertIn("2990", self.client.get("/pricing").text)
        self.assertIn("Оплатить", self.client.get("/billing").text)
        self.assertIn("Подписка и оплата", self.client.get("/account").text)
        gateway_app._current_account.return_value = None
        self.assertIn("2990", self.client.get("/").text)


class AppLinkFileTests(GatewayDbTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.client = TestClient(gateway_app.app, follow_redirects=False)

    def test_empty_defaults(self) -> None:
        with mock.patch.object(config, "ANDROID_PACKAGE", ""), mock.patch.object(config, "IOS_APP_ID", ""):
            r = self.client.get("/.well-known/assetlinks.json")
            self.assertEqual((r.status_code, r.json()), (200, []))
            r = self.client.get("/.well-known/apple-app-site-association")
            self.assertEqual(r.status_code, 200)
            self.assertIn("application/json", r.headers["content-type"])
            self.assertEqual(r.json(), {"applinks": {"apps": [], "details": []}})

    def test_configured(self) -> None:
        with mock.patch.object(config, "ANDROID_PACKAGE", "ru.marketshelper.app"), \
                mock.patch.object(config, "ANDROID_SHA256", ["AA:BB", "CC:DD"]), \
                mock.patch.object(config, "IOS_APP_ID", "TEAM123.ru.marketshelper.app"):
            target = self.client.get("/.well-known/assetlinks.json").json()[0]["target"]
            self.assertEqual(target["package_name"], "ru.marketshelper.app")
            self.assertEqual(target["sha256_cert_fingerprints"], ["AA:BB", "CC:DD"])
            detail = self.client.get("/.well-known/apple-app-site-association").json()["applinks"]["details"][0]
            self.assertEqual(detail["appIDs"], ["TEAM123.ru.marketshelper.app"])
            self.assertIn("/team/accept*", detail["paths"])


class DeleteAccountPageTests(GatewayDbTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.client = TestClient(gateway_app.app, follow_redirects=False)

    def test_page_is_public_indexable_and_linked(self) -> None:
        r = self.client.get("/delete-account")
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("noindex", r.text)
        for needle in ("УДАЛИТЬ", "mvvorokov@yandex.ru", 'href="/privacy"', 'href="/consent"', f"{config.BACKUP_KEEP_DAYS} дней"):
            self.assertIn(needle, r.text)
        self.assertIn("/delete-account</loc>", self.client.get("/sitemap.xml").text)
        self.assertIn('href="/delete-account"', self.client.get("/contacts").text)

    def test_calculator_drafts_link_absent_from_public_templates(self) -> None:
        for path in ("/", "/pricing", "/calculator", "/contacts"):
            self.assertNotIn("calculator-drafts", self.client.get(path).text)


class SeoFixTests(GatewayDbTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.client = TestClient(gateway_app.app, follow_redirects=False)

    def test_trailing_slash_redirect_stays_on_https(self) -> None:
        for path in ("/pricing/", "/guides/", "/updates/", "/training/"):
            r = self.client.get(path, headers={"X-Forwarded-Proto": "https", "Host": "marketshelper.ru"})
            self.assertIn(r.status_code, (301, 307, 308))
            self.assertEqual(r.headers["location"], "https://marketshelper.ru" + path.rstrip("/"))

    def test_sitemap_lastmod(self) -> None:
        text = self.client.get("/sitemap.xml").text
        entries = re.findall(r"<url><loc>([^<]+)</loc><lastmod>([^<]+)</lastmod>", text)
        self.assertEqual(len(entries), text.count("<url>"))
        for _, date in entries:
            self.assertRegex(date, r"^\d{4}-\d{2}-\d{2}$")
        self.assertIn(("%s/guides/zamena-servisov-analitiki" % config.base_url(), "2026-10-01"), entries)

    def test_footer_links_key_pages(self) -> None:
        for page in ("/login", "/", "/guides"):
            text = self.client.get(page).text
            for href in ("/capabilities", "/contacts", "/offer", "/training", "/calculator", "/compliance"):
                self.assertIn(f'href="{href}"', text, (page, href))
        home = self.client.get("/").text
        for href in ("/guides/fifo-po-partiyam", "/guides/zamena-servisov-analitiki", "/calculator"):
            self.assertIn(f'href="{href}"', home)
