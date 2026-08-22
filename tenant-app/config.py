from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
DB_PATH = DATA_DIR / "wb_dashboard.sqlite3"
SETTINGS_PATH = DATA_DIR / "settings.json"
TOKEN_FALLBACK_PATH = DATA_DIR / ".wb_token"
KEYRING_SERVICE = "WB Dashboard Local"
KEYRING_USER = "wb_api_token"

# Set by the SaaS gateway (provisioning.py) when this container is one of
# its tenants, pointing back at https://<parent-domain>/billing. Empty when
# the dashboard is run standalone (no gateway) -- in that case the sidebar
# simply doesn't show a subscription link, since there's nothing to link to.
BILLING_URL = os.environ.get("WB_SAAS_BILLING_URL", "")

# Same reasoning as BILLING_URL: points back at the gateway's own /logout
# (session cookie lives on the parent domain, not this container). Empty
# when run standalone -- nothing to log out of without a gateway.
LOGOUT_URL = os.environ.get("WB_SAAS_LOGOUT_URL", "")

# Set on the one public "demo" tenant (see ../scripts/provision_demo_tenant.py)
# that the marketing site embeds for anonymous visitors. That tenant's
# Traefik router intentionally has no ForwardAuth middleware, so anyone can
# open it -- the dashboard itself must not expose anything writable
# (token/cost editing) or any page beyond the read-only headline ones.
IS_DEMO = os.environ.get("WB_SAAS_IS_DEMO", "0") == "1"

DEFAULT_SETTINGS: dict[str, Any] = {
    "sync_interval_minutes": 30,
    "initial_history_days": 90,
    "advert_history_days": 31,
    "default_period_days": 30,
    "currency": "RUB",
    "demo_mode": False,
    # "novice" hides the advanced pages (Производство/Закупки/Реклама/Остатки/
    # Контроль) so a first-time seller isn't dropped into the full instrument
    # panel on day one; "expert" (default) is the full dashboard as before.
    "ui_mode": "expert",
}


def load_settings() -> dict[str, Any]:
    data = DEFAULT_SETTINGS.copy()
    if SETTINGS_PATH.exists():
        try:
            loaded = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data.update(loaded)
        except (OSError, json.JSONDecodeError):
            pass
    return data


def save_settings(settings: dict[str, Any]) -> None:
    merged = DEFAULT_SETTINGS.copy()
    merged.update(settings)
    SETTINGS_PATH.write_text(
        json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def save_token(token: str) -> None:
    token = token.strip()
    if not token:
        raise ValueError("Пустой токен")
    try:
        import keyring

        keyring.set_password(KEYRING_SERVICE, KEYRING_USER, token)
        if TOKEN_FALLBACK_PATH.exists():
            TOKEN_FALLBACK_PATH.unlink()
        return
    except Exception:
        # Резервный вариант для сред без системного хранилища ключей.
        TOKEN_FALLBACK_PATH.write_text(token, encoding="utf-8")
        try:
            os.chmod(TOKEN_FALLBACK_PATH, 0o600)
        except OSError:
            pass


def get_token() -> str | None:
    try:
        import keyring

        value = keyring.get_password(KEYRING_SERVICE, KEYRING_USER)
        if value:
            return value.strip()
    except Exception:
        pass
    if TOKEN_FALLBACK_PATH.exists():
        try:
            value = TOKEN_FALLBACK_PATH.read_text(encoding="utf-8").strip()
            return value or None
        except OSError:
            return None
    return None


def delete_token() -> None:
    try:
        import keyring

        keyring.delete_password(KEYRING_SERVICE, KEYRING_USER)
    except Exception:
        pass
    try:
        TOKEN_FALLBACK_PATH.unlink(missing_ok=True)
    except OSError:
        pass
