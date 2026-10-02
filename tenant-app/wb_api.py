from __future__ import annotations

import base64
import json
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
    MARKETPLACE_URL = "https://marketplace-api.wildberries.ru"
    COMMON_URL = "https://common-api.wildberries.ru"
    PRICES_URL = "https://discounts-prices-api.wildberries.ru"
    FEEDBACKS_URL = "https://feedbacks-api.wildberries.ru"
    RETURNS_URL = "https://returns-api.wildberries.ru"
    CHAT_URL = "https://buyer-chat-api.wildberries.ru"
    TOKEN_SCOPES = {
        1: "Контент",
        2: "Аналитика",
        3: "Цены и скидки",
        4: "Маркетплейс",
        5: "Статистика",
        6: "Продвижение",
        7: "Отзывы/Вопросы",
        9: "Чат с покупателями",
        10: "Поставки",
        11: "Возвраты",
        12: "Документы",
        13: "Финансы",
    }
    READ_ONLY_BIT = 30
    RETENTION_MAX_PAGES = 1000

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
        retry_safe = kwargs.pop("retry_safe", True)
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
                    if not retry_safe:
                        raise WBAPIError("Ответ WB не получен. Результат отправки неизвестен; проверьте историю WB перед повтором.") from exc
                    last_error = exc
                    continue
                if response.status_code == 401 and not bearer:
                    continue
                if response.status_code == 429:
                    if not retry_safe:
                        raise WBAPIError("WB ограничил частоту запросов. Повторите позднее.")
                    retry_after = float(response.headers.get("Retry-After", 20))
                    time.sleep(min(max(retry_after, 2), 60))
                    break
                if response.status_code >= 500:
                    if not retry_safe:
                        raise WBAPIError("WB сообщил об ошибке сервера. Результат отправки неизвестен; проверьте историю WB перед повтором.")
                    # 502/503/504 happen regularly on WB's side. Treat them as
                    # transient like a 429: break out to the backoff below and
                    # retry the whole attempt, instead of failing the entire sync
                    # on the very first blip. Only 4xx (client errors -- bad
                    # token, bad request) fall through to raise immediately.
                    last_error = WBAPIError(f"WB API {response.status_code}: {response.text[:200]}")
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

    def token_scope_info(self) -> dict[str, Any]:
        """Decode WB's local JWT scope metadata without logging the token.

        WB documents the ``s`` claim as a bit mask. Decoding the payload locally
        avoids a burst of /ping calls (which are heavily rate limited) while
        still showing the user which categories the token advertises.
        """
        raw_token = self.token[7:].strip() if self.token.lower().startswith("bearer ") else self.token
        parts = raw_token.split(".")
        if len(parts) < 2:
            return {"known": False, "scopes": [], "missing_scopes": [], "read_only": False, "expires_at": None}
        try:
            padded = parts[1] + "=" * (-len(parts[1]) % 4)
            payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
            mask = int(payload.get("s"))
        except (ValueError, TypeError, KeyError, UnicodeError, json.JSONDecodeError, base64.binascii.Error):
            return {"known": False, "scopes": [], "missing_scopes": [], "read_only": False, "expires_at": None}
        scopes = [label for bit, label in self.TOKEN_SCOPES.items() if mask & (1 << bit)]
        all_scopes = list(self.TOKEN_SCOPES.values())
        expires_at = payload.get("exp")
        return {
            "known": True,
            "scopes": scopes,
            "missing_scopes": [label for label in all_scopes if label not in scopes],
            "read_only": bool(mask & (1 << self.READ_ONLY_BIT)),
            "expires_at": int(expires_at) if isinstance(expires_at, (int, float)) else None,
        }

    def get_balance(self) -> dict[str, Any]:
        """Return the seller balance widget from the WB finance API."""
        result = self._request("GET", f"{self.FINANCE_URL}/api/v1/account/balance")
        if isinstance(result, dict):
            return result.get("data") if isinstance(result.get("data"), dict) else result
        return {}

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

    def get_sales_funnel_products(
        self,
        start: str,
        end: str,
        nm_ids: list[int] | None = None,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """Return the WB sales-funnel product rows for a selected period.

        The v3 endpoint is deliberately read-only.  Keep pagination here so
        the adapter never silently presents only the first page of cards.
        """
        start_date = date.fromisoformat(str(start)[:10])
        end_date = date.fromisoformat(str(end)[:10])
        days = max(1, (end_date - start_date).days + 1)
        past_end = start_date - timedelta(days=1)
        past_start = past_end - timedelta(days=days - 1)
        page_limit = max(1, min(int(limit), 1000))
        rows: list[dict[str, Any]] = []
        offset = 0
        for _ in range(100):
            body = {
                "selectedPeriod": {"start": start_date.isoformat(), "end": end_date.isoformat()},
                "pastPeriod": {"start": past_start.isoformat(), "end": past_end.isoformat()},
                "nmIds": [int(value) for value in (nm_ids or [])[:1000]],
                "brandNames": [],
                "subjectIds": [],
                "tagIds": [],
                "skipDeletedNm": False,
                "orderBy": {"field": "openCard", "mode": "desc"},
                "limit": page_limit,
                "offset": offset,
            }
            result = self._request(
                "POST",
                f"{self.ANALYTICS_URL}/api/analytics/v3/sales-funnel/products",
                json=body,
            )
            data = result.get("data") if isinstance(result, dict) else result
            chunk = data.get("products") if isinstance(data, dict) else data
            chunk = [row for row in (chunk or []) if isinstance(row, dict)] if isinstance(chunk, list) else []
            rows.extend(chunk)
            if len(chunk) < page_limit:
                break
            offset += page_limit
        return rows

    def get_seller_warehouses(self) -> list[dict[str, Any]]:
        """Return seller-owned FBS/DBS warehouses (read-only)."""
        result = self._request("GET", f"{self.MARKETPLACE_URL}/api/v3/warehouses")
        if isinstance(result, list):
            return [row for row in result if isinstance(row, dict)]
        if isinstance(result, dict):
            rows = result.get("warehouses") or result.get("data") or result.get("items") or []
            return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
        return []

    def get_seller_warehouse_inventory(self, warehouse_id: int, chrt_ids: list[int]) -> list[dict[str, Any]]:
        """Read inventory amounts for one seller warehouse by size IDs."""
        if int(warehouse_id) <= 0:
            raise ValueError("warehouse_id must be positive")
        ids = [int(value) for value in chrt_ids if int(value) > 0][:1000]
        if not ids:
            return []
        result = self._request(
            "POST",
            f"{self.MARKETPLACE_URL}/api/v3/stocks/{int(warehouse_id)}",
            json={"chrtIds": ids},
        )
        rows = result.get("stocks") if isinstance(result, dict) else result
        return [row for row in (rows or []) if isinstance(row, dict)] if isinstance(rows, list) else []


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

    def get_fbs_supplies(self, limit: int = 100) -> list[dict[str, Any]]:
        page_limit = max(1, min(limit, 1000))
        rows: list[dict[str, Any]] = []
        cursor = 0
        for _ in range(100):
            result = self._request("GET", f"{self.MARKETPLACE_URL}/api/v3/supplies", params={"limit": page_limit, "next": cursor})
            if isinstance(result, dict):
                chunk = result.get("supplies") or result.get("data") or result.get("items") or []
                next_cursor = result.get("next")
            else:
                chunk, next_cursor = result, None
            if not isinstance(chunk, list):
                break
            rows.extend(row for row in chunk if isinstance(row, dict))
            if next_cursor in (None, cursor, 0):
                break
            try:
                cursor = int(next_cursor)
            except (TypeError, ValueError):
                break
        return rows

    def get_fbs_supply_details(self, supply_id: str) -> dict[str, Any]:
        result = self._request("GET", f"{self.MARKETPLACE_URL}/api/v3/supplies/{supply_id}")
        return result if isinstance(result, dict) else {}

    def get_fbs_supply_barcode(self, supply_id: str, barcode_type: str = "png") -> dict[str, Any]:
        result = self._request("GET", f"{self.MARKETPLACE_URL}/api/v3/supplies/{supply_id}/barcode", params={"type": barcode_type})
        return result if isinstance(result, dict) else {}

    def get_fbs_new_orders(self) -> list[dict[str, Any]]:
        result = self._request("GET", f"{self.MARKETPLACE_URL}/api/v3/orders/new")
        if isinstance(result, dict):
            result = result.get("orders") or result.get("data") or result.get("items") or []
        return [row for row in result if isinstance(row, dict)] if isinstance(result, list) else []

    def get_fbs_orders(self, date_from: int, date_to: int, limit: int = 1000, next_cursor: int = 0) -> dict[str, Any]:
        """One page of GET /api/v3/orders (FBS assembly tasks); dates are unix seconds. Returns {"orders": [...], "next": int|None}."""
        params = {"limit": max(1, min(int(limit), 1000)), "next": int(next_cursor), "dateFrom": int(date_from), "dateTo": int(date_to)}
        result = self._request("GET", f"{self.MARKETPLACE_URL}/api/v3/orders", params=params)
        rows = result.get("orders") if isinstance(result, dict) else result
        cursor = result.get("next") if isinstance(result, dict) else None
        return {"orders": [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else [], "next": cursor}

    def get_fbs_order_statuses(self, order_ids: list[int]) -> list[dict[str, Any]]:
        """POST /api/v3/orders/status for up to 1000 ids -> [{"id", "supplierStatus", "wbStatus"}]. Read-only despite POST."""
        result = self._request("POST", f"{self.MARKETPLACE_URL}/api/v3/orders/status", json={"orders": [int(i) for i in order_ids[:1000]]})
        rows = result.get("orders") if isinstance(result, dict) else result
        return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []

    def get_acceptance_coefficients(self, warehouse_ids: list[int] | None = None) -> list[dict[str, Any]]:
        params = {"warehouseIDs": ",".join(str(v) for v in warehouse_ids)} if warehouse_ids else None
        result = self._request("GET", f"{self.COMMON_URL}/api/tariffs/v1/acceptance/coefficients", params=params)
        if isinstance(result, dict):
            result = result.get("response") or result.get("data") or result.get("coefficients") or result.get("items") or []
        return [row for row in result if isinstance(row, dict)] if isinstance(result, list) else []

    def get_transit_tariffs(self) -> list[dict[str, Any]]:
        """Return current WB transit routes and box/pallet tariffs (read-only)."""
        result = self._request("GET", f"{self.SUPPLIES_URL}/api/v1/transit-tariffs")
        if isinstance(result, dict):
            result = result.get("response") or result.get("data") or result.get("tariffs") or result.get("items") or []
        return [row for row in result if isinstance(row, dict)] if isinstance(result, list) else []

    def get_deductions(self, date_from: str, date_to: str) -> list[dict[str, Any]]:
        return self._get_retention_report("deductions", date_from, date_to)

    def get_measurement_penalties(self, date_from: str, date_to: str) -> list[dict[str, Any]]:
        return self._get_retention_report("measurement-penalties", date_from, date_to)

    def get_paid_storage_report(self, date_from: str, date_to: str, poll_seconds: float = 5.0, max_polls: int = 30) -> list[dict[str, Any]]:
        """Generate and download paid-storage rows for an arbitrary window.

        WB limits each generation task to eight calendar days, so longer
        finance windows are split into inclusive eight-day chunks.  The report
        is asynchronous and its download endpoint returns HTTP 204 when a
        completed task has no rows; ``_request`` represents that as ``None``.
        """
        start = date.fromisoformat(date_from[:10])
        end = date.fromisoformat(date_to[:10])
        if end < start:
            raise WBAPIError("Некорректный период отчёта платного хранения")
        rows: list[dict[str, Any]] = []
        chunk_start = start
        while chunk_start <= end:
            chunk_end = min(chunk_start + timedelta(days=7), end)
            rows.extend(self._get_paid_storage_chunk(chunk_start.isoformat(), chunk_end.isoformat(), poll_seconds, max_polls))
            chunk_start = chunk_end + timedelta(days=1)
        return rows

    def _get_paid_storage_chunk(self, date_from: str, date_to: str, poll_seconds: float, max_polls: int) -> list[dict[str, Any]]:
        result = self._request("GET", f"{self.ANALYTICS_URL}/api/v1/paid_storage", params={"dateFrom": date_from, "dateTo": date_to})
        data = result.get("data") if isinstance(result, dict) else None
        task_id = data.get("taskId") if isinstance(data, dict) else None
        if not task_id:
            raise WBAPIError("Неожиданный ответ WB при запуске отчёта платного хранения")
        for _ in range(max(1, max_polls)):
            status_result = self._request("GET", f"{self.ANALYTICS_URL}/api/v1/paid_storage/tasks/{task_id}/status")
            status_data = status_result.get("data") if isinstance(status_result, dict) else None
            status = str(status_data.get("status", "")).lower() if isinstance(status_data, dict) else ""
            if status in {"done", "complete", "completed"}:
                downloaded = self._request("GET", f"{self.ANALYTICS_URL}/api/v1/paid_storage/tasks/{task_id}/download")
                if downloaded is None:
                    return []
                if not isinstance(downloaded, list):
                    raise WBAPIError("Неожиданный формат отчёта платного хранения WB")
                return [row for row in downloaded if isinstance(row, dict)]
            if status in {"error", "failed", "canceled", "cancelled"}:
                raise WBAPIError("WB не смог сформировать отчёт платного хранения")
            time.sleep(max(0.0, poll_seconds))
        raise WBAPIError("paid_storage_report_timeout")

    def _get_retention_report(self, endpoint: str, date_from: str, date_to: str) -> list[dict[str, Any]]:
        # These endpoints require limit and return data.reports, not a top-level list.
        # Fetch bounded pages so long periods are complete instead of silently
        # presenting the first 1000 rows as the full financial total.
        limit = 1000
        offset = 0
        all_rows: list[dict[str, Any]] = []
        for _page in range(self.RETENTION_MAX_PAGES):
            params = {"dateFrom": date_from + "T00:00:00+03:00" if len(date_from) == 10 else date_from,
                      "dateTo": date_to + "T23:59:59+03:00" if len(date_to) == 10 else date_to,
                      "limit": limit, "offset": offset}
            result = self._request("GET", f"{self.ANALYTICS_URL}/api/analytics/v1/{endpoint}", params=params, retry_safe=False)
            data = result.get("data") if isinstance(result, dict) else None
            rows = data.get("reports") if isinstance(data, dict) else None
            if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                raise WBAPIError("Неожиданный формат отчёта удержаний WB")
            all_rows.extend(rows)
            total = int(data.get("total", len(all_rows)) or 0)
            if len(all_rows) >= total or len(rows) < limit:
                return all_rows
            offset += len(rows)
        raise WBAPIError("retention_report_too_large")

    def get_product_prices(self, limit: int = 1000) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        offset = 0
        while True:
            result = self._request("GET", f"{self.PRICES_URL}/api/v2/list/goods/filter", params={"limit": max(1, min(limit, 1000)), "offset": offset})
            payload = result.get("data") if isinstance(result, dict) else {}
            chunk = payload.get("listGoods") if isinstance(payload, dict) else []
            if not isinstance(chunk, list) or not chunk:
                break
            rows.extend(row for row in chunk if isinstance(row, dict))
            if len(chunk) < limit:
                break
            offset += limit
        return rows

    def upload_product_prices(self, goods: list[dict[str, Any]]) -> dict[str, Any]:
        if not goods or len(goods) > 1000:
            raise ValueError("WB принимает от 1 до 1000 товаров за одну загрузку")
        result = self._request("POST", f"{self.PRICES_URL}/api/v2/upload/task", json={"data": goods}, retry_safe=False)
        return result if isinstance(result, dict) else {}

    def get_price_upload_status(self, upload_id: int) -> dict[str, Any]:
        result = self._request("GET", f"{self.PRICES_URL}/api/v2/history/tasks", params={"uploadID": int(upload_id)})
        if not isinstance(result, dict) or result.get("error"):
            raise WBAPIError(str((result or {}).get("errorText") or "Статус загрузки пока недоступен"))
        data = result.get("data") or {}
        status = data.get("status")
        labels = {1: "В обработке", 3: "Цены обновлены", 4: "Загрузка отменена", 5: "Обновлено частично", 6: "Все товары отклонены"}
        items = []
        if status in {3, 5, 6}:
            offset = 0
            while True:
                details = self._request("GET", f"{self.PRICES_URL}/api/v2/history/goods/task", params={"uploadID": int(upload_id), "limit": 1000, "offset": offset})
                if not isinstance(details, dict) or details.get("error"):
                    raise WBAPIError("Детализация загрузки WB недоступна")
                chunk = (details.get("data") or {}).get("historyGoods") or []
                items.extend(chunk)
                if len(chunk) < 1000:
                    break
                offset += 1000
        return {"data": data, "label": labels.get(status, "Статус пока неизвестен"), "terminal": status in {3, 4, 5, 6}, "items": items}

    def get_quarantine_goods(self) -> list[dict[str, Any]]:
        """Return goods currently held in WB price quarantine."""
        try:
            result = self._request("GET", f"{self.PRICES_URL}/api/v2/quarantine/goods")
        except WBAPIError as exc:
            # Some WB gateway versions temporarily require an explicit page
            # size although the public contract documents no query params.
            # Retry once with the documented maximum instead of surfacing a
            # misleading empty quarantine block to the dashboard.
            detail = str(exc).lower()
            if "400" not in detail and "limit" not in detail:
                raise
            result = self._request(
                "GET", f"{self.PRICES_URL}/api/v2/quarantine/goods", params={"limit": 1000}
            )
        if isinstance(result, dict):
            payload = result.get("data") or result.get("items") or result.get("goods") or result
            if isinstance(payload, dict):
                payload = payload.get("items") or payload.get("goods") or payload.get("listGoods") or []
            result = payload
        return [row for row in result if isinstance(row, dict)] if isinstance(result, list) else []

    def get_seller_rating(self) -> dict[str, Any]:
        """Return seller valuation and feedback count from the feedbacks API."""
        result = self._request("GET", f"{self.FEEDBACKS_URL}/api/common/v1/rating")
        if isinstance(result, dict):
            payload = result.get("data")
            return payload if isinstance(payload, dict) else result
        return {}

    def get_new_feedbacks_questions(self) -> dict[str, Any]:
        """Return unread feedback/question counters and records when available."""
        result = self._request("GET", f"{self.FEEDBACKS_URL}/api/v1/new-feedbacks-questions")
        return result if isinstance(result, dict) else {}

    def get_feedbacks(self, take: int = 20, skip: int = 0, is_answered: bool = False) -> list[dict[str, Any]]:
        result = self._request("GET", f"{self.FEEDBACKS_URL}/api/v1/feedbacks", params={"take": max(1, min(take, 100)), "skip": max(0, skip), "isAnswered": str(is_answered).lower()})
        if isinstance(result, dict):
            payload = result.get("data") or result.get("feedbacks") or result.get("items") or []
            if isinstance(payload, dict): payload = payload.get("feedbacks") or payload.get("items") or []
            result = payload
        return [row for row in result if isinstance(row, dict)] if isinstance(result, list) else []

    def get_questions(self, take: int = 20, skip: int = 0, is_answered: bool = False) -> list[dict[str, Any]]:
        result = self._request("GET", f"{self.FEEDBACKS_URL}/api/v1/questions", params={"take": max(1, min(take, 100)), "skip": max(0, skip), "isAnswered": str(is_answered).lower()})
        if isinstance(result, dict):
            payload = result.get("data") or result.get("questions") or result.get("items") or []
            if isinstance(payload, dict): payload = payload.get("questions") or payload.get("items") or []
            result = payload
        return [row for row in result if isinstance(row, dict)] if isinstance(result, list) else []

    def get_returns_claims(self, is_archive: bool = False) -> dict[str, Any]:
        """Return buyer return claims; WB requires is_archive as a string."""
        result = self._request("GET", f"{self.RETURNS_URL}/api/v1/claims", params={"is_archive": "true" if is_archive else "false"})
        return result if isinstance(result, dict) else {"claims": result if isinstance(result, list) else []}

    def answer_feedback(self, feedback_id: str, text: str, edit: bool = False) -> dict[str, Any]:
        if not feedback_id or not text.strip(): raise ValueError("ID отзыва и текст ответа обязательны")
        method = "PATCH" if edit else "POST"
        result = self._request(method, f"{self.FEEDBACKS_URL}/api/v1/feedbacks/answer", json={"id": feedback_id, "text": text.strip()})
        return result if isinstance(result, dict) else {}

    def answer_question(self, question_id: str, text: str, state: str = "wbRu") -> dict[str, Any]:
        if not question_id or not text.strip(): raise ValueError("ID вопроса и текст ответа обязательны")
        result = self._request("PATCH", f"{self.FEEDBACKS_URL}/api/v1/questions", json={"id": question_id, "text": text.strip(), "state": state})
        return result if isinstance(result, dict) else {}

    def process_return_claim(self, claim_id: str, action: str) -> dict[str, Any]:
        if not claim_id or not action: raise ValueError("ID заявки и действие обязательны")
        result = self._request("PATCH", f"{self.RETURNS_URL}/api/v1/claim", json={"id": claim_id, "action": action})
        return result if isinstance(result, dict) else {}

    def get_goods_returns(self, date_from: str | None = None, date_to: str | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {}
        if date_from: params["dateFrom"] = date_from
        if date_to: params["dateTo"] = date_to
        result = self._request("GET", f"{self.RETURNS_URL}/api/v1/analytics/goods-return", params=params or None)
        if isinstance(result, dict):
            result = result.get("data") or result.get("items") or result.get("returns") or result.get("report") or []
        return [row for row in result if isinstance(row, dict)] if isinstance(result, list) else []

    def get_seller_chats(self) -> list[dict[str, Any]]:
        result = self._request("GET", f"{self.CHAT_URL}/api/v1/seller/chats")
        if isinstance(result, dict):
            result = result.get("chats") or result.get("data") or result.get("items") or []
        return [row for row in result if isinstance(row, dict)] if isinstance(result, list) else []

    def send_seller_message(self, reply_sign: str, text: str) -> dict[str, Any]:
        """Send a text message to a buyer chat using its current replySign.

        WB requires multipart/form-data even when no attachment is sent. The
        ``files`` tuples with ``filename=None`` encode ordinary form fields
        while keeping the request multipart and avoiding a fake empty file.
        This is a write operation, so retries stay disabled.
        """
        reply_sign = reply_sign.strip()
        text = text.strip()
        if not reply_sign or len(reply_sign) > 255:
            raise ValueError("Подпись чата обязательна и не должна превышать 255 символов")
        if not text or len(text) > 1000:
            raise ValueError("Сообщение обязательно и не должно превышать 1000 символов")
        result = self._request(
            "POST",
            f"{self.CHAT_URL}/api/v1/seller/message",
            files=[("replySign", (None, reply_sign)), ("message", (None, text))],
            retry_safe=False,
        )
        return result if isinstance(result, dict) else {}

    def get_product_cards(self, nm_ids: list[int] | None = None) -> list[dict[str, Any]]:
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
                    "filter": {"withPhoto": -1, **({"nmIDs": nm_ids[:1000]} if nm_ids else {})},
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

    def update_product_card(self, card: dict[str, Any]) -> dict[str, Any]:
        """Replace a card's text and characteristics through the Content API.

        WB treats this endpoint as a full replacement. Callers must therefore
        pass a complete card object read from ``get_product_cards`` and change
        only the intended fields before submitting it. This method is a write
        operation and never retries an ambiguous request.
        """
        if not isinstance(card, dict) or not card.get("nmID"):
            raise ValueError("Полная карточка WB с nmID обязательна")
        result = self._request(
            "POST",
            f"{self.CONTENT_URL}/content/v2/cards/update",
            json=[card],
            retry_safe=False,
        )
        return result if isinstance(result, dict) else {}

    def save_product_media(self, nm_id: int, urls: list[str]) -> dict[str, Any]:
        """Replace the card media order using a complete list of HTTPS URLs."""
        if int(nm_id) <= 0:
            raise ValueError("Артикул WB должен быть положительным")
        clean_urls = [str(url).strip() for url in urls if str(url).strip()]
        if not clean_urls or len(clean_urls) > 30:
            raise ValueError("Передайте от 1 до 30 ссылок на медиафайлы")
        if any(not url.lower().startswith(("https://", "http://")) for url in clean_urls):
            raise ValueError("Ссылки на медиафайлы должны начинаться с http:// или https://")
        result = self._request(
            "POST",
            f"{self.CONTENT_URL}/content/v3/media/save",
            json={"nmId": int(nm_id), "data": clean_urls},
            retry_safe=False,
        )
        return result if isinstance(result, dict) else {}

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
