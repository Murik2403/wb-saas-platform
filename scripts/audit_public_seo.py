#!/usr/bin/env python3
"""Read-only health audit for the public, crawler-facing website.

The audit discovers public routes from ``/sitemap.xml`` and performs ordinary
GET requests only.  It does not log in, submit forms, create accounts, or
write to the remote application.  Results are emitted as JSON with individual
``pass``, ``warn`` and ``fail`` check records so a CI job or a human can act on
the same evidence.

Examples::

    python scripts/audit_public_seo.py --base-url https://marketshelper.ru
    python scripts/audit_public_seo.py --base-url http://127.0.0.1:8000 --output audit.json
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
import json
import os
import re
import sys
from typing import Callable, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urldefrag, urljoin, urlsplit
from urllib.request import Request, urlopen


USER_AGENT = "marketshelper-public-seo-audit/1.0 (read-only)"
DEFAULT_TIMEOUT = 20


@dataclass(frozen=True)
class FetchResult:
    status: int
    final_url: str
    body: str
    content_type: str = ""


Fetcher = Callable[[str], FetchResult]


def fetch_url(url: str, timeout: int = DEFAULT_TIMEOUT) -> FetchResult:
    """GET *url* and return a non-throwing result, including network errors."""
    request = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(request, timeout=timeout) as response:
            return FetchResult(
                int(response.status),
                response.geturl(),
                response.read().decode("utf-8", "replace"),
                response.headers.get("content-type", ""),
            )
    except HTTPError as error:
        return FetchResult(
            int(error.code),
            error.geturl(),
            error.read().decode("utf-8", "replace"),
            error.headers.get("content-type", "") if error.headers else "",
        )
    except (OSError, URLError) as error:
        return FetchResult(0, f"{url} ({type(error).__name__}: {error})", "", "")


class _PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.description: str | None = None
        self.robots: str | None = None
        self.canonicals: list[str] = []
        self.h1_parts: list[list[str]] = []
        self.links: list[str] = []
        self.jsonld: list[str] = []
        self._title = False
        self._h1: list[str] | None = None
        self._jsonld: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.lower(): (value or "") for key, value in attrs}
        name = tag.lower()
        if name == "title":
            self._title = True
        elif name == "h1":
            self._h1 = []
            self.h1_parts.append(self._h1)
        elif name == "a" and values.get("href"):
            self.links.append(values["href"])
        elif name == "meta" and values.get("name", "").lower() == "description":
            self.description = values.get("content", "").strip()
        elif name == "meta" and values.get("name", "").lower() == "robots":
            self.robots = values.get("content", "").strip()
        elif name == "link" and "canonical" in values.get("rel", "").lower().split():
            self.canonicals.append(values.get("href", "").strip())
        elif name == "script" and values.get("type", "").lower() == "application/ld+json":
            self._jsonld = []

    def handle_endtag(self, tag: str) -> None:
        name = tag.lower()
        if name == "title":
            self._title = False
        elif name == "h1":
            self._h1 = None
        elif name == "script" and self._jsonld is not None:
            self.jsonld.append("".join(self._jsonld).strip())
            self._jsonld = None

    def handle_data(self, data: str) -> None:
        if self._title:
            self.title_parts.append(data)
        if self._h1 is not None:
            self._h1.append(data)
        if self._jsonld is not None:
            self._jsonld.append(data)


def parse_html(body: str) -> dict[str, object]:
    parser = _PageParser()
    parser.feed(body)
    jsonld_types: list[str] = []
    faq_count = 0
    invalid_jsonld = 0
    for raw in parser.jsonld:
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            invalid_jsonld += 1
            continue
        nodes: Iterable[object]
        if isinstance(data, dict) and isinstance(data.get("@graph"), list):
            nodes = data["@graph"]
        elif isinstance(data, list):
            nodes = data
        else:
            nodes = [data]
        for node in nodes:
            if isinstance(node, dict):
                kind = node.get("@type")
                kinds = kind if isinstance(kind, list) else [kind]
                for value in kinds:
                    if value:
                        jsonld_types.append(str(value))
                        if str(value).lower() == "faqpage":
                            faq_count += 1
    return {
        "title": " ".join("".join(parser.title_parts).split()),
        "description": parser.description or "",
        "robots": parser.robots or "",
        "canonical": parser.canonicals[0] if parser.canonicals else "",
        "canonical_count": len(parser.canonicals),
        "h1": [" ".join("".join(parts).split()) for parts in parser.h1_parts],
        "links": parser.links,
        "faq_jsonld": faq_count,
        "jsonld_types": sorted(jsonld_types),
        "invalid_jsonld": invalid_jsonld,
    }


def _paths_from_sitemap(base_url: str, body: str) -> list[str]:
    paths: list[str] = []
    for raw in re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", body, flags=re.I):
        url = urldefrag(raw.strip()).url
        parsed = urlsplit(url)
        if parsed.netloc and parsed.netloc.lower() != urlsplit(base_url).netloc.lower():
            continue
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query
        if path not in paths:
            paths.append(path)
    return paths


def _check(checks: list[dict[str, object]], state: str, scope: str, name: str, detail: str) -> None:
    checks.append({"status": state, "scope": scope, "check": name, "detail": detail})


def audit_public_site(base_url: str, fetcher: Fetcher = fetch_url) -> dict[str, object]:
    """Run the public audit against *base_url* using an injectable GET fetcher."""
    base = base_url.rstrip("/")
    checks: list[dict[str, object]] = []
    pages: dict[str, dict[str, object]] = {}

    robots_url = base + "/robots.txt"
    robots = fetcher(robots_url)
    _check(checks, "pass" if robots.status == 200 else "fail", "/robots.txt", "status", f"HTTP {robots.status}")
    robots_body = robots.body
    sitemap_ref = next((line.split(":", 1)[1].strip() for line in robots_body.splitlines() if line.lower().startswith("sitemap:")), "")
    _check(checks, "pass" if sitemap_ref == base + "/sitemap.xml" else "fail", "/robots.txt", "sitemap directive", sitemap_ref or "missing")
    if "User-agent: *" not in robots_body:
        _check(checks, "warn", "/robots.txt", "user-agent", "missing User-agent: *")
    else:
        _check(checks, "pass", "/robots.txt", "user-agent", "User-agent: * present")

    sitemap_url = base + "/sitemap.xml"
    sitemap = fetcher(sitemap_url)
    _check(checks, "pass" if 200 <= sitemap.status < 300 else "fail", "/sitemap.xml", "status", f"HTTP {sitemap.status}")
    paths = _paths_from_sitemap(base, sitemap.body) if 200 <= sitemap.status < 300 else []
    _check(checks, "pass" if paths else "fail", "/sitemap.xml", "public routes", f"{len(paths)} URL(s) discovered")
    sitemap_urls = {urljoin(base + "/", path) for path in paths}

    internal_targets: dict[str, set[str]] = {}
    for path in paths:
        url = urljoin(base + "/", path)
        result = fetcher(url)
        page = parse_html(result.body) if result.body else {}
        pages[path] = {"url": url, "status": result.status, "final_url": result.final_url, **page}
        scope = path
        _check(checks, "pass" if 200 <= result.status < 300 else "fail", scope, "status", f"HTTP {result.status}")
        if not page:
            continue
        canonical = str(page.get("canonical", ""))
        expected = urljoin(base + "/", path)
        if canonical == expected and int(page.get("canonical_count", 0)) == 1:
            _check(checks, "pass", scope, "canonical", canonical)
        else:
            _check(checks, "fail", scope, "canonical", f"expected {expected}; got {canonical or 'missing'}")
        title = str(page.get("title", ""))
        _check(checks, "pass" if title else "fail", scope, "title", title or "missing")
        description = str(page.get("description", ""))
        _check(checks, "pass" if description else "fail", scope, "description", description or "missing")
        h1 = page.get("h1", [])
        if len(h1) == 1:
            _check(checks, "pass", scope, "h1", str(h1[0]))
        elif len(h1) == 0:
            _check(checks, "fail", scope, "h1", "missing")
        else:
            _check(checks, "warn", scope, "h1", f"{len(h1)} headings")
        robots_directive = str(page.get("robots", "")).lower()
        if "noindex" in robots_directive:
            _check(checks, "fail", scope, "robots", robots_directive)
        elif "index" in robots_directive:
            _check(checks, "pass", scope, "robots", robots_directive)
        else:
            _check(checks, "warn", scope, "robots", "missing index directive")
        if int(page.get("invalid_jsonld", 0)):
            _check(checks, "fail", scope, "FAQ JSON-LD", "invalid JSON-LD block")
        elif int(page.get("faq_jsonld", 0)):
            _check(checks, "pass", scope, "FAQ JSON-LD", f"{page['faq_jsonld']} FAQPage block(s)")
        else:
            _check(checks, "warn", scope, "FAQ JSON-LD", "FAQPage not present")
        if url in sitemap_urls:
            _check(checks, "pass", scope, "sitemap membership", "listed in sitemap")
        else:
            _check(checks, "fail", scope, "sitemap membership", "not listed in sitemap")
        targets: set[str] = set()
        for href in page.get("links", []):
            absolute = urljoin(url, str(href))
            parsed = urlsplit(urldefrag(absolute).url)
            if parsed.scheme in {"http", "https"} and parsed.netloc.lower() == urlsplit(base).netloc.lower():
                targets.add(parsed.path or "/")
        internal_targets[path] = targets

    checked_links: set[str] = set()
    for source, targets in internal_targets.items():
        for target in sorted(targets):
            if target in checked_links or target in paths:
                if target in paths:
                    _check(checks, "pass", source, "internal link", f"{target} listed public route")
                continue
            checked_links.add(target)
            result = fetcher(urljoin(base + "/", target))
            if 200 <= result.status < 400:
                _check(checks, "pass", source, "internal link", f"{target} HTTP {result.status}")
            else:
                _check(checks, "fail", source, "internal link", f"{target} HTTP {result.status}")

    summary = {state: sum(1 for item in checks if item["status"] == state) for state in ("pass", "warn", "fail")}
    return {
        "base_url": base,
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "summary": summary,
        "checks": checks,
        "pages": pages,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only public SEO health audit")
    parser.add_argument("--base-url", default=None, help="default: $WB_SAAS_PARENT_DOMAIN or https://marketshelper.ru")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    parser.add_argument("--output", help="also save JSON locally at this path")
    args = parser.parse_args(argv)
    domain = args.base_url or os.environ.get("WB_SAAS_PARENT_DOMAIN", "marketshelper.ru")
    base = domain.rstrip("/") if "://" in domain else f"{'http' if domain.startswith(('localhost', '127.0.0.1')) else 'https'}://{domain.rstrip('/') }"
    result = audit_public_site(base, lambda url: fetch_url(url, args.timeout))
    encoded = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(encoded)
    # PowerShell on this host may expose a cp1251 stdout.  Write UTF-8 bytes
    # directly so Russian titles and descriptions never make the audit crash.
    sys.stdout.buffer.write(encoded.encode("utf-8"))
    return 1 if result["summary"]["fail"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
