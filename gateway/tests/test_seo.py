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
from marketing_routes import AUTHOR_GUIDES, AUTHOR_NAME, AUTHOR_PATH, AUTHOR_ROLE, GUIDE_DATES  # noqa: E402

PUBLIC_PAGES = ("/", "/pricing", "/capabilities", "/contacts", "/compliance", "/offer", "/privacy", "/consent", "/delete-account") + gateway_app.PUBLIC_MARKETING_PATHS
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

    def test_switching_guide_keeps_ozon_source_and_ledger_boundaries(self) -> None:
        response = self.client.get("/guides/zamena-servisov-analitiki")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Ozon подключается только в модуле рекомендаций", response.text)
        self.assertIn("API Ozon", response.text)
        self.assertIn("отдельный документальный журнал", response.text)
        self.assertIn("Начисления API и документальный расчёт показываются отдельно", response.text)
        self.assertIn("налоги и внешние расходы", response.text)

    def test_robots_txt_points_at_the_sitemap(self) -> None:
        response = self.client.get("/robots.txt")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/plain", response.headers["content-type"])
        self.assertIn(f"Sitemap: {config.base_url()}/sitemap.xml", response.text)

    def test_release_journal_is_public_and_discoverable(self) -> None:
        response = self.client.get("/updates")
        self.assertEqual(response.status_code, 200)
        self.assertIn('<time datetime="2026-09-30">', response.text)
        self.assertIn('href="/updates"', self.client.get("/contacts").text)
        self.assertIn(f"<loc>{config.base_url()}/updates</loc>", self.client.get("/sitemap.xml").text)
        self.assertIn('/updates', self.client.get("/llms.txt").text)

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

    def test_favicon_assets_are_public_and_have_expected_mime_types(self) -> None:
        svg = self.client.get("/favicon.svg")
        self.assertEqual(svg.status_code, 200)
        self.assertTrue(svg.headers["content-type"].startswith("image/svg+xml"))
        self.assertIn("<svg", svg.text)
        png = self.client.get("/favicon-192.png")
        self.assertEqual(png.status_code, 200)
        self.assertTrue(png.headers["content-type"].startswith("image/png"))
        self.assertEqual(png.content[:8], b"\x89PNG\r\n\x1a\n")

    def test_shared_template_declares_favicon_for_public_pages(self) -> None:
        for path in PUBLIC_PAGES:
            with self.subTest(path=path):
                html = self.client.get(path).text
                self.assertIn('<link rel="icon" type="image/svg+xml" href="/favicon.svg">', html)
                self.assertIn('<link rel="icon" type="image/png" sizes="192x192" href="/favicon-192.png">', html)


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

    def test_shared_calculator_faq_matches_rendered_coverage_and_capital_semantics(self) -> None:
        answers = []
        for path in ("/sebestoimost-wb", "/calculator"):
            with self.subTest(path=path):
                html = self.client.get(path).text
                blocks = re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)
                faq = next(json.loads(block) for block in blocks if '"@type": "FAQPage"' in block)
                answer = next(question["acceptedAnswer"]["text"] for question in faq["mainEntity"]
                              if question["name"] == "Калькулятор на сайте показывает чистую прибыль?")
                self.assertIn(f"<p>{answer}", html)
                self.assertNotIn("в такой расчёт полностью не входят", html)
                for phrase in ("хранению", "приёмке", "возвратам", "введённому налогу",
                               "Не введённые расходы", "не означает автоматическое",
                               "капитал показан отдельно и не вычитается", "не подтверждённая"):
                    self.assertIn(phrase, answer)
                answers.append(answer)
        self.assertEqual(answers[0], answers[1])
        calculator = self.client.get("/calculator").text
        for field in ("calcStorage", "calcAcceptance", "calcReturns", "calcTax", "calcCapital"):
            self.assertIn(f'id="{field}"', calculator)

    def test_calculator_limits_exclude_unentered_values_without_denying_available_fields(self) -> None:
        html = self.client.get("/calculator").text
        region = re.search(r'<section id="limits".*?</section>', html, re.S).group(0)
        self.assertIn("не введённые возвраты, брак и потери", region)
        self.assertIn("не введённый налог", region)
        self.assertIn("отдельных полей", region)
        self.assertIn("не подтверждает ваши суммы", region)
        self.assertNotIn("только заполненные и подтверждённые данные", region)
        self.assertNotIn("<li>возвраты, брак", region)
        self.assertNotIn("<li>налоги и общие", region)


class GuideAuthorshipTests(unittest.TestCase):
    """E-E-A-T: a guide without a visible author and date is an anonymous page.

    The markup is only allowed to say what the reader can see, so the dates in
    the Article JSON-LD are checked against the dates printed in the byline.
    """

    def setUp(self) -> None:
        self.client = TestClient(gateway_app.app)

    def _article_graph(self, html: str) -> dict:
        """The guide's JSON-LD graph, keyed by @type."""
        for raw in re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S):
            graph = {node["@type"]: node for node in json.loads(raw).get("@graph", [])}
            if "Article" in graph:
                return graph
        self.fail("no Article JSON-LD on the page")

    def test_every_guide_names_its_author_and_shows_its_dates(self) -> None:
        for path, (published, modified) in GUIDE_DATES.items():
            with self.subTest(path=path):
                html = self.client.get(path).text
                byline = re.search(r'<p class="mk-byline">(.*?)</p>', html, re.S)
                self.assertIsNotNone(byline, f"{path}: no byline")
                self.assertIn(AUTHOR_NAME, byline.group(1))
                self.assertIn(AUTHOR_ROLE, byline.group(1))
                self.assertIn(f'<time datetime="{published}">', byline.group(1))
                visible = re.findall(r'<time datetime="([^"]+)">([^<]+)</time>', byline.group(1))
                self.assertEqual(visible[0][1], f"{published[8:10]}.{published[5:7]}.{published[:4]}")
                if modified != published:
                    self.assertIn(f'<time datetime="{modified}">', byline.group(1))
                    self.assertEqual(len(visible), 2)
                else:
                    self.assertEqual(len(visible), 1, f"{path}: unchanged page claims an update")

    def test_guide_structured_data_matches_the_visible_byline(self) -> None:
        for path, (published, modified) in GUIDE_DATES.items():
            with self.subTest(path=path):
                html = self.client.get(path).text
                nodes = self._article_graph(html)
                article, person = nodes["Article"], nodes["Person"]
                self.assertEqual(article["datePublished"], published)
                self.assertEqual(article["dateModified"], modified)
                self.assertIn(f'<time datetime="{published}">', html)
                # One graph, not two strangers: author and publisher resolve by @id.
                self.assertEqual(article["author"]["@id"], person["@id"])
                self.assertEqual(article["publisher"]["@id"], nodes["Organization"]["@id"])
                self.assertEqual(person["worksFor"]["@id"], nodes["Organization"]["@id"])
                self.assertEqual(person["name"], AUTHOR_NAME)
                self.assertIn(person["name"], html)
                self.assertIn(person["jobTitle"], html)
                self.assertIn(article["headline"], html)

    def test_only_guides_carry_article_markup(self) -> None:
        for path in set(gateway_app.PUBLIC_MARKETING_PATHS) - set(GUIDE_DATES):
            with self.subTest(path=path):
                self.assertNotIn('"@type": "Article"', self.client.get(path).text)


class AuthorPageTests(unittest.TestCase):
    """The guides' Person markup only means something if it resolves to a page.

    The page must be the SAME author node the guides point at, not a second
    Person with the same name, so the @id is compared against a guide's graph.
    """

    def setUp(self) -> None:
        self.client = TestClient(gateway_app.app)

    def _person(self, html: str) -> dict:
        for raw in re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S):
            for node in json.loads(raw).get("@graph", []):
                if node["@type"] == "Person":
                    return node
        self.fail("no Person JSON-LD on the page")

    def test_author_page_renders_with_one_h1_and_a_description(self) -> None:
        response = self.client.get(AUTHOR_PATH)
        self.assertEqual(response.status_code, 200)
        html = response.text
        self.assertEqual(html.count("<h1"), 1)
        description = re.search(r'<meta name="description" content="([^"]*)"', html).group(1)
        self.assertGreater(len(description), 40)
        self.assertIn(AUTHOR_NAME, html)
        # The honesty section is the point of the page, not decoration.
        for boundary in ("Бухгалтерский и налоговый учёт", "Внешняя аналитика ниш"):
            self.assertIn(boundary, html)

    def test_author_page_links_to_every_guide_and_to_the_legal_details(self) -> None:
        html = self.client.get(AUTHOR_PATH).text
        self.assertEqual(sorted(path for path, _ in AUTHOR_GUIDES), sorted(GUIDE_DATES))
        for path, _ in AUTHOR_GUIDES:
            self.assertIn(f'href="{path}"', html)
        self.assertIn('href="/contacts"', html)

    def test_author_page_is_listed_in_the_sitemap_and_llms_txt(self) -> None:
        self.assertIn(f"<loc>{config.base_url()}{AUTHOR_PATH}</loc>", self.client.get("/sitemap.xml").text)
        self.assertIn(f"{config.base_url()}{AUTHOR_PATH}", self.client.get("/llms.txt").text)

    def test_author_page_reuses_the_person_id_from_the_guides(self) -> None:
        page = self._person(self.client.get(AUTHOR_PATH).text)
        guide = self._person(self.client.get(next(iter(GUIDE_DATES))).text)
        self.assertEqual(page["@id"], guide["@id"])
        self.assertEqual(page["name"], AUTHOR_NAME)
        self.assertEqual(page["jobTitle"], AUTHOR_ROLE)
        self.assertEqual(page["url"], f"{config.base_url()}{AUTHOR_PATH}")
        self.assertEqual(page["mainEntityOfPage"], f"{config.base_url()}{AUTHOR_PATH}")
        self.assertEqual(page["worksFor"]["@id"], f"{config.base_url()}/#organization")
        # No profile is published anywhere on the site, so none may be claimed.
        self.assertNotIn("sameAs", page)

    def test_every_guide_byline_links_to_the_author_page(self) -> None:
        for path in GUIDE_DATES:
            with self.subTest(path=path):
                byline = re.search(r'<p class="mk-byline">(.*?)</p>', self.client.get(path).text, re.S).group(1)
                link = re.search(r'<a class="mk-byline-author" href="([^"]+)"', byline)
                self.assertIsNotNone(link, f"{path}: byline author is not a link")
                self.assertEqual(link.group(1), AUTHOR_PATH)


class LlmsTxtTests(unittest.TestCase):
    """The AI-search summary is generated, so it cannot drift from the site."""

    def setUp(self) -> None:
        self.client = TestClient(gateway_app.app)

    def test_llms_txt_is_plain_text_and_lists_the_public_marketing_pages(self) -> None:
        response = self.client.get("/llms.txt")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/plain", response.headers["content-type"])
        listed = re.findall(rf"^- {re.escape(config.base_url())}(\S+?): (.+)$", response.text, re.M)
        self.assertEqual(
            sorted(path for path, _ in listed),
            sorted(gateway_app.PUBLIC_MARKETING_PATHS),
        )
        for _, description in listed:
            self.assertGreater(len(description), 40)

    def test_llms_txt_states_the_boundaries_of_the_service(self) -> None:
        body = self.client.get("/llms.txt").text
        for claim in ("не бухгалтерия", "не внешняя аналитика", "Wildberries", "Ozon"):
            self.assertIn(claim, body)

    def test_llms_txt_is_allowed_by_robots_but_stays_out_of_the_sitemap(self) -> None:
        self.assertIn("Allow: /llms.txt", self.client.get("/robots.txt").text)
        self.assertNotIn("/llms.txt", self.client.get("/sitemap.xml").text)


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
