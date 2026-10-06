"""Bounded Seller API reads. Never invokes a marketplace mutation endpoint."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any
import requests
import time
import json
import hashlib
import re
from db.ozon_accounting import seller_fingerprint


class OzonAccountingError(ValueError):
    pass


def checked_realization_month(month: int, year: int):
    if type(month) is not int or type(year) is not int or not 1 <= month <= 12:
        raise OzonAccountingError("Выберите месяц отчёта Ozon.")
    if (year, month) < (2023, 8) or (year, month) > (date.today().year, date.today().month):
        raise OzonAccountingError("Отчёт доступен с августа 2023 года, без будущих месяцев.")
    return year, month


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

    def _post(self, path: str, body: dict, *, timeout=20) -> dict:
        self.requests += 1
        if self.requests > 250 or time.monotonic() > self.deadline:
            raise OzonAccountingError("Достигнут предел загрузки Ozon. Сократите период.")
        response = None
        try:
            response = self.session.post(self.BASE+path, headers=self.headers, json=body, timeout=timeout, allow_redirects=False, stream=True)
            if response.status_code != 200:
                error=OzonAccountingError(f"Ozon: HTTP {response.status_code} при чтении {path}. Данные не заменены.")
                error.status_code=response.status_code
                raise error
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
                # Seller returns IDs are decimal strings; request last_id is int64.
                if isinstance(next_cursor, str) and re.fullmatch(r'[1-9][0-9]{0,18}', next_cursor):
                    next_cursor = int(next_cursor)
                if isinstance(next_cursor,bool) or not isinstance(next_cursor,int) or not 1 <= next_cursor <= 2**63-1:
                    raise OzonAccountingError("Некорректный числовой курсор Ozon.")
            elif not isinstance(next_cursor,str) or len(next_cursor)>4096:
                raise OzonAccountingError("Некорректный курсор Ozon.")
            if not has_next and next_cursor == "":
                return collected
            if not page or next_cursor is None or str(next_cursor) == str(previous) or str(next_cursor) in seen:
                raise OzonAccountingError("Курсор Ozon не продвигается; загрузка неполная.")
            seen.add(str(next_cursor)); previous = next_cursor
        raise OzonAccountingError("Достигнут предел страниц Ozon. Сократите период.")

    def product_details(self, products: list[dict]) -> list[dict]:
        """Enrich only explicit product IDs; response SKU is never inferred."""
        identifiers=[]
        for product in products:
            value=product.get('product_id')
            if isinstance(value,bool) or not isinstance(value,(str,int)) or not re.fullmatch(r'[1-9][0-9]{0,19}',str(value)):
                raise OzonAccountingError('Каталог Ozon не содержит корректный product_id.')
            identifiers.append(str(value))
        identifiers=list(dict.fromkeys(identifiers)); details={}
        for offset in range(0,len(identifiers),1000):
            batch=identifiers[offset:offset+1000]
            rows=self._post('/v3/product/info/list',{'product_id':batch}).get('items')
            if not isinstance(rows,list) or len(rows)>1000:
                raise OzonAccountingError('Неизвестный ответ карточек Ozon.')
            for row in rows:
                if not isinstance(row,dict) or isinstance(row.get('id'),bool) or str(row.get('id')) not in batch:
                    raise OzonAccountingError('Карточка Ozon не соответствует запрошенному product_id.')
                key=str(row['id'])
                if key in details and details[key]!=row:
                    raise OzonAccountingError('Конфликтующие карточки Ozon.')
                details[key]=row
        return [{**product,**details.get(str(product['product_id']),{}),'product_id':product['product_id']} for product in products]

    def product_prices(self) -> list[dict]:
        """Current v5 read-only price source; values remain raw currency amounts."""
        return self._pages('/v5/product/info/prices',{'filter':{'visibility':'ALL'},'limit':1000},rows='items',cursor='cursor')

    def snapshot(self, start: str, end: str, *, include_prices: bool=False) -> dict:
        first, last = checked_period(start, end)
        products = self._pages("/v3/product/list", {"filter": {"visibility": "ALL"}, "limit": 1000}, rows="items", cursor="last_id", nested=True)
        products = self.product_details(products)
        prices = self.product_prices() if include_prices else None
        stocks = self._pages("/v4/product/info/stocks", {"filter": {"visibility": "ALL"}, "limit": 1000}, rows="items", cursor="cursor")
        period_filter = {"since": first.isoformat()+"T00:00:00Z", "to": last.isoformat()+"T23:59:59Z"}
        # Current PostingFboList/PostingFbsList use cursor, sort_dir and 1..100
        # postings per page; legacy limit=1000/dir belongs to retired versions.
        fbo = self._pages("/v3/posting/fbo/list", {"filter": period_filter, "limit": 100, "sort_dir": "ASC"}, rows="postings", cursor="cursor", has_next=True)
        fbs = self._pages("/v4/posting/fbs/list", {"filter": period_filter, "limit": 100, "sort_dir": "ASC"}, rows="postings", cursor="cursor", has_next=True)
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
        # Explicit posting numbers come from the separate order sources. Do not
        # infer a posting number from by-day unit_number, or join financial rows
        # by date/SKU. This endpoint returns its own history without a date filter.
        posting_numbers = [row["posting_number"] for row in fbo + fbs
                           if isinstance(row.get("posting_number"), str) and row["posting_number"]]
        finance_postings = self.finance_postings(posting_numbers)
        return {"seller_fingerprint":seller_fingerprint(self.headers["Client-Id"]), "start_date": start, "end_date": end, "products": products, "prices": prices, "source_endpoints": {"fbs":"/v4/posting/fbs/list","fbo":"/v3/posting/fbo/list","products":"/v3/product/info/list","prices":"/v5/product/info/prices" if include_prices else None}, "stocks": stocks, "fbo": fbo, "fbs": fbs, "returns": returns, "types": dictionary, "accruals": accruals,"finance_postings":finance_postings,"finance_postings_scope":{"requested_number_count":len(set(posting_numbers)),"date_filter_supported":False,"missing_number_count":len(set(posting_numbers)-{r["posting_number"] for r in finance_postings}),"normalization_verified":False},"collection_provenance":{"schema_version":1,"completed_days":completed_days,"start_date":start,"end_date":end,"terminal_pages_checked":True,"financial_normalization_verified":False,"page_receipts":self.page_receipts.copy()}}

    def realization_source(self, month: int, year: int) -> dict:
        checked_realization_month(month, year)
        self.deadline=min(self.deadline,time.monotonic()+5)
        try:
            report = self._post("/v1/finance/realization/posting", {"month":month,"year":year}, timeout=5)
        except OzonAccountingError as exc:
            if getattr(exc,'status_code',None) in (403,404):
                raise OzonAccountingError("Месячный документ Ozon недоступен для этого аккаунта или периода. Это не подтверждение нулевых продаж. Сохранённый документ не заменён.") from None
            raise
        if (not isinstance(report.get("header"), dict) or not isinstance(report.get("rows"), list)
                or any(not isinstance(row, dict) for row in report["rows"])):
            raise OzonAccountingError("Не распознана структура месячного отчёта Ozon.")
        if len(report["rows"]) > self.max_rows:
            raise OzonAccountingError("Превышен безопасный объём месячного отчёта Ozon.")
        return {"source_kind":"ozon-realization-v1", "month":month,"year":year,
                "report":report,"page_receipts":self.page_receipts.copy()}

    def finance_postings(self, posting_numbers: list[str]) -> list[dict]:
        """Official v1 raw financial rows, sequential requests of 1..200 numbers.

        Duplicate request references are collapsed, never response occurrences.
        Request/stream/time budgets apply; this does not certify ledger identity,
        financial category signs, order movements, or returned coverage.
        """
        if not isinstance(posting_numbers, list) or any(not isinstance(n, str) or not n or len(n) > 128 for n in posting_numbers):
            raise OzonAccountingError("Некорректный список номеров отправлений Ozon.")
        numbers = list(dict.fromkeys(posting_numbers))
        if len(numbers) > self.max_rows or (len(numbers) + 199) // 200 > self.max_pages:
            raise OzonAccountingError("Превышен безопасный объём финансовых отправлений Ozon. Сократите период.")
        collected = []
        child_count = 0
        for offset in range(0, len(numbers), 200):
            batch = numbers[offset:offset + 200]
            result = self._post("/v1/finance/accrual/postings", {"posting_numbers": batch})
            groups = result.get("posting_accruals")
            if not isinstance(groups, list):
                raise OzonAccountingError("Неизвестная структура финансовых отправлений Ozon.")
            for group in groups:
                if (not isinstance(group, dict) or group.get("posting_number") not in batch
                        or not isinstance(group.get("accruals"), list)
                        or any(not isinstance(row, dict) for row in group["accruals"])):
                    raise OzonAccountingError("Не распознана строка финансового отправления Ozon.")
                child_count += len(group["accruals"])
                if child_count > self.max_rows or len(collected) >= self.max_rows:
                    raise OzonAccountingError("Превышен безопасный объём финансовых строк Ozon. Сократите период.")
                collected.append(group)
        return collected
