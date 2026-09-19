#!/usr/bin/env python3
"""Read-only probe: which WB API endpoints answer for this seller's token.

Why this exists: some seller-analytics reports are gated behind WB's own
"Джем" subscription. Whether an endpoint is gated cannot be read off the
documentation -- it shows up only as the response the token actually gets.
So: take one snapshot while the subscription is active, take another after
it lapses, and diff them. The difference is the honest answer to "what can
we build on our side without the customer paying WB for Джем".

    set WB_API_TOKEN=...                      (do not pass it on the CLI)
    python scripts/wb_api_probe.py --label with-jam
    ... after the subscription lapses ...
    python scripts/wb_api_probe.py --label without-jam
    python scripts/wb_api_probe.py --compare probe-with-jam.json probe-without-jam.json

Safety: reads only. Several WB report endpoints are POST-with-a-body by
design ("get me this report"), and those are the only POSTs here -- none of
them creates, updates or deletes anything. Report *generation*
(POST /api/v2/nm-report/downloads) is deliberately left out: it would spend
part of the 20-reports-per-day quota.
The token is read from the environment, never logged and never stored in
the snapshot.
"""
from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

# Paths taken from tenant-app/wb_api.py and agents/marketplaces/wb_client.py --
# i.e. calls this product already makes, not guesses from documentation.
# layer: "baseline"  -- must work with any valid token; proves the token is fine
#        "own"       -- the seller's own data; what we already build on
#        "gated?"    -- suspected to depend on the Джем subscription
#        "gated"     -- documented by WB as Джем-only
PROBES: tuple[dict, ...] = (
    {"id": "ping", "layer": "baseline", "method": "GET",
     "url": "https://common-api.wildberries.ru/ping"},
    {"id": "orders", "layer": "own", "method": "GET",
     "url": "https://statistics-api.wildberries.ru/api/v1/supplier/orders",
     "params": {"dateFrom": "{date_from}", "flag": "0"}},
    {"id": "sales", "layer": "own", "method": "GET",
     "url": "https://statistics-api.wildberries.ru/api/v1/supplier/sales",
     "params": {"dateFrom": "{date_from}", "flag": "0"}},
    {"id": "balance", "layer": "own", "method": "GET",
     "url": "https://finance-api.wildberries.ru/api/v1/account/balance"},
    {"id": "prices", "layer": "own", "method": "GET",
     "url": "https://discounts-prices-api.wildberries.ru/api/v2/list/goods/filter",
     "params": {"limit": "10", "offset": "0"}},
    {"id": "adv-count", "layer": "own", "method": "GET",
     "url": "https://advert-api.wildberries.ru/adv/v1/promotion/count"},
    {"id": "feedbacks", "layer": "own", "method": "GET",
     "url": "https://feedbacks-api.wildberries.ru/api/v1/feedbacks",
     "params": {"isAnswered": "true", "take": "1", "skip": "0"}},
    {"id": "acceptance-coef", "layer": "own", "method": "GET",
     "url": "https://common-api.wildberries.ru/api/tariffs/v1/acceptance/coefficients"},
    {"id": "cards-list", "layer": "own", "method": "POST",
     "url": "https://content-api.wildberries.ru/content/v2/get/cards/list",
     "body": {"settings": {"cursor": {"limit": 1}, "filter": {"withPhoto": -1}}}},
    # Analytics. Everything below needs a token of the "Аналитика" category --
    # a 401 here with a 200 on /ping means the token lacks that category, not
    # that the report is paid for.
    {"id": "stocks-wb", "layer": "own", "method": "POST",
     "url": "https://seller-analytics-api.wildberries.ru/api/analytics/v1/stocks-report/wb-warehouses",
     "body": {"limit": 1, "offset": 0}},
    {"id": "order-feed", "layer": "own", "method": "POST",
     "url": "https://seller-analytics-api.wildberries.ru/api/analytics/v1/order-feed",
     "body": {"selectedPeriod": {"start": "{date_from}T00:00:00.00000+03:00",
                                 "end": "{date_to}T00:00:00.00000+03:00"},
              "pagination": {"offset": 0, "limit": 1}}},
    {"id": "item-rating", "layer": "own", "method": "POST",
     "url": "https://seller-analytics-api.wildberries.ru/api/analytics/v2/item-rating",
     "body": {"currentPeriod": {"start": "{date_from}", "end": "{date_to}"},
              "orderBy": {"field": "feedbackCount", "mode": "desc"},
              "limit": 1, "offset": 0}},
    # Documented as available without Джем, but the funnel is where WB adds
    # paid extras (показы, CTR), so it is worth watching for a 402.
    {"id": "sales-funnel", "layer": "gated?", "method": "POST",
     "url": "https://seller-analytics-api.wildberries.ru/api/analytics/v3/sales-funnel/products",
     "body": {"selectedPeriod": {"start": "{date_from}", "end": "{date_to}"},
              "orderBy": {"field": "openCard", "mode": "desc"},
              "limit": 1, "offset": 0}},
    # Documented Джем-only ("Вы можете использовать эти методы только с
    # подпиской Джем"). With the subscription live these should answer; after
    # it lapses they are the ones expected to turn into 402.
    {"id": "search-report", "layer": "gated", "method": "POST",
     "url": "https://seller-analytics-api.wildberries.ru/api/v2/search-report/report",
     "body": {"currentPeriod": {"start": "{date_from}", "end": "{date_to}"},
              "positionCluster": "all",
              "orderBy": {"field": "avgPosition", "mode": "asc"},
              "limit": 1, "offset": 0}},
    # GET only lists existing report jobs -- it does not generate one, so no
    # part of the 20-reports-per-day quota is spent here.
    {"id": "csv-downloads-list", "layer": "gated", "method": "GET",
     "url": "https://seller-analytics-api.wildberries.ru/api/v2/nm-report/downloads"},
)

MEANING = {
    200: "доступен",
    204: "доступен, но данных нет",
    401: "токен недействителен или нет категории «Аналитика»",
    402: "требуется оплата — это и есть гейт подписки Джем",
    403: "доступ закрыт (подписка, права токена или тариф)",
    404: "путь не существует — проверить сам адрес, это не про подписку",
    429: "лимит запросов — повторить позже, вывод недостоверен",
}


def classify(status: int | None, error: str | None = None) -> str:
    """Turn an HTTP status into the one thing we actually want to know."""
    if status is None:
        return f"нет ответа: {error or 'unknown'}"
    if status in MEANING:
        return MEANING[status]
    if 500 <= status < 600:
        return "ошибка на стороне WB — повторить позже"
    return f"HTTP {status}"


def _fill(value, subs: dict[str, str]):
    if isinstance(value, str):
        for key, replacement in subs.items():
            value = value.replace("{" + key + "}", replacement)
        return value
    if isinstance(value, dict):
        return {k: _fill(v, subs) for k, v in value.items()}
    return value


def probe(entry: dict, token: str, subs: dict[str, str], timeout: float) -> dict:
    url = _fill(entry["url"], subs)
    if entry.get("params"):
        url += "?" + urllib.parse.urlencode(_fill(entry["params"], subs))
    data = None
    headers = {"Authorization": token, "Accept": "application/json"}
    if entry["method"] == "POST":
        data = json.dumps(_fill(entry.get("body", {}), subs)).encode()
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(url, data=data, headers=headers, method=entry["method"])
    started = time.monotonic()
    status: int | None = None
    error: str | None = None
    body = b""
    try:
        with urllib.request.urlopen(request, timeout=timeout,
                                    context=ssl.create_default_context()) as response:
            status = response.status
            body = response.read(20000)
    except urllib.error.HTTPError as exc:          # 4xx/5xx still carry a body worth seeing
        status = exc.code
        body = exc.read(2000)
    except Exception as exc:                        # network, DNS, TLS, timeout
        error = f"{type(exc).__name__}: {exc}"

    result = {
        "id": entry["id"],
        "layer": entry["layer"],
        "method": entry["method"],
        "url": entry["url"],
        "status": status,
        "meaning": classify(status, error),
        "ms": round((time.monotonic() - started) * 1000),
    }
    if error:
        result["error"] = error
    if body:
        try:
            parsed = json.loads(body)
        except ValueError:
            result["sample"] = body[:200].decode("utf-8", "replace")
        else:
            # Row count says whether a 200 is real data or a polite empty answer.
            rows = parsed
            for key in ("data", "cards", "report", "products"):
                if isinstance(rows, dict) and key in rows:
                    rows = rows[key]
            result["rows"] = len(rows) if isinstance(rows, (list, dict)) else None
            if status != 200:
                result["sample"] = json.dumps(parsed, ensure_ascii=False)[:200]
    return result


def run(label: str, out_path: str, delay: float, timeout: float) -> int:
    token = os.environ.get("WB_API_TOKEN", "").strip()
    if not token:
        print("WB_API_TOKEN не задан. Установите переменную окружения и повторите.\n"
              "Токен не передавайте аргументом командной строки — он попадёт в историю.",
              file=sys.stderr)
        return 2

    today = datetime.now(timezone.utc).date()
    subs = {"date_from": str(today - timedelta(days=7)), "date_to": str(today)}

    results = []
    for index, entry in enumerate(PROBES):
        if index:
            time.sleep(delay)  # WB rate-limits per endpoint group; probing is not urgent
        result = probe(entry, token, subs, timeout)
        results.append(result)
        rows = f" rows={result['rows']}" if result.get("rows") is not None else ""
        print(f"{result['status'] or '---':>4}  {result['layer']:<9} {result['id']:<18} "
              f"{result['meaning']}{rows}")

    snapshot = {
        "label": label,
        "taken_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "results": results,
    }
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump(snapshot, handle, ensure_ascii=False, indent=2)
    print(f"\nСнимок сохранён: {out_path}")
    return 0


def compare(before_path: str, after_path: str) -> int:
    with open(before_path, encoding="utf-8") as handle:
        before = json.load(handle)
    with open(after_path, encoding="utf-8") as handle:
        after = json.load(handle)

    after_by_id = {item["id"]: item for item in after["results"]}
    print(f"{before['label']} ({before['taken_at']})  ->  {after['label']} ({after['taken_at']})\n")
    changed = 0
    for item in before["results"]:
        other = after_by_id.get(item["id"])
        if other is None:
            continue
        mark = "  "
        if item["status"] != other["status"]:
            changed += 1
            mark = "!!" if item["status"] == 200 and other["status"] != 200 else "**"
        print(f"{mark} {item['id']:<18} {str(item['status']):>4} -> {str(other['status']):>4}"
              f"   {other['meaning']}")
    print(f"\nИзменений: {changed}.  !! — было доступно, стало нет: это и есть цена подписки.")
    return 0


def self_test() -> int:
    assert classify(200) == "доступен"
    assert "подписка" in classify(403)
    assert "не про подписку" in classify(404)
    assert classify(None, "timeout").startswith("нет ответа")
    assert classify(503).startswith("ошибка на стороне WB")
    assert _fill({"a": "{date_from} x"}, {"date_from": "2026-09-20"}) == {"a": "2026-09-20 x"}
    print("self-test ok")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--label", default="snapshot",
                        help="как пометить снимок, например with-jam / without-jam")
    parser.add_argument("--out", help="куда сохранить (по умолчанию probe-<label>.json)")
    parser.add_argument("--delay", type=float, default=2.0, help="пауза между запросами, с")
    parser.add_argument("--timeout", type=float, default=30.0, help="таймаут запроса, с")
    parser.add_argument("--compare", nargs=2, metavar=("BEFORE", "AFTER"),
                        help="сравнить два снимка вместо нового запуска")
    parser.add_argument("--self-test", action="store_true", help="проверить классификацию ответов")
    args = parser.parse_args()

    if args.self_test:
        return self_test()
    if args.compare:
        return compare(*args.compare)
    return run(args.label, args.out or f"probe-{args.label}.json", args.delay, args.timeout)


if __name__ == "__main__":
    raise SystemExit(main())
