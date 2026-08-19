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
    pass


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


def extract_payment_method_id(payment: dict[str, Any]) -> str:
    """Empty string if the customer didn't consent to saving their card."""
    method = payment.get("payment_method") or {}
    if method.get("saved"):
        return str(method.get("id", ""))
    return ""
