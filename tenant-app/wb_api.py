from __future__ import annotations

import time
from datetime import date, timedelta
from typing import Any, Iterable

import requests


class WBAPIError(RuntimeError):
    pass


class WBAPI:
    STATS_URL = "https://statistics-api.wildberries.ru"
    ANALYTICS_URL = "https://seller-analytics-api.wildberries.ru"
    ADVERT_URL = "https://advert-api.wildberries.ru"
    FINANCE_URL = "https://finance-api.wildberries.ru"
    CONTENT_URL = "https://content-api.wildberries.ru"
    SUPPLIES_URL = "https://supplies-api.wildberries.ru"

    def __init__(self, token: str, timeout: int = 60):
        self.token = token.strip()
        self.timeout = timeout
        self.session = requests.Session()

    def _headers(self, bearer: bool = False) -> dict[str, str]:
        token = self.token
        if bearer and not token.lower().startswith("bearer "):
            token = f"Bearer {token}"
        return {
            "Authorization": token,
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "WB-Dashboard-Local/1.0",
        }

    def _request(self, method: str, url: str, **kwargs) -> Any:
        last_error: Exception | None = None
        for attempt in range(5):
            for bearer in (False, True):
                try:
                    response = self.session.request(
                        method,
                        url,
                        headers=self._headers(bearer=bearer),
                        timeout=self.timeout,
                        **kwargs,
                    )
                except requests.RequestException as exc:
                    last_error = exc
                    continue
                if response.status_code == 401 and not bearer:
                    continue
                if response.status_code == 429:
                    retry_after = float(response.headers.get("Retry-After", 20))
                    time.sleep(min(max(retry_after, 2), 60))
                    break
                if response.status_code >= 400:
                    body = response.text[:800]
                    raise WBAPIError(f"WB API {response.status_code}: {body}")
                if not response.content:
                    return None
                try:
                    return response.json()
                except ValueError as exc:
                    raise WBAPIError("WB API вернул ответ не в JSON") from exc
            else:
                continue
            time.sleep(min(2 ** attempt, 20))
        if last_error:
            raise WBAPIError(f"Не удалось соединиться с WB API: {last_error}")
        raise WBAPIError("WB API временно недоступен")

    def test_statistics(self) -> bool:
        today = date.today().isoformat()
        result = self._request(
            "GET",
            f"{self.STATS_URL}/api/v1/supplier/orders",
            params={"dateFrom": today, "flag": 0},
        )
        return isinstance(result, list)

    def get_orders(self, date_from: str) -> list[dict[str, Any]]:
        result = self._request(
            "GET",
            f"{self.STATS_URL}/api/v1/supplier/orders",
            params={"dateFrom": date_from, "flag": 0},
        )
        return result if isinstance(result, list) else []

    def get_sales(self, date_from: str) -> list[dict[str, Any]]:
        result = self._request(
            "GET",
            f"{self.STATS_URL}/api/v1/supplier/sales",
            params={"dateFrom": date_from, "flag": 0},
        )
        return result if isinstance(result, list) else []

    def get_stocks(self, nm_ids: list[int] | None = None) -> list[dict[str, Any]]:
        all_rows: list[dict[str, Any]] = []
        offset = 0
        limit = 250_000
        while True:
            body: dict[str, Any] = {
                "nmIds": (nm_ids or [])[:1000],
                "chrtIds": [],
                "limit": limit,
                "offset": offset,
            }
            result = self._request(
                "POST",
                f"{self.ANALYTICS_URL}/api/analytics/v1/stocks-report/wb-warehouses",
                json=body,
            )
            if isinstance(result, list):
                rows = result
            elif isinstance(result, dict):
                rows = (
                    result.get("data")
                    or result.get("stocks")
                    or result.get("items")
                    or result.get("result")
                    or []
                )
                if isinstance(rows, dict):
                    rows = rows.get("items") or rows.get("stocks") or []
            else:
                rows = []
            rows = rows if isinstance(rows, list) else []
            all_rows.extend(r for r in rows if isinstance(r, dict))
            if len(rows) < limit:
                break
            offset += limit
            time.sleep(20)
        return all_rows


    def get_fbw_supply_details(self, supply_id: int) -> dict[str, Any]:
        """Return FBW supply details by supply ID."""
        result = self._request(
            "GET",
            f"{self.SUPPLIES_URL}/api/v1/supplies/{int(supply_id)}",
        )
        if isinstance(result, dict):
            payload = result.get("data") if isinstance(result.get("data"), dict) else result
            return payload if isinstance(payload, dict) else {}
        return {}

    def get_fbw_supply_goods(self, supply_id: int) -> list[dict[str, Any]]:
        """Return the declared goods composition of an FBW supply."""
        result = self._request(
            "GET",
            f"{self.SUPPLIES_URL}/api/v1/supplies/{int(supply_id)}/goods",
        )
        if isinstance(result, list):
            return [row for row in result if isinstance(row, dict)]
        if isinstance(result, dict):
            rows = result.get("data") or result.get("goods") or result.get("items") or result.get("result") or []
            if isinstance(rows, dict):
                rows = rows.get("goods") or rows.get("items") or []
            return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
        return []

    def get_product_cards(self) -> list[dict[str, Any]]:
        """Return all active product cards using cursor pagination.

        The method is available for tokens with the Promotion category, so the
        dashboard can build an authoritative product list without a separate
        content token. Cards in the trash are intentionally excluded.
        """
        cards: list[dict[str, Any]] = []
        limit = 100
        cursor: dict[str, Any] = {"limit": limit}
        while True:
            body = {
                "settings": {
                    "sort": {"ascending": True},
                    "cursor": cursor,
                    "filter": {"withPhoto": -1},
                }
            }
            result = self._request(
                "POST",
                f"{self.CONTENT_URL}/content/v2/get/cards/list",
                json=body,
            )
            if not isinstance(result, dict):
                break
            chunk = result.get("cards") or []
            chunk = [row for row in chunk if isinstance(row, dict)]
            cards.extend(chunk)
            response_cursor = result.get("cursor") or {}
            total = int(response_cursor.get("total") or len(chunk) or 0)
            if total < limit or not chunk:
                break
            updated_at = response_cursor.get("updatedAt")
            nm_id = response_cursor.get("nmID") or response_cursor.get("nmId")
            if not updated_at or not nm_id:
                break
            cursor = {"limit": limit, "updatedAt": updated_at, "nmID": int(nm_id)}
            time.sleep(6)
        return cards

    def get_campaign_ids(self) -> list[int]:
        result = self._request(
            "GET", f"{self.ADVERT_URL}/adv/v1/promotion/count"
        )
        ids: set[int] = set()

        def walk(value: Any, allowed: bool = True) -> None:
            if isinstance(value, dict):
                local_allowed = allowed
                if "status" in value:
                    try:
                        local_allowed = int(value["status"]) in {7, 9, 11}
                    except (TypeError, ValueError):
                        local_allowed = allowed
                for key, item in value.items():
                    if key in {"advertId", "advert_id"} and local_allowed:
                        try:
                            ids.add(int(item))
                        except (TypeError, ValueError):
                            pass
                    else:
                        walk(item, local_allowed)
            elif isinstance(value, list):
                for item in value:
                    walk(item, allowed)

        walk(result)
        return sorted(ids)

    @staticmethod
    def _chunks(values: list[int], size: int) -> Iterable[list[int]]:
        for i in range(0, len(values), size):
            yield values[i : i + size]

    def get_ad_stats(
        self, campaign_ids: list[int], begin_date: date, end_date: date
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        if not campaign_ids:
            return results
        period_start = begin_date
        while period_start <= end_date:
            period_end = min(period_start + timedelta(days=30), end_date)
            for chunk in self._chunks(campaign_ids, 50):
                result = self._request(
                    "GET",
                    f"{self.ADVERT_URL}/adv/v3/fullstats",
                    params={
                        "ids": ",".join(str(v) for v in chunk),
                        "beginDate": period_start.isoformat(),
                        "endDate": period_end.isoformat(),
                    },
                )
                if isinstance(result, list):
                    results.extend(r for r in result if isinstance(r, dict))
                time.sleep(20)
            period_start = period_end + timedelta(days=1)
        return results


    def get_financial_report(self, date_from: str, date_to: str, period: str = "daily") -> list[dict[str, Any]]:
        """Load realization report rows through the current Finance API.

        The response is paginated by rrdId. A 204 response means that all rows
        have been received. Date boundaries are interpreted in Moscow time.
        """
        rows: list[dict[str, Any]] = []
        rrdid = 0
        limit = 100000
        while True:
            body = {
                "dateFrom": date_from,
                "dateTo": date_to,
                "limit": limit,
                "rrdId": rrdid,
                "period": period,
            }
            result = self._request(
                "POST",
                f"{self.FINANCE_URL}/api/finance/v1/sales-reports/detailed",
                json=body,
            )
            if not result:
                break
            if isinstance(result, list):
                chunk = [r for r in result if isinstance(r, dict)]
            elif isinstance(result, dict):
                candidate = result.get("data") or result.get("items") or result.get("rows") or result.get("result") or []
                if isinstance(candidate, dict):
                    candidate = candidate.get("items") or candidate.get("rows") or candidate.get("data") or []
                chunk = [r for r in candidate if isinstance(r, dict)] if isinstance(candidate, list) else []
            else:
                chunk = []
            if not chunk:
                break
            rows.extend(chunk)
            next_id = int(chunk[-1].get("rrdId") or chunk[-1].get("rrd_id") or 0)
            if len(chunk) < limit or not next_id or next_id == rrdid:
                break
            rrdid = next_id
            time.sleep(61)
        return rows

def flatten_ad_stats(campaigns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Flatten campaign statistics without double counting.

    A campaign/day total is stored with nm_id=0 and is the source of truth for
    total advertising KPIs. Product rows are aggregated across all app types
    (the current API field is ``nms``; older responses used ``nm``).
    """
    rows: list[dict[str, Any]] = []
    metric_keys = (
        "views", "clicks", "sum", "atbs", "orders", "canceled", "shks", "sum_price"
    )
    for campaign in campaigns:
        campaign_id = campaign.get("advertId") or campaign.get("advert_id") or 0
        for day_item in campaign.get("days") or []:
            day = str(day_item.get("date") or day_item.get("day") or "")[:10]
            if not day:
                continue

            product_totals: dict[int, dict[str, Any]] = {}
            for app in day_item.get("apps") or []:
                product_items = app.get("nms") or app.get("nm") or []
                for nm in product_items:
                    if not isinstance(nm, dict):
                        continue
                    nm_id = int(nm.get("nmId") or nm.get("nm") or 0)
                    if not nm_id:
                        continue
                    acc = product_totals.setdefault(
                        nm_id,
                        {
                            "name": str(nm.get("name") or ""),
                            **{key: 0.0 for key in metric_keys},
                        },
                    )
                    if not acc.get("name") and nm.get("name"):
                        acc["name"] = str(nm.get("name"))
                    for key in metric_keys:
                        try:
                            acc[key] += float(nm.get(key) or 0)
                        except (TypeError, ValueError):
                            pass

            # The day-level row is authoritative for totals and prevents product
            # attribution differences from changing the overall advertising spend.
            rows.append(
                {
                    "campaign_id": campaign_id,
                    "day": day,
                    "nm_id": 0,
                    "product_name": "",
                    "views": day_item.get("views", 0),
                    "clicks": day_item.get("clicks", 0),
                    "spend": day_item.get("sum", 0),
                    "atbs": day_item.get("atbs", 0),
                    "orders_count": day_item.get("orders", 0),
                    "canceled": day_item.get("canceled", 0),
                    "shks": day_item.get("shks", 0),
                    "ad_revenue": day_item.get("sum_price", 0),
                    "raw": {"level": "campaign_day", "data": day_item},
                }
            )

            for nm_id, acc in product_totals.items():
                rows.append(
                    {
                        "campaign_id": campaign_id,
                        "day": day,
                        "nm_id": nm_id,
                        "product_name": acc.get("name", ""),
                        "views": acc.get("views", 0),
                        "clicks": acc.get("clicks", 0),
                        "spend": acc.get("sum", 0),
                        "atbs": acc.get("atbs", 0),
                        "orders_count": acc.get("orders", 0),
                        "canceled": acc.get("canceled", 0),
                        "shks": acc.get("shks", 0),
                        "ad_revenue": acc.get("sum_price", 0),
                        "raw": {"level": "product_day", "data": acc},
                    }
                )
    return rows
