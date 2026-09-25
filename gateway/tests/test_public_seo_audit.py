"""Unit tests for the read-only public SEO audit."""
from __future__ import annotations

from pathlib import Path
import sys
import unittest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from audit_public_seo import FetchResult, audit_public_site  # noqa: E402


BASE = "https://example.test"


def fixture_page(canonical: str = f"{BASE}/", link: str = "/pricing") -> str:
    return f'''<!doctype html><html><head>
    <title>MARKETSHELPER</title>
    <meta name="description" content="Описание публичной страницы для проверки поискового аудита">
    <meta name="robots" content="index, follow">
    <link rel="canonical" href="{canonical}">
    <link rel="icon" type="image/svg+xml" href="/favicon.svg">
    <script type="application/ld+json">{{"@type":"FAQPage","mainEntity":[]}}</script>
    </head><body><h1>Публичная страница</h1><a href="{link}">Тариф</a></body></html>'''


class PublicSeoAuditTests(unittest.TestCase):
    def _run(self, pages: dict[str, str], statuses: dict[str, int] | None = None):
        statuses = statuses or {}
        sitemap = "<urlset>" + "".join(f"<url><loc>{BASE}{path}</loc></url>" for path in pages) + "</urlset>"
        bodies = {"/robots.txt": f"User-agent: *\nSitemap: {BASE}/sitemap.xml\n", "/sitemap.xml": sitemap, "/favicon.svg": "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 1 1'></svg>", **pages}

        def fetch(url: str) -> FetchResult:
            path = url.removeprefix(BASE) or "/"
            content_type = "image/svg+xml" if path == "/favicon.svg" else "text/html"
            return FetchResult(statuses.get(path, 200 if path in bodies else 404), url, bodies.get(path, ""), content_type)

        return audit_public_site(BASE, fetch)

    def test_passing_page_has_passes_and_no_failures(self):
        result = self._run({"/": fixture_page(link="/")})
        self.assertEqual(result["summary"]["fail"], 0)
        self.assertGreater(result["summary"]["pass"], 0)

    def test_missing_canonical_is_failure(self):
        html = fixture_page().replace(f'<link rel="canonical" href="{BASE}/">', "")
        result = self._run({"/": html})
        failures = [item for item in result["checks"] if item["status"] == "fail"]
        self.assertTrue(any(item["check"] == "canonical" for item in failures))

    def test_broken_internal_link_is_failure(self):
        result = self._run({"/": fixture_page(link="/missing")})
        failures = [item for item in result["checks"] if item["status"] == "fail"]
        self.assertTrue(any(item["check"] == "internal link" and "/missing" in item["detail"] for item in failures))

    def test_missing_favicon_declaration_is_reported(self):
        html = fixture_page().replace('<link rel="icon" type="image/svg+xml" href="/favicon.svg">', "")
        result = self._run({"/": html})
        self.assertTrue(any(item["check"] == "favicon declaration" and item["status"] == "fail" for item in result["checks"]))

    def test_inaccessible_favicon_is_reported(self):
        result = self._run({"/": fixture_page()}, {"/favicon.svg": 404})
        self.assertTrue(any(item["check"] == "favicon asset" and item["status"] == "fail" for item in result["checks"]))


if __name__ == "__main__":
    unittest.main()
