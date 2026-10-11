from __future__ import annotations

import argparse
import time
from decimal import Decimal, InvalidOperation
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from config import get_token, load_settings
from db import (
    finish_sync_log,
    update_sync_progress,
    init_db,
    insert_stocks,
    clear_demo_data,
    replace_product_catalog,
    replace_ads_period,
    cleanup_orphan_zero_costs,
    max_value,
    start_sync_log,
    upsert_orders,
    upsert_sales,
    upsert_financial,
    ensure_financial_schema,
    sales_fifo_tracking_status,
    initialize_sales_fifo_tracking,
    process_sales_fifo_events,
)
from wb_api import WBAPI, flatten_ad_stats
from sync_lock import sync_lock


def _record_first_sync_event() -> None:
    """Queue the first successful full-sync milestone for the tenant API."""
    try:
        from growth_activation import record_growth_event
        from config import DB_PATH
        is_first = record_growth_event(
            DB_PATH,
            event_name="first_sync_succeeded",
            occurred_at=datetime.now(timezone.utc).isoformat(),
        )
        # Deliver immediately as well as through the API background hooks.
        # This prevents a successful unattended sync from leaving the durable
        # outbox pending until the user opens another activation screen.
        try:
            from growth_delivery import deliver_pending_growth_events
            deliver_pending_growth_events(DB_PATH)
        except Exception:
            # Delivery remains retryable from the outbox and must never make a
            # successful WB import fail.
            pass
        # The outbox insert is unique per tenant, so True happens exactly once
        # per store: that is the "first successful full sync" signal.
        if is_first:
            _notify_first_sync_telegram()
    except Exception:
        # Growth measurement must never make a successful WB import fail. The
        # next sync retries the idempotent outbox insert.
        return


FIRST_SYNC_TELEGRAM_TEXT = (
    "MARKETSHELPER: данные вашего магазина загружены. "
    "Откройте кабинет и проверьте себестоимость товаров."
)


def _notify_first_sync_telegram() -> None:
    """Best-effort one-off Telegram ping; skipped silently when not linked."""
    try:
        from reports.delivery import send_text_telegram
        send_text_telegram(FIRST_SYNC_TELEGRAM_TEXT)
    except Exception:
        pass


def _incremental_from(table: str, default_days: int) -> str:
    latest = max_value(table, "last_change")
    if latest:
        try:
            parsed = datetime.fromisoformat(latest.replace("Z", "+00:00"))
            return (parsed - timedelta(days=1)).isoformat()
        except ValueError:
            pass
    return (datetime.now() - timedelta(days=default_days)).isoformat()


def _financial_incremental_range(default_days: int, overlap_days: int = 14) -> tuple[date, date]:
    """Return a safe finance refresh window without reloading all history.

    Realization rows can be corrected after their first appearance, therefore
    every automatic sync intentionally reloads an overlap. If the local report
    is stale, the window expands back to the last known operation (bounded by
    the configured history depth), so a missed sync cannot leave a silent gap.
    """
    end = datetime.now(ZoneInfo("Europe/Moscow")).date()
    history_days = max(1, int(default_days))
    earliest = end - timedelta(days=history_days - 1)
    start = earliest
    latest = max_value("financial_report", "operation_date")
    if latest:
        try:
            parsed = datetime.fromisoformat(str(latest).replace("Z", "+00:00"))
            start = max(earliest, parsed.date() - timedelta(days=max(1, overlap_days)))
        except ValueError:
            pass
    return start, end


def sync_all() -> dict[str, int | str]:
    with sync_lock():
        return _sync_all_locked()


# Stage keys reported to the UI as "progress: N/7 <key>" while a full sync runs.
SYNC_STAGES = ("products", "orders", "sales", "finance", "stocks", "fifo", "ads")


def _progress(log_id: int, done: int) -> None:
    """Tell the cabinet which stage just finished. Never allowed to break the sync."""
    try:
        update_sync_progress(log_id, done, len(SYNC_STAGES), SYNC_STAGES[done - 1])
    except Exception:
        pass


def _sync_all_locked() -> dict[str, int | str]:
    init_db()
    ensure_financial_schema()
    settings = load_settings()
    token = get_token()
    if not token:
        raise RuntimeError("Сначала сохраните токен WB API в разделе «Настройки».")

    log_id = start_sync_log()
    api = WBAPI(token)
    result: dict[str, int | str] = {}
    try:
        # Demo rows must never coexist with real cabinet data. Previous versions
        # kept them in the same database, which inflated advertising spend and
        # added fake products to the cost table.
        clear_demo_data()

        history_days = int(settings.get("initial_history_days", 90))

        try:
            cards = api.get_product_cards()
            result["products"] = replace_product_catalog(cards) if cards else 0
        except Exception as exc:
            # Product catalogue is useful but must not block orders/finance sync.
            result["products_error"] = str(exc)[:180]
        _progress(log_id, 1)

        orders = api.get_orders(_incremental_from("orders", history_days))
        result["orders"] = upsert_orders(orders)
        _progress(log_id, 2)

        sales = api.get_sales(_incremental_from("sales", history_days))
        result["sales"] = upsert_sales(sales)
        _progress(log_id, 3)

        # Finance used to be refreshed only by a separate manual button. That
        # allowed the finance page to look current while actually ending weeks
        # earlier. Refresh it as part of the normal synchronization, but use an
        # incremental window with overlap so the regular 30-minute cycle stays
        # reasonably light. A Finance-scope/API failure must not block orders,
        # sales, stocks, FIFO or advertising updates.
        try:
            finance_start, finance_end = _financial_incremental_range(history_days)
            from db.financial_source import captured_identity
            financial_identity = captured_identity(api)
            finance_rows = api.get_financial_report(
                finance_start.isoformat(), finance_end.isoformat(), period="daily"
            )
            result["financial_rows"] = upsert_financial(finance_rows, source_identity=financial_identity)
            result["financial_from"] = finance_start.isoformat()
            result["financial_to"] = finance_end.isoformat()
        except Exception as exc:
            result["financial_error"] = str(exc)[:180]
        _progress(log_id, 4)

        stocks = api.get_stocks()
        result["stocks"] = insert_stocks(stocks)
        _progress(log_id, 5)

        # v4.6: establish the current database as a historical baseline on the
        # first run, then consume FIFO finished-goods layers only for new sales
        # and returns received by later synchronizations.
        try:
            fifo_status = sales_fifo_tracking_status()
            if not fifo_status.get("initialized"):
                fifo_result = initialize_sales_fifo_tracking()
                result["fifo_sales_baseline"] = int(fifo_result.get("baseline", 0))
            else:
                fifo_result = process_sales_fifo_events()
                result["fifo_sales"] = int(fifo_result.get("sales", 0))
                result["fifo_returns"] = int(fifo_result.get("returns", 0))
                result["fifo_errors"] = int(fifo_result.get("errors", 0))
        except Exception as exc:
            # COGS tracking must not block operational WB synchronization.
            result["fifo_error"] = str(exc)[:180]
        _progress(log_id, 6)

        _sync_ads(api, settings, result)
        _progress(log_id, 7)
        result["cleaned_cost_rows"] = cleanup_orphan_zero_costs()

        details = ", ".join(f"{k}: {v}" for k, v in result.items())
        status = "partial" if any(k.endswith("_error") or k.endswith("_skipped") for k in result) or result.get("fifo_errors", 0) else "success"
        finish_sync_log(log_id, status, details)
        if status == "success":
            _record_first_sync_event()
        return result
    except Exception as exc:
        details = ", ".join(f"{k}: {v}" for k, v in result.items())
        finish_sync_log(log_id, "error", f"{details}; error: {exc}" if details else str(exc))
        raise




def _sync_ads(api, settings, result):
    try:
        campaign_ids = api.get_campaign_ids()
        ad_days = max(1, min(int(settings.get("advert_history_days", 31)), 365))
        ad_end = datetime.now(ZoneInfo("Europe/Moscow")).date()
        ad_start = ad_end - timedelta(days=ad_days - 1)
        requested = {int(value) for value in campaign_ids}
        complete = set(requested)
        stats = []
        window_start = ad_start
        while requested and window_start <= ad_end:
            window_end = min(window_start + timedelta(days=30), ad_end)
            chunk = api.get_ad_stats(campaign_ids, window_start, window_end)
            returned = _validated_ad_campaigns(chunk, requested, window_start, window_end)
            complete.intersection_update(returned)
            stats.extend(chunk)
            window_start = window_end + timedelta(days=1)
        # Missing campaigns (including cancelled/deleted campaigns omitted by promotion/count)
        # retain history. A campaign explicitly returned with days=[] is a
        # complete zero result; an empty/malformed HTTP response is not.
        verified = [item for item in stats if int(item.get("advertId") or item.get("advert_id")) in complete]
        flattened = flatten_ad_stats(verified)
        result["ads"] = replace_ads_period(flattened, ad_start.isoformat(), ad_end.isoformat(), complete)
        if requested - complete:
            result["ads_skipped"] = "incomplete API response for campaigns -- kept their existing data"
        result["campaigns"] = len(campaign_ids)
    except Exception as exc:
        # Keep existing ad history and report the sections already saved.
        result["ads_error"] = str(exc)[:400]


def _validated_ad_campaigns(stats, requested: set[int], start: date, end: date) -> set[int]:
    """Reject malformed/out-of-range results before replacing any history."""
    if not isinstance(stats, list):
        raise ValueError("Invalid advertising response")
    returned = set()
    seen_days = set()
    for item in stats:
        if not isinstance(item, dict):
            raise ValueError("Invalid advertising campaign response")
        campaign = int(item.get("advertId") or item.get("advert_id") or 0)
        if campaign not in requested or campaign in returned or not isinstance(item.get("days"), list):
            raise ValueError("Unexpected or incomplete advertising campaign response")
        totals = _ad_metrics(item, require_spend=not item["days"])
        if not item["days"]:
            # An explicitly empty day list is only a verified zero when the
            # campaign-level spend confirms it. Missing/contradicting totals
            # can be another partial response and must not erase saved spend.
            if any(value != 0 for value in totals.values()):
                raise ValueError("Empty advertising days without verified zero spend")
        returned.add(campaign)
        day_spend = Decimal(0)
        for day in item["days"]:
            if not isinstance(day, dict):
                raise ValueError("Invalid advertising day response")
            day_date = date.fromisoformat(str(day.get("date") or day.get("day") or "")[:10])
            if not start <= day_date <= end or (campaign, day_date) in seen_days:
                raise ValueError("Advertising day outside requested period or duplicated")
            seen_days.add((campaign, day_date))
            daily = _ad_metrics(day, require_spend=True)
            day_spend += daily["sum"]
            apps = day.get("apps") or []
            if not isinstance(apps, list):
                raise ValueError("Invalid advertising app breakdown")
            app_spend = Decimal(0)
            products_spend = Decimal(0)
            product_count = 0
            for app in apps:
                if not isinstance(app, dict):
                    raise ValueError("Invalid advertising app breakdown")
                app_metrics = _ad_metrics(app)
                app_spend += app_metrics.get("sum", Decimal(0))
                products = app.get("nms") or app.get("nm") or []
                if not isinstance(products, list):
                    raise ValueError("Invalid advertising product breakdown")
                app_product_spend = Decimal(0)
                for product in products:
                    if not isinstance(product, dict):
                        raise ValueError("Invalid advertising product breakdown")
                    # Product rows are summed across device/app types by the
                    # flattener, not deduplicated by SKU. Validate each observed
                    # component once; never add app and SKU totals together.
                    app_product_spend += _ad_metrics(product, require_spend=True)["sum"]
                if "sum" in app_metrics:
                    _ad_subtotal(app_product_spend, app_metrics["sum"], len(products))
                products_spend += app_product_spend
                product_count += len(products)
            _ad_subtotal(products_spend, daily["sum"], product_count)
            _ad_subtotal(app_spend, daily["sum"], len(apps))
        if "sum" in totals:
            tolerance = Decimal(0) if totals["sum"] == 0 or day_spend == 0 else Decimal("0.01") * max(1, len(item["days"]))
            if abs(totals["sum"] - day_spend) > tolerance:
                raise ValueError("Campaign advertising spend disagrees with daily spend")
    return returned


def _ad_metrics(row, *, require_spend: bool = False) -> dict[str, Decimal]:
    """Missing optional counters are allowed; missing money is never zero."""
    if require_spend and "sum" not in row:
        raise ValueError("Advertising spend missing from response")
    metrics = {}
    for key in ("sum", "views", "clicks", "atbs", "orders", "canceled", "shks", "sum_price"):
        if key not in row:
            continue
        try:
            if isinstance(row[key], bool):
                raise ValueError("Invalid advertising metric")
            value = Decimal(str(row[key]))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValueError("Invalid advertising metric") from exc
        if not value.is_finite() or value < 0:
            raise ValueError("Invalid advertising metric")
        metrics[key] = value
    return metrics


def _ad_subtotal(parts: Decimal, total: Decimal, count: int) -> None:
    # Attribution can be incomplete: do not require equality or synthesize
    # missing SKU spend. Rounding may accumulate one kopeck per component, but
    # a confirmed zero total must never coexist with positive components.
    tolerance = Decimal(0) if total == 0 else Decimal("0.01") * max(1, count)
    if parts - total > tolerance:
        raise ValueError("Advertising breakdown exceeds authoritative spend")


def sync_finances(days: int = 90) -> dict[str, int]:
    with sync_lock():
        return _sync_finances_locked(days)


def sync_ads_only() -> dict[str, int | str]:
    with sync_lock():
        token = get_token()
        if not token:
            raise RuntimeError("Сначала подключите WB API")
        init_db()
        log_id = start_sync_log()
        result = {"scope": "ads"}
        _sync_ads(WBAPI(token), load_settings(), result)
        status = "partial" if "ads_error" in result or "ads_skipped" in result else "success"
        finish_sync_log(log_id, status, ", ".join(f"{key}: {value}" for key, value in result.items()))
        return result


def _sync_finances_locked(days: int) -> dict[str, int]:
    init_db()
    ensure_financial_schema()
    token = get_token()
    if not token:
        raise RuntimeError("Сначала сохраните токен WB API.")
    api = WBAPI(token)
    end = datetime.now(ZoneInfo("Europe/Moscow")).date()
    start = end - timedelta(days=max(1, days) - 1)
    from db.financial_source import captured_identity
    financial_identity = captured_identity(api)
    rows = api.get_financial_report(start.isoformat(), end.isoformat(), period="daily")
    return {"financial_rows": upsert_financial(rows, source_identity=financial_identity)}


def loop() -> None:
    while True:
        settings = load_settings()
        interval = max(15, int(settings.get("sync_interval_minutes", 30)))
        try:
            result = sync_all()
            print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] Синхронизация: {result}", flush=True)
        except Exception as exc:
            print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] Ошибка: {exc}", flush=True)
        time.sleep(interval * 60)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--loop", action="store_true")
    args = parser.parse_args()
    if args.loop:
        loop()
    else:
        print(sync_all())


if __name__ == "__main__":
    main()
