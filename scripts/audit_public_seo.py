#!/usr/bin/env python3
"""Read-only SEO smoke audit for public MARKETSHELPER pages."""
from __future__ import annotations

import argparse
import json
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen


class _MetaParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title = ""
        self.h1 = ""
        self.description = ""
        self.canonical = ""
        self.links: list[str] = []
        self._title = False
        self._h1 = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        data = dict(attrs)
        if tag == "title": self._title = True
        if tag == "h1": self._h1 = True
        if tag == "meta" and data.get("name", "").lower() == "description": self.description = data.get("content", "") or ""
        if tag == "link" and data.get("rel", "").lower() == "canonical": self.canonical = data.get("href", "") or ""
        if tag == "a" and data.get("href"): self.links.append(data["href"] or "")

    def handle_endtag(self, tag: str) -> None:
        if tag == "title": self._title = False
        if tag == "h1": self._h1 = False

    def handle_data(self, data: str) -> None:
        if self._title: self.title += data.strip()
        if self._h1: self.h1 += data.strip()


def audit_html(html: str, url: str, *, sitemap_urls: set[str] | None = None, known_urls: set[str] | None = None) -> dict:
    parser = _MetaParser(); parser.feed(html)
    sitemap_urls = sitemap_urls or set()
    known_urls = known_urls or set()
    issues: list[dict[str, str]] = []
    if not parser.title: issues.append({"level": "fail", "code": "missing_title"})
    if not parser.description: issues.append({"level": "fail", "code": "missing_description"})
    if not parser.h1: issues.append({"level": "fail", "code": "missing_h1"})
    canonical = urljoin(url, parser.canonical) if parser.canonical else ""
    if not canonical: issues.append({"level": "fail", "code": "missing_canonical"})
    elif canonical.rstrip("/") != url.rstrip("/"): issues.append({"level": "warn", "code": "canonical_mismatch", "value": canonical})
    if sitemap_urls and url.rstrip("/") not in {item.rstrip("/") for item in sitemap_urls}: issues.append({"level": "warn", "code": "missing_sitemap_membership"})
    broken = []
    for href in parser.links:
        target = urljoin(url, href)
        parsed = urlparse(target)
        if parsed.netloc == urlparse(url).netloc and parsed.path and known_urls and target.split("#", 1)[0].rstrip("/") not in {item.rstrip("/") for item in known_urls}:
            broken.append(target)
    if broken: issues.append({"level": "fail", "code": "broken_internal_links", "value": ",".join(broken)})
    return {"url": url, "status": "fail" if any(i["level"] == "fail" for i in issues) else "warn" if issues else "pass", "title": parser.title, "description": parser.description, "h1": parser.h1, "canonical": canonical, "issues": issues}


def fetch(url: str) -> tuple[int, str]:
    request = Request(url, headers={"User-Agent": "MARKETSHELPER-seo-audit/1.0"})
    with urlopen(request, timeout=15) as response:
        return int(response.status), response.read().decode("utf-8", errors="replace")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("urls", nargs="*", default=["https://marketshelper.ru/", "https://marketshelper.ru/sebestoimost-wb", "https://marketshelper.ru/calculator", "https://marketshelper.ru/pricing"])
    parser.add_argument("--sitemap", default="https://marketshelper.ru/sitemap.xml")
    args = parser.parse_args()
    sitemap_urls: set[str] = set()
    try:
        _, sitemap = fetch(args.sitemap)
        sitemap_urls = {line.strip() for line in sitemap.splitlines() if line.strip().startswith("http")}
    except Exception:
        pass
    results = []
    for url in args.urls:
        try:
            status, html = fetch(url)
            result = audit_html(html, url, sitemap_urls=sitemap_urls)
            result["http_status"] = status
        except Exception as exc:
            result = {"url": url, "status": "fail", "issues": [{"level": "fail", "code": "fetch_error", "value": str(exc)}]}
        results.append(result)
    print(json.dumps({"results": results}, ensure_ascii=True, indent=2))
    return 1 if any(item["status"] == "fail" for item in results) else 0


if __name__ == "__main__": raise SystemExit(main())
