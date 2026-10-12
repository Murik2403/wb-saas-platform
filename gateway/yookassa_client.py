"""Thin wrapper around the YooKassa REST API (v3).

Cannot be exercised in the sandbox this was authored in (no network access,
no real shop credentials) -- kept deliberately thin and boring so the parts
that actually decide what happens to an account (logic/billing.py) stay
fully unit-tested even though this file isn't. Test against YooKassa's own
test-mode credentials before going live -- see DEPLOY.md.

Docs: https://yookassa.ru/developers/api

Security note (important, see app.py's webhook handler): YooKassa webhook
notifications are NOT signed. Never act on a webhook body directly --
always re-fetch the payment by id via get_payment() (an authenticated
GET, using our own shop credentials) and act on THAT response. Otherwise
anyone who discovers the webhook URL could POST a fake "payment.succeeded"
and grant themselves a free subscription.
"""
from __future__ import annotations

import uuid
from typing import Any

import config


class YooKassaError(RuntimeError):
    """``status_code`` is the HTTP status when the provider answered; None when it never did (timeout, network)."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code

    @property
    def definitive(self) -> bool:
        """True when the provider clearly refused and did not act (a client error), so the attempt may be released.

        A timeout, 5xx, 408/409/425/429 or no answer at all leaves the outcome unknown: the request may have been applied.
        """
        return self.status_code is not None and 400 <= self.status_code < 500 and self.status_code not in {408, 409, 425, 429}


def _auth() -> tuple[str, str]:
    if not config.YOOKASSA_SHOP_ID or not config.YOOKASSA_SECRET_KEY:
        raise YooKassaError(
            "WB_SAAS_YOOKASSA_SHOP_ID / WB_SAAS_YOOKASSA_SECRET_KEY не настроены."
        )
    return (config.YOOKASSA_SHOP_ID, config.YOOKASSA_SECRET_KEY)


def _base_url() -> str:
    return "https://api.yookassa.ru/v3"


def create_payment(
    amount_rub: float,
    description: str,
    idempotence_key: str | None = None,
    return_url: str | None = None,
    payment_method_id: str | None = None,
    save_payment_method: bool = False,
    metadata: dict[str, Any] | None = None,
    customer_email: str | None = None,
) -> dict[str, Any]:
    """Creates a payment.

    Two modes:
    - First-time / manual checkout (payment_method_id=None): pass
      return_url and save_payment_method=True. The response contains
      confirmation.confirmation_url -- redirect the browser there so the
      customer enters card details on YooKassa's own (PCI-compliant)
      hosted page. We never see or store card numbers.
    - Recurring off-session charge (payment_method_id set, from a prior
      saved card): no return_url/confirmation needed -- YooKassa charges
      the saved method directly. Requires "автоплатежи" to be enabled on
      the shop account (a one-time setup with YooKassa support, not code).
    """
    import requests  # imported lazily so this module can be imported without `requests` installed

    body: dict[str, Any] = {
        "amount": {"value": f"{amount_rub:.2f}", "currency": "RUB"},
        "capture": True,
        "description": description,
    }
    if config.RECEIPT_ENABLED:
        if not customer_email:
            raise YooKassaError("Для чека 54-ФЗ нужен email покупателя")
        body["receipt"] = {
            "customer": {"email": customer_email},
            "tax_system_code": config.RECEIPT_TAX_SYSTEM_CODE,
            "items": [{
                "description": description[:128],
                "quantity": "1.00",
                "amount": {"value": f"{amount_rub:.2f}", "currency": "RUB"},
                "vat_code": config.RECEIPT_VAT_CODE,
                "payment_subject": "service",
                "payment_mode": "full_payment",
            }],
        }
    if payment_method_id:
        body["payment_method_id"] = payment_method_id
    else:
        body["confirmation"] = {"type": "redirect", "return_url": return_url}
        if save_payment_method:
            body["save_payment_method"] = True
    if metadata:
        body["metadata"] = metadata

    response = requests.post(
        f"{_base_url()}/payments",
        json=body,
        auth=_auth(),
        headers={"Idempotence-Key": idempotence_key or str(uuid.uuid4())},
        timeout=30,
    )
    if response.status_code >= 400:
        raise YooKassaError(f"YooKassa create_payment failed ({response.status_code}): {response.text}")
    return response.json()


def get_payment(payment_id: str) -> dict[str, Any]:
    """Authoritative payment status -- always use this, never the webhook
    body, to decide what happens to an account (see module docstring)."""
    import requests

    response = requests.get(f"{_base_url()}/payments/{payment_id}", auth=_auth(), timeout=30)
    if response.status_code >= 400:
        raise YooKassaError(f"YooKassa get_payment failed ({response.status_code}): {response.text}")
    return response.json()


def get_refund(refund_id: str) -> dict[str, Any]:
    """Fetch authoritative refund status for an unsigned webhook."""
    import requests

    try:
        response = requests.get(f"{_base_url()}/refunds/{refund_id}", auth=_auth(), timeout=30)
    except requests.RequestException:
        raise YooKassaError("YooKassa refund lookup unavailable") from None
    if response.status_code >= 400:
        raise YooKassaError(f"YooKassa refund lookup failed ({response.status_code}): {response.text}")
    return response.json()


def create_refund(payment_id: str, amount_rub: float | None = None, idempotence_key: str | None = None,
                  description: str = "") -> dict[str, Any]:
    """Request a full or partial refund in YooKassa."""
    import requests
    body: dict[str, Any] = {"payment_id": payment_id}
    if description:
        body["description"] = description
    if amount_rub is not None:
        body["amount"] = {"value": f"{amount_rub:.2f}", "currency": "RUB"}
    try:
        response = requests.post(f"{_base_url()}/refunds", json=body, auth=_auth(), headers={"Idempotence-Key": idempotence_key or str(uuid.uuid4())}, timeout=30)
    except requests.RequestException:
        raise YooKassaError("YooKassa refund request got no answer") from None
    if response.status_code >= 400:
        raise YooKassaError(f"YooKassa refund failed ({response.status_code}): {response.text}", response.status_code)
    return response.json()


def list_refunds(payment_id: str) -> list[dict[str, Any]]:
    """A complete validated provider list, or an error. Partial lists can never release reserved money."""
    import requests
    from time import monotonic

    deadline, cursor, seen, result = monotonic() + 60, "", set(), []
    for _ in range(100):  # At most 10,000 records; exceeding the bound requires manual reconciliation.
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise YooKassaError("YooKassa refund list deadline exceeded; incomplete list")
        params = {"payment_id": payment_id, "limit": 100}
        if cursor:
            params["cursor"] = cursor
        try:
            response = requests.get(f"{_base_url()}/refunds", params=params, auth=_auth(), timeout=min(30, remaining))
        except requests.RequestException:
            raise YooKassaError("YooKassa refund list unavailable") from None
        if response.status_code >= 400:
            raise YooKassaError(f"YooKassa refund list failed ({response.status_code})", response.status_code)
        try:
            payload = response.json()
        except (ValueError, TypeError):
            raise YooKassaError("YooKassa refund list invalid JSON") from None
        if (not isinstance(payload, dict) or payload.get("type") != "list" or not isinstance(payload.get("items"), list)
                or any(not isinstance(item, dict) for item in payload["items"])):
            raise YooKassaError("YooKassa refund list malformed")
        items, next_cursor = payload["items"], payload.get("next_cursor")
        if next_cursor is not None and (not isinstance(next_cursor, str) or not next_cursor or not items or next_cursor in seen):
            raise YooKassaError("YooKassa refund list invalid pagination")
        result.extend(items)
        if next_cursor is None:
            return result
        seen.add(next_cursor)
        cursor = next_cursor
    raise YooKassaError("YooKassa refund list exceeds safe pagination limit")


def extract_payment_method_id(payment: dict[str, Any]) -> str:
    """Empty string if the customer didn't consent to saving their card."""
    method = payment.get("payment_method") or {}
    if method.get("saved"):
        return str(method.get("id", ""))
    return ""
