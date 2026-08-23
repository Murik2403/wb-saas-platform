"""WB API client for the price/ad agents.

Read paths reuse the same request pattern as tenant-app/wb_api.py
(bearer/non-bearer fallback, 429 backoff). Duplicated rather than
imported because agents/ must also run standalone, outside the
wb-saas-platform-clean checkout.

Write endpoints (set_price / campaign budget / pause) are stubbed with
NotImplementedError until a token with pricing+advert scopes exists to
verify the real request shape against WB's API.
"""
from __future__ import annotations

import time
from datetime import date
from typing import Any

import requests

from .base import ApplyResult, Campaign, PriceRow


class WBAgentClientError(RuntimeError):
    pass


class WBAgentClient:
    marketplace = "wb"

    ANALYTICS_URL = "https://seller-analytics-api.wildberries.ru"
    ADVERT_URL = "https://advert-api.wildberries.ru"
    DISCOUNTS_PRICES_URL = "https://discounts-prices-api.wildberries.ru"

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
            "User-Agent": "WB-Agents/1.0",
        }

    def _request(self, method: str, url: str, **kwargs) -> Any:
        last_error: Exception | None = None
        for attempt in range(5):
            for bearer in (False, True):
                try:
                    response = self.session.request(
                        method, url, headers=self._headers(bearer=bearer),
                        timeout=self.timeout, **kwargs,
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
                    raise WBAgentClientError(f"WB API {response.status_code}: {response.text[:800]}")
                if not response.content:
                    return None
                try:
                    return response.json()
                except ValueError as exc:
                    raise WBAgentClientError("WB API вернул ответ не в JSON") from exc
            else:
                continue
            time.sleep(min(2 ** attempt, 20))
        if last_error:
            raise WBAgentClientError(f"Не удалось соединиться с WB API: {last_error}")
        raise WBAgentClientError("WB API временно недоступен")

    def get_prices(self) -> list[PriceRow]:
        """GET /api/v2/list/goods/filter — paginated list of nmIDs with current price/discount."""
        rows: list[PriceRow] = []
        limit = 1000
        offset = 0
        while True:
            result = self._request(
                "GET",
                f"{self.DISCOUNTS_PRICES_URL}/api/v2/list/goods/filter",
                params={"limit": limit, "offset": offset},
            )
            goods = []
            if isinstance(result, dict):
                data = result.get("data") if isinstance(result.get("data"), dict) else result
                goods = data.get("listGoods") or data.get("goods") or []
            if not isinstance(goods, list) or not goods:
                break
            for item in goods:
                if not isinstance(item, dict):
                    continue
                nm_id = item.get("nmID") or item.get("nmId")
                sizes = item.get("sizes") or []
                price = None
                if sizes and isinstance(sizes, list) and isinstance(sizes[0], dict):
                    price = sizes[0].get("price") or sizes[0].get("discountedPrice")
                if nm_id is None or price is None:
                    continue
                rows.append(
                    PriceRow(
                        sku=str(nm_id),
                        name=item.get("vendorCode") or str(nm_id),
                        current_price=float(price),
                    )
                )
            if len(goods) < limit:
                break
            offset += limit
            time.sleep(1)
        return rows

    def _current_discount(self, nm_id: str) -> int:
        """Fetch the live discount for one nmID so set_price never silently resets it to 0."""
        result = self._request(
            "GET",
            f"{self.DISCOUNTS_PRICES_URL}/api/v2/list/goods/filter",
            params={"limit": 1, "offset": 0, "filterNmID": nm_id},
        )
        if isinstance(result, dict):
            data = result.get("data") if isinstance(result.get("data"), dict) else result
            goods = data.get("listGoods") or data.get("goods") or []
            if goods and isinstance(goods[0], dict):
                return int(goods[0].get("discount") or 0)
        return 0

    def set_price(self, sku: str, price: float, *, dry_run: bool = True) -> ApplyResult:
        """POST /api/v2/upload/task — {"data": [{"nmID": ..., "price": ..., "discount": ...}]}.

        WB's price/discount update is a joint field — omitting discount can reset
        it, so the current discount is re-fetched right before the write.
        """
        endpoint = f"{self.DISCOUNTS_PRICES_URL}/api/v2/upload/task"
        if dry_run:
            return ApplyResult(ok=True, dry_run=True, detail=f"[dry-run] POST {endpoint} price={price} sku={sku}")
        discount = self._current_discount(sku)
        result = self._request(
            "POST", endpoint,
            json={"data": [{"nmID": int(sku), "price": int(price), "discount": discount}]},
        )
        # Success shape: {"data": {"id": ..., "alreadyExists": bool}, "error": false, "errorText": ""}
        ok = isinstance(result, dict) and result.get("error") is False
        return ApplyResult(ok=ok, dry_run=False, detail=f"POST {endpoint} price={price} discount={discount} sku={sku} -> {result}")

    def get_campaigns(self) -> list[Campaign]:
        """Verified live against a real WB token (2026-08-23):
        - GET /adv/v1/promotion/count -> ids grouped by status (9=active, 11=paused). Works.
        - GET /adv/v3/fullstats?ids=...&beginDate=&endDate= -> spend/revenue/ctr per id. Works.
        - GET /adv/v1/budget?id=X -> {"cash","netting","total","currency"} = remaining balance. Works.
        - A "get campaign name" endpoint (several candidates tried: /adv/v1/promotion/adverts
          GET/POST in various shapes, /adv/v0/adverts, /adv/v2/auto/campaign) 404'd every way —
          not resolved yet. Campaign name falls back to "Кампания {id}" until found.
        """
        count_result = self._request("GET", f"{self.ADVERT_URL}/adv/v1/promotion/count")
        status_by_id: dict[int, int] = {}

        def walk(value: Any, status: int | None = None) -> None:
            if isinstance(value, dict):
                local_status = status
                if "status" in value:
                    try:
                        local_status = int(value["status"])
                    except (TypeError, ValueError):
                        local_status = status
                for key, item in value.items():
                    if key in {"advertId", "advert_id"} and local_status in {9, 11}:
                        try:
                            status_by_id[int(item)] = local_status
                        except (TypeError, ValueError):
                            pass
                    else:
                        walk(item, local_status)
            elif isinstance(value, list):
                for item in value:
                    walk(item, status)

        walk(count_result)
        if not status_by_id:
            return []
        ids = sorted(status_by_id)

        end = date.today()
        stats = self._request(
            "GET", f"{self.ADVERT_URL}/adv/v3/fullstats",
            params={"ids": ",".join(str(v) for v in ids), "beginDate": end.isoformat(), "endDate": end.isoformat()},
        )
        stats_by_id: dict[int, dict] = {}
        if isinstance(stats, list):
            for item in stats:
                if isinstance(item, dict) and item.get("advertId") is not None:
                    stats_by_id[int(item["advertId"])] = item

        campaigns: list[Campaign] = []
        for cid in ids:
            stat = stats_by_id.get(cid, {})
            balance = self._request("GET", f"{self.ADVERT_URL}/adv/v1/budget", params={"id": cid})
            daily_budget = float(balance["total"]) if isinstance(balance, dict) and balance.get("total") is not None else None
            campaigns.append(
                Campaign(
                    campaign_id=str(cid),
                    name=f"Кампания {cid}",
                    status="active" if status_by_id[cid] == 9 else "paused",
                    daily_budget=daily_budget,
                    spend_7d=float(stat.get("sum") or 0),
                    revenue_7d=float(stat.get("sum_price") or 0),
                    ctr=float(stat.get("ctr")) if stat.get("ctr") is not None else None,
                )
            )
            time.sleep(0.5)
        return campaigns

    def set_campaign_budget(self, campaign_id: str, budget: float, *, dry_run: bool = True) -> ApplyResult:
        """POST /adv/v1/budget/deposit — {"id": ..., "sum": ...}.

        WB only supports topping the balance up, never withdrawing from it — there
        is no API to reduce a campaign's balance. If the proposed target is below
        the current live balance, refuse loudly instead of silently depositing the
        wrong (positive) amount.
        """
        endpoint = f"{self.ADVERT_URL}/adv/v1/budget/deposit"
        if dry_run:
            return ApplyResult(ok=True, dry_run=True, detail=f"[dry-run] POST {endpoint} campaign={campaign_id} target_balance={budget}")
        current = self._request("GET", f"{self.ADVERT_URL}/adv/v1/budget", params={"id": int(campaign_id)})
        current_total = float(current.get("total", 0)) if isinstance(current, dict) else 0.0
        if budget <= current_total:
            raise WBAgentClientError(
                f"WB API не поддерживает уменьшение бюджета кампании (только пополнение через {endpoint}); "
                f"текущий баланс {current_total}, предложено {budget}. Для снижения расходов используйте pause_campaign."
            )
        top_up = round(budget - current_total, 2)
        result = self._request("POST", endpoint, json={"id": int(campaign_id), "sum": int(top_up)})
        return ApplyResult(
            ok=True, dry_run=False,
            detail=f"POST {endpoint} campaign={campaign_id} top_up={top_up} (баланс {current_total}->{budget}) -> {result}",
        )

    def pause_campaign(self, campaign_id: str, *, dry_run: bool = True) -> ApplyResult:
        # GET /adv/v0/pause?id={campaign_id}
        endpoint = f"{self.ADVERT_URL}/adv/v0/pause"
        if dry_run:
            return ApplyResult(ok=True, dry_run=True, detail=f"[dry-run] GET {endpoint}?id={campaign_id}")
        result = self._request("GET", endpoint, params={"id": int(campaign_id)})
        return ApplyResult(ok=True, dry_run=False, detail=f"GET {endpoint}?id={campaign_id} -> {result}")

    def resume_campaign(self, campaign_id: str, *, dry_run: bool = True) -> ApplyResult:
        # GET /adv/v0/start?id={campaign_id}
        endpoint = f"{self.ADVERT_URL}/adv/v0/start"
        if dry_run:
            return ApplyResult(ok=True, dry_run=True, detail=f"[dry-run] GET {endpoint}?id={campaign_id}")
        result = self._request("GET", endpoint, params={"id": int(campaign_id)})
        return ApplyResult(ok=True, dry_run=False, detail=f"GET {endpoint}?id={campaign_id} -> {result}")
