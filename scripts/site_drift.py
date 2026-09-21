#!/usr/bin/env python3
"""Public-site drift monitor: catches silent SEO regressions between deploys.

Written after 20.09.2026, when the gateway container came back from a stale
image, every marketing page started answering 404 and the home page kept
answering 200 -- so nothing alerted and it was found by hand. This script
watches the class of change that incident belongs to: the site is up, but
something search cares about quietly moved.

  python3 scripts/site_drift.py --snapshot     # freeze a reference
  python3 scripts/site_drift.py                # compare, exit 1 on CRITICAL

The URL list is read from the live /sitemap.xml plus a small fixed extra
list, never from a hardcoded copy of the routes -- a page falling out of
the sitemap is itself the drift we want reported, and a hardcoded list
would hide it.

Read-only HTTP GETs, stdlib only, no page bodies in the snapshot (hashes
and the extracted fields are enough to review the file in a diff).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_SNAPSHOT = "docs/marketing/site-drift-baseline.json"
EXTRA_PATHS = ["/robots.txt", "/login", "/register"]
USER_AGENT = "marketshelper-drift-monitor/1.0 (+ops, read-only)"
TIMEOUT = 20
LENGTH_TOLERANCE = 0.30  # body length change under 30% is noise, not drift


# --------------------------------------------------------------------- fetch


def _base_url(arg: str | None) -> str:
    if arg:
        return arg.rstrip("/")
    domain = os.environ.get("WB_SAAS_PARENT_DOMAIN", "marketshelper.ru")
    if "://" in domain:
        return domain.rstrip("/")
    scheme = "http" if domain.startswith("localhost") else "https"
    return f"{scheme}://{domain}"


def fetch(url: str) -> tuple[int, str, str]:
    """Return (status, final_url, body). Never raises on an HTTP error."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            body = resp.read().decode("utf-8", "replace")
            return resp.status, resp.geturl(), body
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        return exc.code, exc.geturl(), body
    except Exception as exc:  # DNS, TLS, timeout -- drift too, just louder
        return 0, f"{url} ({type(exc).__name__}: {exc})", ""


# ------------------------------------------------------------------- extract

_TAG = re.compile(r"<[^>]+>")


def _attr(tag: str, name: str) -> str | None:
    m = re.search(rf"{name}\s*=\s*[\"']([^\"']*)[\"']", tag, re.I)
    return m.group(1).strip() if m else None


def _meta(html: str, key: str, value: str) -> str | None:
    for tag in re.findall(r"<meta\b[^>]*>", html, re.I):
        if (_attr(tag, key) or "").lower() == value:
            return _attr(tag, "content")
    return None


def extract(path: str, status: int, final_url: str, body: str) -> dict:
    page: dict = {
        "status": status,
        "final_url": final_url,
        "body_len": len(body),
        "body_hash": hashlib.sha256(body.encode("utf-8", "replace")).hexdigest()[:16],
    }

    if path == "/robots.txt":
        page["disallow"] = sorted(
            {
                line.split(":", 1)[1].strip()
                for line in body.splitlines()
                if line.lower().startswith("disallow:")
            }
        )
        return page

    if path == "/sitemap.xml":
        page["locs"] = sorted(set(re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", body, re.I)))
        return page

    title = re.search(r"<title[^>]*>(.*?)</title>", body, re.I | re.S)
    page["title"] = _TAG.sub("", title.group(1)).strip() if title else None
    page["description"] = _meta(body, "name", "description")
    page["robots"] = _meta(body, "name", "robots")
    canonical = None
    for tag in re.findall(r"<link\b[^>]*>", body, re.I):
        if (_attr(tag, "rel") or "").lower() == "canonical":
            canonical = _attr(tag, "href")
    page["canonical"] = canonical

    blocks = re.findall(
        r"<script\b[^>]*type\s*=\s*[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
        body,
        re.I | re.S,
    )
    types: list[str] = []
    for block in blocks:
        try:
            data = json.loads(block)
        except ValueError:
            types.append("<invalid-json>")
            continue
        items = data if isinstance(data, list) else [data]
        if len(items) == 1 and isinstance(items[0], dict) and "@graph" in items[0]:
            items = items[0]["@graph"]
        for item in items if isinstance(items, list) else [items]:
            if isinstance(item, dict):
                types.append(str(item.get("@type", "<no-type>")))
    page["jsonld_types"] = sorted(types)

    h1s = [
        _TAG.sub("", h).strip()
        for h in re.findall(r"<h1\b[^>]*>(.*?)</h1>", body, re.I | re.S)
    ]
    page["h1_count"] = len(h1s)
    page["h1_text"] = h1s
    return page


def collect(base: str) -> dict:
    """Fetch the sitemap, then every URL it lists plus the fixed extras."""
    status, final_url, body = fetch(base + "/sitemap.xml")
    pages = {"/sitemap.xml": extract("/sitemap.xml", status, final_url, body)}

    paths = []
    for loc in pages["/sitemap.xml"].get("locs", []):
        path = re.sub(r"^https?://[^/]+", "", loc) or "/"
        if path not in paths:
            paths.append(path)
    for path in EXTRA_PATHS:
        if path not in paths:
            paths.append(path)

    for path in paths:
        status, final_url, body = fetch(base + path)
        pages[path] = extract(path, status, final_url, body)

    return {
        "base_url": base,
        "taken_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "pages": pages,
    }


# ---------------------------------------------------------------------- diff

FIELDS_CRITICAL = ("canonical",)
FIELDS_WARNING = ("title", "description")


def diff(old: dict, new: dict) -> tuple[list[str], list[str]]:
    """Compare two snapshots. Returns (critical, warnings), both greppable."""
    critical: list[str] = []
    warning: list[str] = []
    old_pages, new_pages = old.get("pages", {}), new.get("pages", {})

    for path in sorted(set(old_pages) | set(new_pages)):
        o, n = old_pages.get(path), new_pages.get(path)
        if o is None:
            warning.append(f"{path}: new page in sitemap")
            continue
        if n is None:
            critical.append(f"{path}: dropped out of the sitemap")
            continue

        if not (200 <= n["status"] < 300):
            critical.append(f"{path}: status {o['status']} -> {n['status']}")
            continue
        if o["final_url"] != n["final_url"]:
            critical.append(f"{path}: redirects to {n['final_url']} (was {o['final_url']})")

        if path == "/robots.txt":
            gone = set(o.get("disallow", [])) - set(n.get("disallow", []))
            added = set(n.get("disallow", [])) - set(o.get("disallow", []))
            if gone:
                warning.append(f"{path}: Disallow removed: {sorted(gone)}")
            if added:
                critical.append(f"{path}: Disallow added: {sorted(added)}")
            continue

        if path == "/sitemap.xml":
            # the per-page loop above already reports pages that vanished;
            # here we catch a loc dropping while the page itself still answers
            gone = set(o.get("locs", [])) - set(n.get("locs", []))
            added = set(n.get("locs", [])) - set(o.get("locs", []))
            if gone:
                critical.append(f"{path}: URLs lost from sitemap: {sorted(gone)}")
            if added:
                warning.append(f"{path}: new URLs in sitemap: {sorted(added)}")
            continue

        robots = (n.get("robots") or "").lower()
        if "noindex" in robots and "noindex" not in (o.get("robots") or "").lower():
            critical.append(f"{path}: noindex appeared on a public page")
        if o.get("canonical") != n.get("canonical"):
            critical.append(
                f"{path}: canonical {o.get('canonical')!r} -> {n.get('canonical')!r}"
            )
        if o.get("h1_count", 0) > 0 and n.get("h1_count", 0) == 0:
            critical.append(f"{path}: h1 missing")
        if o.get("jsonld_types") and not n.get("jsonld_types"):
            critical.append(f"{path}: JSON-LD disappeared")
        elif o.get("jsonld_types") != n.get("jsonld_types"):
            warning.append(
                f"{path}: JSON-LD types {o.get('jsonld_types')} -> {n.get('jsonld_types')}"
            )

        for field in FIELDS_WARNING:
            if o.get(field) != n.get(field):
                warning.append(f"{path}: {field} changed")
        if o.get("h1_text") != n.get("h1_text") and n.get("h1_count"):
            warning.append(f"{path}: h1 text changed")

        old_len = o.get("body_len") or 0
        new_len = n.get("body_len") or 0
        if old_len and abs(new_len - old_len) / old_len > LENGTH_TOLERANCE:
            warning.append(f"{path}: body length {old_len} -> {new_len}")

    return critical, warning


# ---------------------------------------------------------------------- mail


def _send_email(to_email: str, subject: str, body: str) -> bool:
    """The gateway's own SMTP sender -- same config, no new dependency and no
    new config key. Imported lazily so the monitor keeps working (and keeps
    being stdlib-only) on a machine where the gateway package is absent."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "gateway"))
    import mailer  # noqa: E402

    status = mailer.send_email(to_email, subject, body)
    return status == "accepted" or status is True


def alert(
    addresses: list[str],
    base: str,
    baseline_at: str,
    critical: list[str],
    warning: list[str],
    send_email=_send_email,
) -> bool:
    """One message per run, critical first. No drift -> no mail, ever.

    Returns True if there was nothing to send or it went out, False if SMTP
    refused it -- the caller keeps its own exit code either way."""
    if not addresses or not (critical or warning):
        return True
    subject = (
        f"[site-drift] {len(critical)} critical, {len(warning)} warnings "
        f"— {base} (baseline {baseline_at})"
    )
    lines = [f"CRITICAL {line}" for line in critical]
    lines += [f"WARNING {line}" for line in warning]
    body = (
        f"Дрейф публичного сайта {base}.\n"
        f"Базовый снимок: {baseline_at}.\n\n" + "\n".join(lines) + "\n"
    )
    # One message, all recipients in one To: header -- an hourly cron must
    # not multiply a drift into one mail per address.
    return bool(send_email(", ".join(addresses), subject, body))


# ----------------------------------------------------------------- self-test


def self_test() -> int:
    """One runnable check: synthetic before/after through diff(). No network."""

    def page(**over):
        base = {
            "status": 200,
            "final_url": "https://x/p",
            "body_len": 1000,
            "body_hash": "a",
            "title": "T",
            "description": "D",
            "robots": None,
            "canonical": "https://x/p",
            "jsonld_types": ["Article"],
            "h1_count": 1,
            "h1_text": ["H"],
        }
        base.update(over)
        return base

    def snap(pages):
        return {"base_url": "https://x", "taken_at": "t", "pages": pages}

    def check(label, old, new, *, crit=(), warn=()):
        c, w = diff(snap(old), snap(new))
        for frag in crit:
            assert any(frag in line for line in c), f"{label}: missing critical {frag!r}: {c}"
        for frag in warn:
            assert any(frag in line for line in w), f"{label}: missing warning {frag!r}: {w}"
        if not crit:
            assert not c, f"{label}: unexpected critical {c}"
        if not warn:
            assert not w, f"{label}: unexpected warning {w}"
        print(f"  ok  {label}")

    base = {"/p": page()}
    check("clean run", base, {"/p": page()})
    check("404 (the 20.09 incident)", base, {"/p": page(status=404)}, crit=["status 200 -> 404"])
    check("unreachable", base, {"/p": page(status=0)}, crit=["-> 0"])
    check(
        "new redirect",
        base,
        {"/p": page(final_url="https://x/other")},
        crit=["redirects to"],
        warn=[],
    )
    check("noindex appeared", base, {"/p": page(robots="noindex, follow")}, crit=["noindex appeared"])
    check("canonical changed", base, {"/p": page(canonical="https://x/q")}, crit=["canonical"])
    check("canonical missing", base, {"/p": page(canonical=None)}, crit=["canonical"])
    check("h1 missing", base, {"/p": page(h1_count=0, h1_text=[])}, crit=["h1 missing"])
    check("json-ld gone", base, {"/p": page(jsonld_types=[])}, crit=["JSON-LD disappeared"])
    check("page vanished", base, {}, crit=["dropped out of the sitemap"])
    check("new page", {}, base, warn=["new page in sitemap"])
    check("title changed", base, {"/p": page(title="T2")}, warn=["title changed"])
    check("description changed", base, {"/p": page(description="D2")}, warn=["description changed"])
    check("body shrank 50%", base, {"/p": page(body_len=500)}, warn=["body length"])
    check("body wobbled 10%", base, {"/p": page(body_len=1100)})

    sm = {"/sitemap.xml": {"status": 200, "final_url": "https://x/sitemap.xml",
                           "body_len": 10, "body_hash": "a",
                           "locs": ["https://x/a", "https://x/b"]}}
    sm_lost = json.loads(json.dumps(sm))
    sm_lost["/sitemap.xml"]["locs"] = ["https://x/a"]
    check("sitemap lost a URL", sm, sm_lost, crit=["URLs lost from sitemap"])
    check("sitemap gained a URL", sm_lost, sm, warn=["new URLs in sitemap"])

    rb = {"/robots.txt": {"status": 200, "final_url": "https://x/robots.txt",
                          "body_len": 10, "body_hash": "a", "disallow": ["/admin"]}}
    rb_more = json.loads(json.dumps(rb))
    rb_more["/robots.txt"]["disallow"] = ["/admin", "/guides"]
    check("robots Disallow added", rb, rb_more, crit=["Disallow added"])
    check("robots Disallow removed", rb_more, rb, warn=["Disallow removed"])

    # --- the alert path, with the mailer replaced by a recorder ----------
    sent: list[tuple[str, str, str]] = []

    def fake_send(to, subject, body):
        sent.append((to, subject, body))
        return True

    alert(["ops@x"], "https://x", "2026-09-20T00:00:00+00:00", [], [], send_email=fake_send)
    assert sent == [], f"clean run must not mail: {sent}"
    print("  ok  no drift -> no mail")

    crit, warn = diff(snap(base), snap({"/p": page(status=404)}))
    alert(["ops@x", "ops2@x"], "https://x", "2026-09-20T00:00:00+00:00", crit, warn,
          send_email=fake_send)
    assert len(sent) == 1, f"exactly one message expected, got {len(sent)}"
    to, subject, body = sent[0]
    assert to == "ops@x, ops2@x", to
    assert "https://x" in subject and "2026-09-20" in subject, subject
    for line in crit:
        assert f"CRITICAL {line}" in body, f"missing critical line in mail: {line}"
    assert body.index("CRITICAL") < (body.index("WARNING") if "WARNING" in body else len(body))
    print("  ok  drift -> exactly one message, critical first")

    sent.clear()
    alert([], "https://x", "t", crit, warn, send_email=fake_send)
    assert sent == [], "no --email means no mail"
    print("  ok  no --email -> no mail")

    print("self-test: all cases classified correctly")
    return 0


# ---------------------------------------------------------------------- main


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--base-url", help="default: $WB_SAAS_PARENT_DOMAIN or marketshelper.ru")
    ap.add_argument("--snapshot", action="store_true", help="write a reference snapshot instead of comparing")
    ap.add_argument("--file", default=DEFAULT_SNAPSHOT, help=f"snapshot path (default {DEFAULT_SNAPSHOT})")
    ap.add_argument("--self-test", action="store_true", help="run the diff self-test, no network")
    ap.add_argument("--email", action="append", default=[],
                    help="alert address; repeatable or comma-separated. Mail only when there is drift.")
    args = ap.parse_args(argv)

    if args.self_test:
        return self_test()

    base = _base_url(args.base_url)
    current = collect(base)

    if args.snapshot:
        bad = [p for p, d in current["pages"].items() if not 200 <= d["status"] < 300]
        if bad:
            print(f"REFUSING to snapshot a broken site, non-2xx: {sorted(bad)}")
            return 1
        with open(args.file, "w", encoding="utf-8") as fh:
            json.dump(current, fh, ensure_ascii=False, indent=2, sort_keys=True)
            fh.write("\n")
        print(f"snapshot: {len(current['pages'])} URLs from {base} -> {args.file}")
        return 0

    try:
        with open(args.file, encoding="utf-8") as fh:
            baseline = json.load(fh)
    except FileNotFoundError:
        print(f"CRITICAL no snapshot at {args.file}; run --snapshot after a verified deploy")
        return 1

    critical, warning = diff(baseline, current)
    for line in critical:
        print(f"CRITICAL {line}")
    for line in warning:
        print(f"WARNING {line}")

    addresses = [a.strip() for chunk in args.email for a in chunk.split(",") if a.strip()]
    # The exit code is decided first: a mail failure is reported, never
    # allowed to hide the drift that triggered it.
    code = 1 if critical else 0
    if not alert(addresses, base, str(baseline.get("taken_at")), critical, warning):
        print("CRITICAL alert email was refused by SMTP -- see the gateway mailer log")
        code = 1

    if critical:
        print(f"site drift: {len(critical)} critical, {len(warning)} warnings (baseline {baseline.get('taken_at')})")
    elif warning:
        print(f"site drift: no critical, {len(warning)} warnings")
    else:
        print(f"no drift: {len(current['pages'])} URLs match baseline {baseline.get('taken_at')}")
    return code


if __name__ == "__main__":
    sys.exit(main())
