"""Read-only lifecycle funnel report for the gateway control-plane database.

The report deliberately uses durable server events and the attribution captured
at trial start. It never writes to the database and does not infer conversions
from mutable subscription state.

Examples (from the repository root)::

    python scripts/report_growth_funnel.py
    python scripts/report_growth_funnel.py --as-of 2026-09-20T00:00:00+00:00 --json
    python scripts/report_growth_funnel.py --campaign wb_cost_pilot_01
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "gateway.sqlite3"
FUNNEL_EVENTS = (
    "account_created",
    "email_verified",
    "tenant_ready",
    "trial_started",
    "first_useful_report",
    "payment_succeeded",
)


def parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def parse_optional(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return parse_time(str(value))
    except ValueError:
        return None


def open_read_only(path: Path) -> sqlite3.Connection:
    # URI mode=ro prevents accidental schema upgrades or writes by this report.
    conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def load_rows(conn: sqlite3.Connection, as_of: datetime, campaign: str | None) -> list[dict[str, Any]]:
    accounts = conn.execute(
        "SELECT a.id, a.email, g.* FROM accounts a "
        "JOIN growth_sources g ON g.account_id=a.id "
        "WHERE g.registered_at<>'' AND g.exclusion_reason=''"
    ).fetchall()
    events = defaultdict(lambda: defaultdict(list))
    for row in conn.execute(
        "SELECT account_id,event_name,event_key,occurred_at FROM growth_events WHERE occurred_at<=?",
        (iso(as_of),),
    ):
        at = parse_optional(row["occurred_at"])
        if at:
            events[int(row["account_id"])][row["event_name"]].append((at, str(row["event_key"])))

    excluded_payments = {str(row[0]) for row in conn.execute("SELECT payment_id FROM growth_payment_exclusions")}
    excluded_payments.update(str(row[0]) for row in conn.execute("SELECT payment_id FROM refunds WHERE status='succeeded'"))
    result = []
    for account in accounts:
        account_id = int(account["id"])
        own = events[account_id]
        registered = parse_optional(account["registered_at"])
        if not registered or registered > as_of:
            continue
        try:
            attribution = json.loads(account["trial_attribution_json"] or account["attribution_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            attribution = {}
        attribution = {key: str(attribution.get(key) or "") for key in ("utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term")}
        if campaign and attribution["utm_campaign"] != campaign:
            continue
        trial = parse_optional(account["trial_at"])
        useful = [at for at, _ in own["first_useful_report"]]
        payments = [at for at, key in own["payment_succeeded"] if key not in excluded_payments]
        result.append({"id": account_id, "events": own, "registered": registered, "trial": trial, "useful": useful, "payments": payments, "utm": attribution})
    return result


def event_count(rows: list[dict[str, Any]], name: str) -> int:
    return sum(bool(row["events"].get(name)) for row in rows)


def window_metric(rows: list[dict[str, Any]], days: int, field: str, as_of: datetime) -> dict[str, Any]:
    eligible = [row for row in rows if row["trial"] and row["trial"] + timedelta(days=days) <= as_of]
    converted = 0
    for row in eligible:
        end = row["trial"] + timedelta(days=days)
        values = row[field]
        if any(row["trial"] <= value < end for value in values):
            converted += 1
    return {"eligible": len(eligible), "converted": converted, "pending": len(rows) - len(eligible), "rate": (converted / len(eligible) if eligible else None)}


def make_report(rows: list[dict[str, Any]], as_of: datetime, campaign: str | None) -> dict[str, Any]:
    groups: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    fields = ("utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term")
    for row in rows:
        groups[tuple(row["utm"][field] or "unknown" for field in fields)].append(row)
    return {
        "as_of": iso(as_of),
        "campaign_filter": campaign,
        "definition": {
            "A7": "trial_started matured 7 days; first_useful_report in [trial_started, trial_started + 7 days)",
            "P30": "trial_started matured 30 days; first non-test, non-refunded payment_succeeded in [trial_started, trial_started + 30 days)",
            "attribution": "trial_attribution_json captured by the server; unknown is shown explicitly",
        },
        "funnel": {event: event_count(rows, event) for event in FUNNEL_EVENTS},
        "a7": window_metric(rows, 7, "useful", as_of),
        "p30": window_metric(rows, 30, "payments", as_of),
        "sources": [
            {**dict(zip(fields, key)), "accounts": len(group), "funnel": {event: event_count(group, event) for event in FUNNEL_EVENTS}, "a7": window_metric(group, 7, "useful", as_of), "p30": window_metric(group, 30, "payments", as_of)}
            for key, group in sorted(groups.items())
        ],
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [f"# Growth funnel (read-only)\n\nAs of: `{report['as_of']}`", "", "Definitions:"]
    lines += [f"- **A7:** {report['definition']['A7']}", f"- **P30:** {report['definition']['P30']}", f"- **Attribution:** {report['definition']['attribution']}", "", "## Funnel"]
    lines += [f"- `{event}`: {count}" for event, count in report["funnel"].items()]
    lines += ["", f"- **A7:** `{report['a7']}`", f"- **P30:** `{report['p30']}`", "", "## Source / UTM slices"]
    for source in report["sources"]:
        label = " / ".join(source[key] for key in ("utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term"))
        lines += [f"### {label}", f"Accounts: {source['accounts']}", f"Funnel: `{source['funnel']}`", f"A7: `{source['a7']}`", f"P30: `{source['p30']}`", ""]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--as-of", type=parse_time, default=datetime.now(timezone.utc))
    parser.add_argument("--campaign", help="Filter by exact captured utm_campaign")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()
    try:
        with open_read_only(args.db) as conn:
            report = make_report(load_rows(conn, args.as_of, args.campaign), args.as_of, args.campaign)
    except (OSError, sqlite3.Error) as exc:
        print(f"Cannot read gateway DB in read-only mode: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2) if args.as_json else render_markdown(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
