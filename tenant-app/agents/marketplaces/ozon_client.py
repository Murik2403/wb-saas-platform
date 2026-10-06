"""Ozon Seller price client. Advertising automation is deliberately unavailable.

Performance API uses separate credentials and asynchronous reports, exposed
by ozon_ads_analytics. Seller credentials must never be exchanged for a
Performance token, and unknown campaign statistics must never become zeros.
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
        raise NotImplementedError(self._advertising_guidance())

    def _performance_headers(self) -> dict[str, str]:
        raise NotImplementedError(self._advertising_guidance())

    @staticmethod
    def _advertising_guidance() -> str:
        return (
            "Реклама Ozon в агенте пока недоступна: нужен отдельный Performance API "
            "и подтверждённые отчёты. Аналитика доступна в блоке «Реклама Ozon»; "
            "изменение бюджетов и запуск/остановка кампаний не подключены."
        )

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
        raise NotImplementedError(self._advertising_guidance())

    def set_campaign_budget(self, campaign_id: str, budget: float, *, dry_run: bool = True) -> ApplyResult:
        raise NotImplementedError(self._advertising_guidance())

    def pause_campaign(self, campaign_id: str, *, dry_run: bool = True) -> ApplyResult:
        raise NotImplementedError(self._advertising_guidance())

    def resume_campaign(self, campaign_id: str, *, dry_run: bool = True) -> ApplyResult:
        raise NotImplementedError(self._advertising_guidance())
