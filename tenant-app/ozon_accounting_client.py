"""Bounded Seller API reads. Never invokes a marketplace mutation endpoint."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any
import requests
import time
import json
import hashlib


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
        self.byte_budget = 16*1024*1024
        self.page_receipts = []
        self.deadline = time.monotonic()+300

    def _post(self, path: str, body: dict) -> dict:
        self.requests += 1
        if self.requests > 250 or time.monotonic() > self.deadline:
            raise OzonAccountingError("Достигнут предел загрузки Ozon. Сократите период.")
        response = None
        try:
            response = self.session.post(self.BASE+path, headers=self.headers, json=body, timeout=20, allow_redirects=False, stream=True)
            if response.status_code != 200:
                raise OzonAccountingError(f"Ozon: HTTP {response.status_code} при чтении {path}. Данные не заменены.")
            raw = bytearray()
            for chunk in response.iter_content(chunk_size=65536):
                if time.monotonic()>self.deadline or len(raw)+len(chunk)>2*1024*1024 or len(chunk)>self.byte_budget:
                    raise OzonAccountingError("Превышен безопасный размер ответа Ozon. Данные не заменены.")
                raw.extend(chunk);self.byte_budget-=len(chunk)
            if time.monotonic()>self.deadline:
                raise OzonAccountingError("Истекло время загрузки Ozon. Данные не заменены.")
            result = json.loads(raw.decode('utf-8'),parse_float=str,parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            if not isinstance(result, dict):raise OzonAccountingError("Неизвестная структура ответа Ozon.")
            self.page_receipts.append({'endpoint':path,'request_fingerprint':hashlib.sha256(json.dumps(body,sort_keys=True,separators=(',',':')).encode()).hexdigest(),'response_fingerprint':hashlib.sha256(raw).hexdigest(),'response_bytes':len(raw)})
            return result
        except requests.RequestException:
            raise OzonAccountingError("Ozon не ответил. Данные не заменены; повторите загрузку.") from None
        except (ValueError,UnicodeError,RecursionError) as exc:
            if isinstance(exc,OzonAccountingError):raise
            raise OzonAccountingError("Ozon вернул неподтверждённый JSON. Данные не заменены.") from None
        finally:
            if response is not None:response.close()

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
            if numeric:
                if isinstance(next_cursor,bool) or not isinstance(next_cursor,int) or next_cursor<1:
                    raise OzonAccountingError("Некорректный числовой курсор Ozon.")
            elif not isinstance(next_cursor,str) or len(next_cursor)>4096:
                raise OzonAccountingError("Некорректный курсор Ozon.")
            if not has_next and next_cursor == "":
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
        completed_days = []
        day = first
        while day <= last:
            accruals.extend(self._pages("/v1/finance/accrual/by-day", {"date": day.isoformat()}, rows="accruals", cursor="last_id"))
            if len(accruals) > self.max_rows:
                raise OzonAccountingError("Превышен безопасный объём начислений Ozon.")
            completed_days.append(day.isoformat())
            day += timedelta(days=1)
        return {"start_date": start, "end_date": end, "products": products, "stocks": stocks, "fbo": fbo, "fbs": fbs, "returns": returns, "types": dictionary, "accruals": accruals,"collection_provenance":{"schema_version":1,"completed_days":completed_days,"start_date":start,"end_date":end,"terminal_pages_checked":True,"financial_normalization_verified":False,"page_receipts":self.page_receipts.copy()}}
