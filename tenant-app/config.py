from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from secure_storage import read as read_encrypted, write as write_encrypted

BASE_DIR = Path(__file__).resolve().parent
# Gateway-managed tenant containers keep their persistent volume at
# /app/data.  The React/Nginx image runs the Python application from
# /app/tenant-app, while the legacy image runs it from /app, so deriving the
# data directory only from this file's location makes the redesign silently
# open a fresh database.  Prefer an explicit override, then the gateway mount,
# and retain the source-relative path for local/manual deployments.
_GATEWAY_DATA_DIR = Path("/app/data")
DATA_DIR = Path(
    os.environ.get(
        "WB_SAAS_DATA_DIR",
        str(_GATEWAY_DATA_DIR if _GATEWAY_DATA_DIR.exists() else BASE_DIR / "data"),
    )
)
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "wb_dashboard.sqlite3"
SETTINGS_PATH = DATA_DIR / "settings.json"
TOKEN_FALLBACK_PATH = DATA_DIR / ".wb_token"
KEYRING_SERVICE = "WB Dashboard Local"
KEYRING_USER = "wb_api_token"

TOKEN_FALLBACK_PATH_OZON = DATA_DIR / ".ozon_credentials"
KEYRING_USER_OZON = "ozon_api_credentials"

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

# Set by the gateway (provisioning.py) so this container can identify and
# authenticate itself when calling back to the gateway's internal API (see
# reports/delivery.py) -- e.g. emailing a scheduled report to the account
# owner. This container is deliberately not on the gateway's docker network
# (per-tenant network isolation), so INTERNAL_API_URL is the gateway's
# public HTTPS domain, not a docker-internal hostname. All empty when run
# standalone -- email delivery is then simply unavailable, same
# graceful-degradation pattern as BILLING_URL above.
TENANT_SLUG = os.environ.get("WB_SAAS_TENANT_SLUG", "")
INTERNAL_API_URL = os.environ.get("WB_SAAS_INTERNAL_API_URL", "")
INTERNAL_API_SECRET = os.environ.get("WB_SAAS_INTERNAL_API_SECRET", "")
DATA_ENCRYPTION_KEY = os.environ.get("WB_SAAS_DATA_ENCRYPTION_KEY", "")
# A JSON object of historical key ids to Fernet keys.  Keep old keys here
# during rotation so existing encrypted backups remain restorable.
DATA_ENCRYPTION_KEY_ID = os.environ.get("WB_SAAS_DATA_ENCRYPTION_KEY_ID", "current")
DATA_ENCRYPTION_KEYRING = os.environ.get("WB_SAAS_DATA_ENCRYPTION_KEYRING", "")

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
    "agent_interval_minutes": 60,
    # Page names (see app.py's PAGES/NOVICE_NAV) whose onboarding hint card
    # (onboarding.render_page_hint) the user has dismissed -- kept here, not
    # in st.session_state, so it stays hidden across reloads and sessions
    # like ui_mode itself.
    "dismissed_hints": [],
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
    if TENANT_SLUG:
        write_encrypted(TOKEN_FALLBACK_PATH, token, DATA_ENCRYPTION_KEY)
        return
    try:
        import keyring

        keyring.set_password(KEYRING_SERVICE, KEYRING_USER, token)
        if TOKEN_FALLBACK_PATH.exists():
            TOKEN_FALLBACK_PATH.unlink()
        return
    except Exception:
        # Резервный вариант для сред без системного хранилища ключей.
        write_encrypted(TOKEN_FALLBACK_PATH, token, DATA_ENCRYPTION_KEY)


def get_token() -> str | None:
    if TENANT_SLUG and TOKEN_FALLBACK_PATH.exists():
        value, legacy = read_encrypted(TOKEN_FALLBACK_PATH, DATA_ENCRYPTION_KEY)
        # read_encrypted validates the existing key even for legacy files.
        # Migrate before returning the token; never generate or rotate keys.
        if value and legacy:
            write_encrypted(TOKEN_FALLBACK_PATH, value, DATA_ENCRYPTION_KEY)
        return value
    try:
        import keyring

        value = keyring.get_password(KEYRING_SERVICE, KEYRING_USER)
        if value:
            return value.strip()
    except Exception:
        pass
    if TOKEN_FALLBACK_PATH.exists():
        value, legacy = read_encrypted(TOKEN_FALLBACK_PATH, DATA_ENCRYPTION_KEY)
        if value and legacy and DATA_ENCRYPTION_KEY:
            write_encrypted(TOKEN_FALLBACK_PATH, value, DATA_ENCRYPTION_KEY)
        return value
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


def save_ozon_credentials(client_id: str, api_key: str) -> None:
    client_id = client_id.strip()
    api_key = api_key.strip()
    if not client_id or not api_key:
        raise ValueError("Client-Id и Api-Key обязательны")
    blob = json.dumps({"client_id": client_id, "api_key": api_key})
    if TENANT_SLUG:
        write_encrypted(TOKEN_FALLBACK_PATH_OZON, blob, DATA_ENCRYPTION_KEY)
        return
    try:
        import keyring

        keyring.set_password(KEYRING_SERVICE, KEYRING_USER_OZON, blob)
        if TOKEN_FALLBACK_PATH_OZON.exists():
            TOKEN_FALLBACK_PATH_OZON.unlink()
        return
    except Exception:
        write_encrypted(TOKEN_FALLBACK_PATH_OZON, blob, DATA_ENCRYPTION_KEY)


def get_ozon_credentials() -> tuple[str, str] | None:
    blob = None
    if TENANT_SLUG and TOKEN_FALLBACK_PATH_OZON.exists():
        blob, legacy = read_encrypted(TOKEN_FALLBACK_PATH_OZON, DATA_ENCRYPTION_KEY)
        if blob and legacy and DATA_ENCRYPTION_KEY:
            write_encrypted(TOKEN_FALLBACK_PATH_OZON, blob, DATA_ENCRYPTION_KEY)
    else:
        legacy = False
    try:
        if TENANT_SLUG:
            raise RuntimeError("SaaS tenants use encrypted file storage")
        import keyring

        blob = keyring.get_password(KEYRING_SERVICE, KEYRING_USER_OZON)
    except Exception:
        pass
    if not blob and TOKEN_FALLBACK_PATH_OZON.exists():
        blob, legacy = read_encrypted(TOKEN_FALLBACK_PATH_OZON, DATA_ENCRYPTION_KEY)
        if blob and legacy and DATA_ENCRYPTION_KEY:
            write_encrypted(TOKEN_FALLBACK_PATH_OZON, blob, DATA_ENCRYPTION_KEY)
    if not blob:
        return None
    try:
        data = json.loads(blob)
        client_id, api_key = data.get("client_id", ""), data.get("api_key", "")
    except (json.JSONDecodeError, AttributeError):
        return None
    if not client_id or not api_key:
        return None
    return client_id, api_key


def delete_ozon_credentials() -> None:
    try:
        import keyring

        keyring.delete_password(KEYRING_SERVICE, KEYRING_USER_OZON)
    except Exception:
        pass
    try:
        TOKEN_FALLBACK_PATH_OZON.unlink(missing_ok=True)
    except OSError:
        pass
