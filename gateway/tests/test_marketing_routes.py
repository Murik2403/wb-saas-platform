"""Public content must render, link to real pages and share the live tariff."""
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit
import sys
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

GATEWAY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(GATEWAY_ROOT))
import config
from marketing_routes import PUBLIC_MARKETING_PATHS, create_marketing_router
from template_setup import create_templates


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.ids = []
        self.h1_count = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a":
            self.links.append(attrs.get("href", ""))
        if "id" in attrs:
            self.ids.append(attrs["id"])
        self.h1_count += tag == "h1"


def page_parser_links(html: str) -> list[str]:
    parser = PageParser()
    parser.feed(html)
    return parser.links


class MarketingRouteTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(create_marketing_router(create_templates()))
        self.client = TestClient(app)

    def test_all_pages_render_and_navigation_resolves(self):
        existing = {"/", "/pricing", "/login", "/register", "/offer", "/privacy", "/contacts"}
        for path in PUBLIC_MARKETING_PATHS:
            with self.subTest(path=path):
                response = self.client.get(path + "?utm_source=test")
                self.assertEqual(response.status_code, 200)
                self.assertIn("text/html", response.headers["content-type"])
                self.assertIn(f'href="{config.base_url()}{path}"', response.text)
                parser = PageParser()
                parser.feed(response.text)
                self.assertEqual(parser.h1_count, 1)
                self.assertEqual(len(parser.ids), len(set(parser.ids)))
                for link in parser.links:
                    parts = urlsplit(link)
                    if parts.scheme or parts.netloc:
                        continue
                    self.assertIn(parts.path or path, existing | set(PUBLIC_MARKETING_PATHS))
                    if parts.fragment and (not parts.path or parts.path == path):
                        self.assertIn(parts.fragment, parser.ids)

    def test_tariff_and_trial_follow_config(self):
        with patch.object(config, "SUBSCRIPTION_PRICE_RUB", 4190), patch.object(config, "TRIAL_DAYS", 21), patch.object(config, "BILLING_PERIOD_DAYS", 31):
            for path in PUBLIC_MARKETING_PATHS:
                html = self.client.get(path).text
                self.assertIn("4190 ₽ / 31 дней", html)
                self.assertIn("Попробовать 21 дней", html)
                self.assertNotIn("2990 ₽", html)

    def test_calculator_metadata_describes_nine_articles(self):
        html = self.client.get("/calculator").text
        self.assertIn("Калькулятор маржи WB по девяти статьям расходов — MARKETSHELPER", html)
        self.assertIn("хранению, рекламе, упаковке, браку и налогам", html)
        self.assertIn("id=\"calcStorage\"", html)
        self.assertIn("id=\"calcTax\"", html)
        self.assertIn("нужно ввести вручную", html)
        self.assertNotIn("или импортировать", html)

    def test_public_analytics_guide_does_not_overstate_api_access(self):
        html = self.client.get("/guides/zamena-servisov-analitiki").text
        self.assertIn("Некоторые отчёты и периоды требуют подписку", html)
        self.assertNotIn("вы можете получить те же данные сами", html)

    def test_calculator_does_not_claim_to_import_expenses(self):
        html = self.client.get("/calculator").text
        self.assertIn("внешние статьи в калькуляторе нужно ввести вручную", html)
        self.assertNotIn("или импортировать", html)

    def test_cost_page_has_safe_seo_content_and_internal_links(self):
        html = self.client.get("/sebestoimost-wb?utm_source=test").text
        self.assertIn("Себестоимость товара на Wildberries: расчёт и учёт партий", html)
        self.assertIn("Себестоимость товара на WB: расчёт и учёт партий — MARKETSHELPER", html)
        self.assertIn("Как рассчитать себестоимость товара на Wildberries: материалы, выпуск и партии FIFO.", html)
        self.assertEqual(html.count("<h1>"), 1)
        self.assertEqual(html.count('id="unit-cost"'), 1)
        self.assertEqual(html.count('id="service"'), 1)
        self.assertEqual(html.count("<summary>"), 6)
        for href in ("/calculator", "/fifo-uchet", "/guides/fifo-po-partiyam", "/guides/pribyl-i-rashody-wb", "/dlya-proizvoditeley-wb", "/guides/iz-excel-v-uchet", "/guides/fifo-ili-srednyaya-v-akcii"):
            self.assertIn(f'href="{href}"', html)
        internal_links = [link for link in page_parser_links(html) if link.startswith("/")]
        self.assertTrue(internal_links)
        self.assertTrue(all("utm_" not in link for link in internal_links))

    def test_unknown_page_is_not_a_generic_landing(self):
        self.assertEqual(self.client.get("/guides/not-a-guide").status_code, 404)
