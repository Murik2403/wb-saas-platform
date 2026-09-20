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
    <script type="application/ld+json">{{"@type":"FAQPage","mainEntity":[]}}</script>
    </head><body><h1>Публичная страница</h1><a href="{link}">Тариф</a></body></html>'''


class PublicSeoAuditTests(unittest.TestCase):
    def _run(self, pages: dict[str, str], statuses: dict[str, int] | None = None):
        statuses = statuses or {}
        sitemap = "<urlset>" + "".join(f"<url><loc>{BASE}{path}</loc></url>" for path in pages) + "</urlset>"
        bodies = {"/robots.txt": f"User-agent: *\nSitemap: {BASE}/sitemap.xml\n", "/sitemap.xml": sitemap, **pages}

        def fetch(url: str) -> FetchResult:
            path = url.removeprefix(BASE) or "/"
            return FetchResult(statuses.get(path, 200 if path in bodies else 404), url, bodies.get(path, ""), "text/html")

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


if __name__ == "__main__":
    unittest.main()
