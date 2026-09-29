"""Bounded Seller API reads. Never invokes a marketplace mutation endpoint."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any
import requests
import time


class OzonAccountingError(ValueError):
    pass


def checked_period(start: str, end: str) -> tuple[date, date]:
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    if first > last or (last-first).days > 30 or first < date(2022, 1, 1) or last > date.today():
        raise OzonAccountingError("Выберите прошедший период не длиннее 31 дня, начиная с 2022 года.")
    return first, last


class OzonAccountingClient:
    BASE = "https://api-seller.ozon.ru"
    def __init__(self, client_id: str, api_key: str, *, session=None, max_pages=100, max_rows=100_000):
        self.session = session or requests.Session()
        self.headers = {"Client-Id": str(client_id), "Api-Key": api_key}
        self.max_pages, self.max_rows = max_pages, max_rows
        self.requests = 0
        self.deadline = time.monotonic()+300

    def _post(self, path: str, body: dict) -> dict:
        self.requests += 1
        if self.requests > 250 or time.monotonic() > self.deadline:
            raise OzonAccountingError("Достигнут предел загрузки Ozon. Сократите период.")
        try:
            response = self.session.post(self.BASE+path, headers=self.headers, json=body, timeout=20)
        except requests.RequestException:
            raise OzonAccountingError("Ozon не ответил. Данные не заменены; повторите загрузку.") from None
        if response.status_code != 200:
            raise OzonAccountingError(f"Ozon: HTTP {response.status_code} при чтении {path}. Данные не заменены.")
        try:
            result = response.json()
        except ValueError:
            raise OzonAccountingError("Ozon вернул ответ не в JSON. Данные не заменены.") from None
        if not isinstance(result, dict):
            raise OzonAccountingError("Неизвестная структура ответа Ozon.")
        return result

    def _pages(self, path: str, body: dict, *, rows: str, cursor: str, nested=False, has_next=False, numeric=False) -> list[dict]:
        collected: list[dict] = []
        previous: Any = 0 if numeric else ""
        seen: set[str] = set()
        for _ in range(self.max_pages):
            result = self._post(path, {**body, cursor: previous})
            data = result.get("result") if nested else result
            if not isinstance(data, dict) or not isinstance(data.get(rows), list):
                raise OzonAccountingError(f"Неизвестная структура списка {path}.")
            page = data[rows]
            if any(not isinstance(row, dict) for row in page):
                raise OzonAccountingError("Ozon вернул некорректную строку.")
            collected.extend(page)
            if len(collected) > self.max_rows:
                raise OzonAccountingError("Превышен безопасный объём загрузки Ozon. Сократите период.")
            if has_next:
                if not isinstance(data.get("has_next"), bool):
                    raise OzonAccountingError("Ozon не сообщил полноту страницы.")
                if not data["has_next"]:
                    return collected
                next_cursor = page[-1].get("id") if numeric and page else data.get(cursor)
            else:
                next_cursor = data.get(cursor)
                if cursor not in data:
                    raise OzonAccountingError("Ozon не сообщил курсор страницы.")
                if next_cursor in ("", 0):
                    return collected
            if not page or next_cursor is None or str(next_cursor) == str(previous) or str(next_cursor) in seen:
                raise OzonAccountingError("Курсор Ozon не продвигается; загрузка неполная.")
            seen.add(str(next_cursor)); previous = next_cursor
        raise OzonAccountingError("Достигнут предел страниц Ozon. Сократите период.")

    def snapshot(self, start: str, end: str) -> dict:
        first, last = checked_period(start, end)
        products = self._pages("/v3/product/list", {"filter": {"visibility": "ALL"}, "limit": 1000}, rows="items", cursor="last_id", nested=True)
        stocks = self._pages("/v4/product/info/stocks", {"filter": {"visibility": "ALL"}, "limit": 1000}, rows="items", cursor="cursor")
        period_filter = {"since": first.isoformat()+"T00:00:00Z", "to": last.isoformat()+"T23:59:59Z"}
        fbo = self._pages("/v3/posting/fbo/list", {"filter": period_filter, "limit": 1000, "dir": "ASC"}, rows="postings", cursor="cursor", has_next=True)
        fbs = self._pages("/v4/posting/fbs/list", {"filter": period_filter, "limit": 1000, "dir": "ASC"}, rows="postings", cursor="cursor", has_next=True)
        # All returns are read because the current date-filter semantics are not verified.
        # The UI labels this collection as a current registry, not period return revenue.
        returns = self._pages("/v1/returns/list", {"filter": {}, "limit": 500}, rows="returns", cursor="last_id", has_next=True, numeric=True)
        dictionary = self._post("/v1/finance/accrual/types", {}).get("accrual_types")
        if not isinstance(dictionary, list) or any(not isinstance(row, dict) for row in dictionary):
            raise OzonAccountingError("Справочник начислений Ozon не загружен.")
        accruals = []
        day = first
        while day <= last:
            accruals.extend(self._pages("/v1/finance/accrual/by-day", {"date": day.isoformat()}, rows="accruals", cursor="last_id"))
            if len(accruals) > self.max_rows:
                raise OzonAccountingError("Превышен безопасный объём начислений Ozon.")
            day += timedelta(days=1)
        return {"start_date": start, "end_date": end, "products": products, "stocks": stocks, "fbo": fbo, "fbs": fbs, "returns": returns, "types": dictionary, "accruals": accruals}
