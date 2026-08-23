"""Ozon Seller API + Performance API client for the price/ad agents.

Two separate credential systems, per Ozon's own architecture:
- Seller API (product/pricing) — Client-Id + Api-Key headers, from the seller cabinet.
- Performance API (advertising) — OAuth2 client_credentials against
  api-performance.ozon.ru/api/client/token. As of 2026-04-06 Ozon requires OAuth
  here even for RU sellers; the credentials for this token exchange are commonly
  the same Client-Id/Api-Key from the seller cabinet, but Ozon also lets sellers
  mint separate Performance API credentials in the advertising cabinet — if the
  token exchange fails with the seller Client-Id/Api-Key, that's likely why.

No Ozon integration existed anywhere in this repo before this file.
"""
from __future__ import annotations

import time
from typing import Any

import requests

from .base import ApplyResult, Campaign, PriceRow


class OzonAgentClientError(RuntimeError):
    pass


class OzonAgentClient:
    marketplace = "ozon"

    BASE_URL = "https://api-seller.ozon.ru"
    PERFORMANCE_URL = "https://api-performance.ozon.ru"

    def __init__(self, client_id: str, api_key: str, timeout: int = 60):
        self.client_id = client_id.strip()
        self.api_key = api_key.strip()
        self.timeout = timeout
        self.session = requests.Session()
        self._perf_token: str | None = None
        self._perf_token_expires_at: float = 0.0

    def _headers(self) -> dict[str, str]:
        return {
            "Client-Id": self.client_id,
            "Api-Key": self.api_key,
            "Content-Type": "application/json",
            "User-Agent": "Ozon-Agents/1.0",
        }

    def _request(self, method: str, url: str, *, headers: dict | None = None, **kwargs) -> Any:
        last_error: Exception | None = None
        for attempt in range(5):
            try:
                response = self.session.request(
                    method, url, headers=headers or self._headers(), timeout=self.timeout, **kwargs,
                )
            except requests.RequestException as exc:
                last_error = exc
                time.sleep(min(2 ** attempt, 20))
                continue
            if response.status_code == 429:
                retry_after = float(response.headers.get("Retry-After", 20))
                time.sleep(min(max(retry_after, 2), 60))
                continue
            if response.status_code >= 400:
                raise OzonAgentClientError(f"Ozon API {response.status_code}: {response.text[:800]}")
            if not response.content:
                return None
            try:
                return response.json()
            except ValueError as exc:
                raise OzonAgentClientError("Ozon API вернул ответ не в JSON") from exc
        if last_error:
            raise OzonAgentClientError(f"Не удалось соединиться с Ozon API: {last_error}")
        raise OzonAgentClientError("Ozon API временно недоступен")

    def _performance_token(self) -> str:
        """POST /api/client/token — OAuth2 client_credentials exchange."""
        if self._perf_token and time.time() < self._perf_token_expires_at:
            return self._perf_token
        result = self._request(
            "POST", f"{self.PERFORMANCE_URL}/api/client/token",
            headers={"Content-Type": "application/json"},
            json={"client_id": self.client_id, "client_secret": self.api_key, "grant_type": "client_credentials"},
        )
        if not isinstance(result, dict) or "access_token" not in result:
            raise OzonAgentClientError(
                f"Ozon Performance API: не удалось получить OAuth-токен по Client-Id/Api-Key ({result}). "
                "Возможно, для рекламного API нужны отдельные учётные данные из рекламного кабинета Ozon, "
                "а не из Seller API."
            )
        self._perf_token = result["access_token"]
        self._perf_token_expires_at = time.time() + float(result.get("expires_in", 1800)) - 60
        return self._perf_token

    def _performance_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._performance_token()}", "Content-Type": "application/json"}

    def get_prices(self) -> list[PriceRow]:
        """POST /v5/product/info/prices — cursor-paginated (last_id)."""
        rows: list[PriceRow] = []
        last_id = ""
        while True:
            body = {"cursor": last_id, "limit": 1000, "filter": {}}
            result = self._request("POST", f"{self.BASE_URL}/v5/product/info/prices", json=body)
            items = result.get("items") if isinstance(result, dict) else None
            if not items:
                break
            for item in items:
                if not isinstance(item, dict):
                    continue
                price_info = item.get("price") or {}
                offer_id = item.get("offer_id") or item.get("product_id")
                price = price_info.get("price")
                if offer_id is None or price is None:
                    continue
                rows.append(PriceRow(sku=str(offer_id), name=str(offer_id), current_price=float(price)))
            new_cursor = result.get("cursor")
            if not new_cursor or new_cursor == last_id or len(items) < 1000:
                break
            last_id = new_cursor
            time.sleep(1)
        return rows

    def set_price(self, sku: str, price: float, *, dry_run: bool = True) -> ApplyResult:
        """POST /v1/product/import/prices — {"prices": [{"offer_id": ..., "price": "..."}]}."""
        endpoint = f"{self.BASE_URL}/v1/product/import/prices"
        if dry_run:
            return ApplyResult(ok=True, dry_run=True, detail=f"[dry-run] POST {endpoint} price={price} sku={sku}")
        result = self._request(
            "POST", endpoint, json={"prices": [{"offer_id": sku, "price": str(int(price))}]}
        )
        errors = result.get("result") if isinstance(result, dict) else None
        ok = isinstance(errors, list) and bool(errors) and not (errors[0].get("errors") if isinstance(errors[0], dict) else True)
        return ApplyResult(ok=ok, dry_run=False, detail=f"POST {endpoint} price={price} sku={sku} -> {result}")

    def get_campaigns(self) -> list[Campaign]:
        """GET /api/client/campaign (Performance API, needs OAuth token)."""
        headers = self._performance_headers()
        result = self._request("GET", f"{self.PERFORMANCE_URL}/api/client/campaign", headers=headers)
        items = (result.get("list") if isinstance(result, dict) else None) or []
        campaigns: list[Campaign] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            state = str(item.get("state", "")).upper()
            campaigns.append(
                Campaign(
                    campaign_id=str(item.get("id")),
                    name=item.get("title") or f"Кампания {item.get('id')}",
                    status="active" if "RUNNING" in state else "paused",
                    daily_budget=float(item["dailyBudget"]) / 1_000_000 if item.get("dailyBudget") else None,
                    spend_7d=0.0,  # требует отдельного вызова /api/client/statistics (асинхронный отчёт)
                    revenue_7d=0.0,
                )
            )
        return campaigns

    def set_campaign_budget(self, campaign_id: str, budget: float, *, dry_run: bool = True) -> ApplyResult:
        endpoint = f"{self.PERFORMANCE_URL}/api/client/campaign/{campaign_id}/budget"
        if dry_run:
            return ApplyResult(ok=True, dry_run=True, detail=f"[dry-run] POST {endpoint} budget={budget}")
        result = self._request("POST", endpoint, headers=self._performance_headers(), json={"dailyBudget": int(budget * 1_000_000)})
        return ApplyResult(ok=True, dry_run=False, detail=f"POST {endpoint} budget={budget} -> {result}")

    def pause_campaign(self, campaign_id: str, *, dry_run: bool = True) -> ApplyResult:
        endpoint = f"{self.PERFORMANCE_URL}/api/client/campaign/{campaign_id}/stop"
        if dry_run:
            return ApplyResult(ok=True, dry_run=True, detail=f"[dry-run] POST {endpoint}")
        result = self._request("POST", endpoint, headers=self._performance_headers())
        return ApplyResult(ok=True, dry_run=False, detail=f"POST {endpoint} -> {result}")

    def resume_campaign(self, campaign_id: str, *, dry_run: bool = True) -> ApplyResult:
        endpoint = f"{self.PERFORMANCE_URL}/api/client/campaign/{campaign_id}/start"
        if dry_run:
            return ApplyResult(ok=True, dry_run=True, detail=f"[dry-run] POST {endpoint}")
        result = self._request("POST", endpoint, headers=self._performance_headers())
        return ApplyResult(ok=True, dry_run=False, detail=f"POST {endpoint} -> {result}")
