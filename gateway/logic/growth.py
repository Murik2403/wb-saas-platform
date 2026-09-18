"""Small first-party lifecycle ledger with explicit, mature cohort metrics.

Only trusted server boundaries call this module. It accepts no arbitrary
URLs, identities, customer records or provider tokens as event properties.
It does not send data to an advertising or analytics service.
"""
from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

import config

UTM_FIELDS = ("utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term")
_TAG = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}\Z")
_SECRET_LIKE = re.compile(r"(?:sk[-_]|bearer|token[-_]|secret[-_]|eyj)|[a-f0-9]{24,}", re.IGNORECASE)
EVENT_NAMES = frozenset({
    "account_created", "email_verified", "tenant_ready", "trial_started",
    "wb_connected", "first_sync_succeeded", "cost_setup_completed",
    "first_report_viewed", "first_useful_report", "first_production_result",
    "payment_succeeded", "renewal_succeeded", "subscription_cancelled",
})
ACTIVATION_EVENTS = frozenset({"first_useful_report", "first_production_result"})
EXCLUSION_REASONS = frozenset({"", "demo", "employee", "test", "bot", "duplicate"})
_PAYMENT_EVENTS = frozenset({"payment_succeeded", "renewal_succeeded"})


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(value: datetime | None) -> datetime:
    value = value or _now()
    if not isinstance(value, datetime):
        raise ValueError("A server datetime is required")
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return _utc(value).isoformat(timespec="seconds")


def _parse(value: str) -> datetime | None:
    try:
        return _utc(datetime.fromisoformat(value)) if value else None
    except (ValueError, TypeError):
        return None


def sanitize_attribution(values: Mapping[str, Any] | None) -> dict[str, str]:
    """Campaign labels only, not free text/search queries or full URLs.

Campaign creators use short lowercase labels. Discard invalid fields rather
than silently truncating a secret or turning a URL into a plausible label.
"""
    clean = {}
    for field in UTM_FIELDS:
        value = (values or {}).get(field)
        if not isinstance(value, str):
            continue
        value = value.strip().lower()
        if _TAG.fullmatch(value) and not _SECRET_LIKE.search(value):
            clean[field] = value
    return clean


def _metadata(values: Mapping[str, Any] | None) -> dict[str, Any]:
    values = values or {}
    clean: dict[str, Any] = {}
    if values.get("scenario") in {"financial", "production"}:
        clean["scenario"] = values["scenario"]
    payment_id = values.get("payment_id")
    if isinstance(payment_id, int) and not isinstance(payment_id, bool) and payment_id > 0:
        clean["payment_id"] = payment_id
    return clean


def record_event(
    conn, account_id: int, event_name: str, *, event_key: str = "",
    occurred_at: datetime | None = None, metadata: Mapping[str, Any] | None = None,
) -> bool:
    """Record a durable server fact once; return False for duplicate retries.

Lifecycle milestones use a fixed empty key. Only payment events accept the
internal integer payments.id as their key (never a provider payment token).
"""
    if event_name not in EVENT_NAMES:
        raise ValueError("Unsupported growth event")
    if event_name in _PAYMENT_EVENTS:
        if not str(event_key).isdigit() or int(event_key) <= 0:
            raise ValueError("Payment event requires an internal payment id")
        event_key = str(int(event_key))
    elif event_key:
        raise ValueError("Lifecycle milestones do not accept variable keys")
    result = conn.execute(
        "INSERT INTO growth_events(account_id,event_name,event_key,occurred_at,metadata_json) "
        "VALUES (?,?,?,?,?) ON CONFLICT(account_id,event_name,event_key) DO NOTHING",
        (int(account_id), event_name, event_key, _iso(occurred_at), json.dumps(_metadata(metadata), sort_keys=True)),
    )
    return result.rowcount == 1


def capture_registration_source(
    conn, account_id: int, values: Mapping[str, Any] | None = None, *,
    consent: bool = False, captured_at: datetime | None = None,
    touch_at: datetime | None = None, exclusion_reason: str = "",
) -> None:
    """Capture once at account creation; captured_at is the signed touch date.

The account_created event always uses the current server time. An older
touch must not backdate registration or restart its attribution window.
Invited team members are not new organizations and must not call this API.
"""
    if exclusion_reason not in EXCLUSION_REASONS:
        raise ValueError("Unsupported growth exclusion")
    now = _now()
    clean = sanitize_attribution(values) if consent else {}
    captured_at = touch_at if touch_at is not None else captured_at
    touch = _utc(captured_at) if clean and captured_at is not None else None
    if touch is None or touch > _utc(now):
        clean, touch = {}, None
    conn.execute(
        "INSERT INTO growth_sources(account_id,attribution_json,touch_at,registered_at,exclusion_reason) "
        "VALUES (?,?,?,?,?) ON CONFLICT(account_id) DO UPDATE SET "
        "attribution_json=excluded.attribution_json,touch_at=excluded.touch_at,registered_at=excluded.registered_at "
        "WHERE growth_sources.registered_at=''",
        (int(account_id), json.dumps(clean, sort_keys=True), _iso(touch) if touch else "", _iso(now), exclusion_reason),
    )
    record_event(conn, account_id, "account_created", occurred_at=now)


def set_account_exclusion(conn, account_id: int, reason: str) -> None:
    """Explicitly classify demo/staff/test traffic; an empty reason restores it."""
    if reason not in EXCLUSION_REASONS:
        raise ValueError("Unsupported growth exclusion")
    conn.execute(
        "INSERT INTO growth_sources(account_id,exclusion_reason) VALUES (?,?) "
        "ON CONFLICT(account_id) DO UPDATE SET exclusion_reason=excluded.exclusion_reason",
        (int(account_id), reason),
    )


def record_trial_started(conn, account_id: int, *, occurred_at: datetime | None = None) -> bool:
    trial = _utc(occurred_at)
    inserted = record_event(conn, account_id, "trial_started", occurred_at=trial)
    if not inserted:
        return False
    source = conn.execute("SELECT * FROM growth_sources WHERE account_id=?", (int(account_id),)).fetchone()
    clean: dict[str, str] = {}
    status = "unknown"
    if source:
        touch = _parse(source["touch_at"])
        if touch and timedelta(0) <= trial - touch <= timedelta(days=30):
            clean = sanitize_attribution(json.loads(source["attribution_json"]))
            status = "attributed" if clean.get("utm_source") else "unknown"
        elif touch:
            status = "outside_window"
    conn.execute(
        "INSERT INTO growth_sources(account_id,trial_at,trial_attribution_json,attribution_status) "
        "VALUES (?,?,?,?) ON CONFLICT(account_id) DO UPDATE SET "
        "trial_at=excluded.trial_at,trial_attribution_json=excluded.trial_attribution_json,"
        "attribution_status=excluded.attribution_status WHERE growth_sources.trial_at=''",
        (int(account_id), _iso(trial), json.dumps(clean, sort_keys=True), status),
    )
    return True


def record_activation(conn, account_id: int, scenario: str, *, occurred_at: datetime | None = None) -> bool:
    """Call only after the server validates both readiness and report viewing."""
    events = {"financial": "first_useful_report", "production": "first_production_result"}
    if scenario not in events:
        raise ValueError("Unsupported activation scenario")
    return record_event(conn, account_id, events[scenario], occurred_at=occurred_at, metadata={"scenario": scenario})


def record_payment_success(conn, account_id: int, payment_id: int, *, is_test: bool = False, occurred_at: datetime | None = None) -> None:
    """Observe an already-applied payment; do not alter the billing state."""
    payment = conn.execute("SELECT * FROM payments WHERE id=? AND account_id=?", (int(payment_id), int(account_id))).fetchone()
    if payment is None or payment["status"] != "succeeded":
        return
    if is_test:
        conn.execute(
            "INSERT INTO growth_payment_exclusions(payment_id,reason) VALUES (?,'test') "
            "ON CONFLICT(payment_id) DO NOTHING", (int(payment_id),),
        )
        return
    amount = float(payment["amount_rub"])
    if not math.isfinite(amount) or amount <= 0:
        return
    excluded = conn.execute("SELECT 1 FROM growth_payment_exclusions WHERE payment_id=?", (int(payment_id),)).fetchone()
    if excluded:
        return
    record_event(conn, account_id, "payment_succeeded", event_key=str(payment_id), occurred_at=occurred_at, metadata={"payment_id": int(payment_id)})
    previous = conn.execute(
        "SELECT p.id FROM payments p WHERE p.account_id=? AND p.id<>? AND p.status='succeeded' AND p.amount_rub>0 "
        "AND NOT EXISTS (SELECT 1 FROM growth_payment_exclusions x WHERE x.payment_id=p.id) "
        "AND NOT EXISTS (SELECT 1 FROM refunds r WHERE r.payment_id=p.id AND r.status='succeeded') LIMIT 1",
        (int(account_id), int(payment_id)),
    ).fetchone()
    if previous:
        record_event(conn, account_id, "renewal_succeeded", event_key=str(payment_id), occurred_at=occurred_at, metadata={"payment_id": int(payment_id)})


def _metric(trials: list[tuple[int, datetime]], converted: set[int], days: int, now: datetime) -> dict[str, Any]:
    eligible = {account_id for account_id, started in trials if started + timedelta(days=days) <= now}
    count = len(eligible & converted)
    return {"eligible": len(eligible), "converted": count, "pending": len(trials) - len(eligible), "rate": count / len(eligible) if eligible else None}


def growth_summary(conn, *, now: datetime | None = None) -> dict[str, Any]:
    """First observed organizations, mature A7/P30, and no fabricated CAC.

Legacy accounts without captured registration are explicitly untracked.
Rates are fractions; None means no eligible observations, not zero success.
The financial snapshot excludes all known successful refunds and test
payments. It is not a historical accounting reconstruction.
"""
    cutoff = _utc(now)
    accounts_rows = conn.execute(
        "SELECT a.id,a.email,g.*,t.slug FROM accounts a LEFT JOIN growth_sources g ON g.account_id=a.id "
        "LEFT JOIN tenant_instances t ON t.account_id=a.id"
    ).fetchall()
    included: dict[int, dict[str, Any]] = {}
    excluded_count = untracked_count = 0
    admins = {str(email).strip().lower() for email in config.ADMIN_EMAILS}
    for item in accounts_rows:
        row = dict(item)
        if row.get("exclusion_reason") or row["email"].strip().lower() in admins or row.get("slug") == "demo":
            excluded_count += 1
            continue
        registered = _parse(row.get("registered_at") or "")
        if registered is None:
            untracked_count += 1
            continue
        if registered <= cutoff:
            included[int(row["id"])] = row

    events: dict[int, dict[str, list[tuple[datetime, str]]]] = defaultdict(lambda: defaultdict(list))
    for row in conn.execute("SELECT account_id,event_name,event_key,occurred_at FROM growth_events").fetchall():
        at = _parse(row["occurred_at"])
        if row["account_id"] in included and at and at <= cutoff:
            events[int(row["account_id"])][row["event_name"]].append((at, row["event_key"]))

    invalid_payments = {str(row["payment_id"]) for row in conn.execute("SELECT payment_id FROM growth_payment_exclusions").fetchall()}
    refunded = set()
    for row in conn.execute("SELECT payment_id,updated_at FROM refunds WHERE status='succeeded'").fetchall():
        at = _parse(row["updated_at"])
        if at and at <= cutoff:
            refunded.add(str(row["payment_id"]))
    invalid_payments |= refunded

    trials: list[tuple[int, datetime]] = []
    activated: set[int] = set()
    paid_in_30: set[int] = set()
    paid: set[int] = set()
    renewed: set[int] = set()
    cohort_trials: dict[str, list[tuple[int, datetime]]] = defaultdict(list)
    source_ids: dict[str, set[int]] = defaultdict(set)
    for account_id, row in included.items():
        own = events[account_id]
        payments = sorted((at, key) for at, key in own.get("payment_succeeded", []) if key not in invalid_payments)
        if payments:
            paid.add(account_id)
        if len(payments) >= 2:
            renewed.add(account_id)
        trial = min((at for at, _ in own.get("trial_started", [])), default=None)
        attribution = json.loads(row["trial_attribution_json"] if trial else row["attribution_json"])
        source_ids[attribution.get("utm_source") or "unknown"].add(account_id)
        if trial is None:
            continue
        trials.append((account_id, trial))
        monday = (trial - timedelta(days=trial.weekday())).date().isoformat()
        cohort_trials[monday].append((account_id, trial))
        activations = [at for event in ACTIVATION_EVENTS for at, _ in own.get(event, [])]
        if any(trial <= at < trial + timedelta(days=7) for at in activations):
            activated.add(account_id)
        if payments and trial <= payments[0][0] < trial + timedelta(days=30):
            paid_in_30.add(account_id)

    def measures(subset: list[tuple[int, datetime]]) -> dict[str, Any]:
        return {"trials": len(subset), "a7": _metric(subset, activated, 7, cutoff), "p30": _metric(subset, paid_in_30, 30, cutoff)}

    result = {
        "as_of": _iso(cutoff), "registered": len(included),
        "email_verified": sum(bool(events[i].get("email_verified")) for i in included),
        "tenant_ready": sum(bool(events[i].get("tenant_ready")) for i in included),
        "paid": len(paid), "renewed": len(renewed),
        "refunds": sum(key in refunded for i in included for _, key in events[i].get("payment_succeeded", [])),
        "excluded_accounts": excluded_count, "untracked_accounts": untracked_count,
        "cac": None, "cac_reason": "Нет подтверждённых расходов и созревшей финансовой когорты.",
        **measures(trials),
        "sources": [
            {"source": source, "registered": len(ids), "paid": len(ids & paid), "cac": None,
             **measures([trial for trial in trials if trial[0] in ids])}
            for source, ids in sorted(source_ids.items())
        ],
        "cohorts": [{"week": week, **measures(subset)} for week, subset in sorted(cohort_trials.items(), reverse=True)],
    }
    return result
