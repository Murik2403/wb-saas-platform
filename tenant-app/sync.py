from __future__ import annotations

import argparse
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from config import get_token, load_settings
from db import (
    finish_sync_log,
    init_db,
    insert_stocks,
    clear_demo_data,
    replace_product_catalog,
    delete_ads_period,
    cleanup_orphan_zero_costs,
    max_value,
    start_sync_log,
    upsert_ads,
    upsert_orders,
    upsert_sales,
    upsert_financial,
    ensure_financial_schema,
    sales_fifo_tracking_status,
    initialize_sales_fifo_tracking,
    process_sales_fifo_events,
)
from wb_api import WBAPI, flatten_ad_stats


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

        orders = api.get_orders(_incremental_from("orders", history_days))
        result["orders"] = upsert_orders(orders)

        sales = api.get_sales(_incremental_from("sales", history_days))
        result["sales"] = upsert_sales(sales)

        # Finance used to be refreshed only by a separate manual button. That
        # allowed the finance page to look current while actually ending weeks
        # earlier. Refresh it as part of the normal synchronization, but use an
        # incremental window with overlap so the regular 30-minute cycle stays
        # reasonably light. A Finance-scope/API failure must not block orders,
        # sales, stocks, FIFO or advertising updates.
        try:
            finance_start, finance_end = _financial_incremental_range(history_days)
            finance_rows = api.get_financial_report(
                finance_start.isoformat(), finance_end.isoformat(), period="daily"
            )
            result["financial_rows"] = upsert_financial(finance_rows)
            result["financial_from"] = finance_start.isoformat()
            result["financial_to"] = finance_end.isoformat()
        except Exception as exc:
            result["financial_error"] = str(exc)[:180]

        stocks = api.get_stocks()
        result["stocks"] = insert_stocks(stocks)

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

        campaign_ids = api.get_campaign_ids()
        ad_days = max(1, min(int(settings.get("advert_history_days", 31)), 365))
        ad_end = datetime.now(ZoneInfo("Europe/Moscow")).date()
        ad_start = ad_end - timedelta(days=ad_days - 1)
        stats = api.get_ad_stats(campaign_ids, ad_start, ad_end)
        # Replace the requested interval so rows produced by older parser
        # versions cannot remain and distort totals -- but ONLY when the API
        # actually returned data. A transient WB advert-API glitch that returns
        # an empty list (HTTP 200, no rows) while live campaigns exist would
        # otherwise wipe a month of real ad-spend history and replace it with
        # nothing. If there genuinely are campaigns but zero rows came back,
        # skip the destructive delete and leave the existing data untouched.
        flattened = flatten_ad_stats(stats)
        if flattened or not campaign_ids:
            delete_ads_period(ad_start.isoformat(), ad_end.isoformat())
            result["ads"] = upsert_ads(flattened)
        else:
            result["ads"] = 0
            result["ads_skipped"] = "empty API response with live campaigns -- kept existing data"
        result["campaigns"] = len(campaign_ids)
        result["cleaned_cost_rows"] = cleanup_orphan_zero_costs()

        details = ", ".join(f"{k}: {v}" for k, v in result.items())
        finish_sync_log(log_id, "success", details)
        return result
    except Exception as exc:
        finish_sync_log(log_id, "error", str(exc))
        raise



def sync_finances(days: int = 90) -> dict[str, int]:
    init_db()
    ensure_financial_schema()
    token = get_token()
    if not token:
        raise RuntimeError("Сначала сохраните токен WB API.")
    api = WBAPI(token)
    end = datetime.now(ZoneInfo("Europe/Moscow")).date()
    start = end - timedelta(days=max(1, days) - 1)
    rows = api.get_financial_report(start.isoformat(), end.isoformat(), period="daily")
    return {"financial_rows": upsert_financial(rows)}


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
