"""Regression checks for the crawler-facing surface of the public site.

These exist because the failure mode is silent: a missing sitemap, a
canonical pointing at the wrong scheme, or a `noindex` that quietly
disappears from /account all look fine in a browser and only show up weeks
later as a deindexed site or a customer's billing page in search results.
"""
from __future__ import annotations

import json
from html.parser import HTMLParser
import re
import sys
from pathlib import Path
from fastapi.testclient import TestClient

import unittest

GATEWAY_ROOT = Path(__file__).resolve().parents[1]
if str(GATEWAY_ROOT) not in sys.path:
    sys.path.insert(0, str(GATEWAY_ROOT))

import app as gateway_app  # noqa: E402
import config  # noqa: E402

PUBLIC_PAGES = ("/", "/pricing", "/contacts", "/compliance", "/offer", "/privacy") + gateway_app.PUBLIC_MARKETING_PATHS
PRIVATE_PAGES = ("/login", "/forgot-password", "/register")


class MetaTags(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.robots: list[str] = []

    def handle_starttag(self, tag, attrs) -> None:
        values = dict(attrs)
        if tag == "meta" and (values.get("name") or "").lower() == "robots":
            self.robots.append(values.get("content") or "")


class RobotsAndSitemapTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(gateway_app.app)

    def test_robots_txt_points_at_the_sitemap(self) -> None:
        response = self.client.get("/robots.txt")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/plain", response.headers["content-type"])
        self.assertIn(f"Sitemap: {config.base_url()}/sitemap.xml", response.text)

    def test_robots_txt_hides_authenticated_areas(self) -> None:
        body = self.client.get("/robots.txt").text
        for path in ("/account", "/billing", "/admin", "/internal/", "/webhooks/"):
            self.assertIn(f"Disallow: {path}", body)

    def test_sitemap_lists_only_public_pages(self) -> None:
        response = self.client.get("/sitemap.xml")
        self.assertEqual(response.status_code, 200)
        self.assertIn("xml", response.headers["content-type"])
        locations = re.findall(r"<loc>([^<]+)</loc>", response.text)
        self.assertEqual(
            sorted(locations),
            sorted(f"{config.base_url()}{path}" for path in PUBLIC_PAGES),
        )

    def test_every_sitemap_url_actually_resolves(self) -> None:
        for path in PUBLIC_PAGES:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)


class PageMetadataTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(gateway_app.app)

    def _meta(self, html: str, name: str) -> str | None:
        match = re.search(rf'<meta name="{name}" content="([^"]*)"', html)
        return match.group(1) if match else None

    def test_public_and_private_pages_have_exactly_one_robots_tag(self) -> None:
        for path in PUBLIC_PAGES + PRIVATE_PAGES:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                metadata = MetaTags()
                metadata.feed(response.text)
                self.assertEqual(len(metadata.robots), 1, f"{path}: conflicting robots directives {metadata.robots}")

    def test_contacts_and_offer_keep_the_compliance_link(self) -> None:
        for path in ("/contacts", "/offer"):
            with self.subTest(path=path):
                link = re.search(r'<a\s+href="/compliance">[^<]+</a>', self.client.get(path).text)
                self.assertIsNotNone(link, f"{path}: missing compliance link")

    def test_public_pages_are_indexable_and_described(self) -> None:
        for path in PUBLIC_PAGES:
            with self.subTest(path=path):
                html = self.client.get(path).text
                description = self._meta(html, "description")
                self.assertIsNotNone(description)
                # Anything shorter is a placeholder, not a real snippet.
                self.assertGreater(len(description or ""), 40)
                self.assertIn("index", self._meta(html, "robots") or "")
                self.assertNotIn("noindex", self._meta(html, "robots") or "")

    def test_authenticated_pages_are_noindex(self) -> None:
        for path in PRIVATE_PAGES:
            with self.subTest(path=path):
                html = self.client.get(path).text
                self.assertIn("noindex", self._meta(html, "robots") or "")

    def test_canonical_is_absolute_and_matches_the_path(self) -> None:
        for path in PUBLIC_PAGES:
            with self.subTest(path=path):
                html = self.client.get(path).text
                canonical = re.search(r'<link rel="canonical" href="([^"]+)"', html).group(1)
                self.assertEqual(canonical, f"{config.base_url()}{path}")

    def test_home_page_ships_valid_structured_data(self) -> None:
        html = self.client.get("/").text
        raw = re.search(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)
        self.assertIsNotNone(raw, "home page lost its JSON-LD block")
        graph = json.loads(raw.group(1))["@graph"]
        types = [node["@type"] for node in graph]
        self.assertEqual(types, ["Organization", "SoftwareApplication", "FAQPage"])

        software = next(node for node in graph if node["@type"] == "SoftwareApplication")
        self.assertEqual(software["offers"]["price"], str(int(config.SUBSCRIPTION_PRICE_RUB)))
        self.assertEqual(software["offers"]["priceCurrency"], "RUB")

        # Rich results get pulled when the markup describes invisible content,
        # so every marked-up question must also be on the page itself.
        faq = next(node for node in graph if node["@type"] == "FAQPage")
        for question in faq["mainEntity"]:
            self.assertIn(question["name"], html)

    def test_cost_and_calculator_pages_ship_visible_faq_structured_data(self) -> None:
        for path in ("/sebestoimost-wb", "/calculator"):
            with self.subTest(path=path):
                html = self.client.get(path).text
                blocks = re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)
                faq_blocks = [json.loads(block) for block in blocks if '"@type": "FAQPage"' in block]
                self.assertEqual(len(faq_blocks), 1)
                faq = faq_blocks[0]
                self.assertEqual(faq["@type"], "FAQPage")
                self.assertEqual(len(faq["mainEntity"]), 6)
                self.assertEqual(html.count("<summary>"), 6)
                for question in faq["mainEntity"]:
                    self.assertIn(question["name"], html)
                    self.assertIn(question["acceptedAnswer"]["text"], html)


class AnalyticsOptInTests(unittest.TestCase):
    """The counter must stay off unless it is explicitly configured."""

    def setUp(self) -> None:
        self.client = TestClient(gateway_app.app)

    def test_no_tracker_without_a_configured_counter(self) -> None:
        if config.YANDEX_METRIKA_ID:
            self.skipTest("a Metrika counter is configured in this environment")
        html = self.client.get("/").text
        self.assertNotIn("mc.yandex.ru", html)


if __name__ == "__main__":
    unittest.main()
