"""Gateway configuration, read from environment variables.

Kept tiny and dependency-free (stdlib os.environ only) so it can be
imported by both the pure-logic tests and the FastAPI app without pulling
in FastAPI itself.
"""
from __future__ import annotations

import os

# The site the gateway itself serves, e.g. "wbsaas.ru". Registration/login live here.
PARENT_DOMAIN = os.environ.get("WB_SAAS_PARENT_DOMAIN", "localhost")

# Tenants live at {slug}.{TENANT_SUBDOMAIN_PREFIX}.{PARENT_DOMAIN}, e.g.
# seller1.app.wbsaas.ru. Kept as a distinct prefix (rather than {slug}.wbsaas.ru
# directly) so the parent domain's own pages (/, /login, /register) can never
# collide with a client's chosen slug.
TENANT_SUBDOMAIN_PREFIX = os.environ.get("WB_SAAS_TENANT_SUBDOMAIN_PREFIX", "app")

# Docker image tag used for every newly-provisioned tenant container.
# New tenants use the finalized React redesign unless explicitly configured otherwise.
TENANT_IMAGE = os.environ.get("WB_SAAS_TENANT_IMAGE", "wb-dashboard-tenant:redesign")

# Docker network shared by the gateway, Traefik, and every tenant container.
DOCKER_NETWORK = os.environ.get("WB_SAAS_DOCKER_NETWORK", "wbsaas_net")

# Internal port the tenant web application listens on inside its container.
TENANT_INTERNAL_PORT = int(os.environ.get("WB_SAAS_TENANT_INTERNAL_PORT", "8501"))

# Per-tenant resource caps, so one client's runaway sync/report can't starve
# every other client sharing the same host. Tune to your VPS size.
TENANT_MEM_LIMIT = os.environ.get("WB_SAAS_TENANT_MEM_LIMIT", "768m")
TENANT_CPU_QUOTA = float(os.environ.get("WB_SAAS_TENANT_CPU_QUOTA", "1.0"))  # in CPU cores

# The gateway never talks to the Docker daemon. It can only call this
# internal service, which validates and executes the small tenant lifecycle
# contract while holding the host socket.
PROVISIONER_URL = os.environ.get("WB_SAAS_PROVISIONER_URL", "http://provisioner:8001")

# Name of the Traefik ForwardAuth middleware (defined once, via labels on
# the gateway service in docker-compose.yml) that every tenant router
# references. Must match exactly what's in docker-compose.yml.
FORWARD_AUTH_MIDDLEWARE = os.environ.get("WB_SAAS_FORWARD_AUTH_MIDDLEWARE", "wb-saas-auth@file")

# Compose's default container-naming convention (<project>-<service>-1) for
# the traefik service in docker-compose.yml -- provisioning.py needs the
# real container to attach it to each tenant's dedicated network (see
# ensure_tenant_network/_attach_traefik).
TRAEFIK_CONTAINER_NAME = os.environ.get("WB_SAAS_TRAEFIK_CONTAINER_NAME", "wb-saas-traefik-1")

# Name of the Traefik certresolver configured in traefik/traefik.yml.
TLS_CERT_RESOLVER = os.environ.get("WB_SAAS_TLS_CERT_RESOLVER", "letsencrypt")

PROVISION_HEALTH_TIMEOUT_SECONDS = int(os.environ.get("WB_SAAS_PROVISION_TIMEOUT", "180"))

# Set to "1" in production behind HTTPS; cookies are only sent over TLS then.
COOKIE_SECURE = os.environ.get("WB_SAAS_COOKIE_SECURE", "0") == "1"

SESSION_COOKIE_NAME = "wb_saas_session"

# --------------------------------------------------------------------------
# Billing (see logic/billing.py + yookassa_client.py)
# --------------------------------------------------------------------------

TRIAL_DAYS = int(os.environ.get("WB_SAAS_TRIAL_DAYS", "14"))
BILLING_PERIOD_DAYS = int(os.environ.get("WB_SAAS_BILLING_PERIOD_DAYS", "30"))
SUBSCRIPTION_PRICE_RUB = float(os.environ.get("WB_SAAS_SUBSCRIPTION_PRICE_RUB", "2990"))
RECEIPT_ENABLED = os.environ.get("WB_SAAS_RECEIPT_ENABLED", "0") == "1"
RECEIPT_VAT_CODE = int(os.environ.get("WB_SAAS_VAT_CODE", "1"))
RECEIPT_TAX_SYSTEM_CODE = int(os.environ.get("WB_SAAS_TAX_SYSTEM_CODE", "1"))
# How long a client keeps access after a failed/missing payment before
# being locked out and their container stopped. Long enough that a
# temporarily-declined card isn't an instant outage, short enough that it's
# not effectively a second free trial.
GRACE_PERIOD_DAYS = int(os.environ.get("WB_SAAS_GRACE_PERIOD_DAYS", "5"))

YOOKASSA_SHOP_ID = os.environ.get("WB_SAAS_YOOKASSA_SHOP_ID", "")
YOOKASSA_SECRET_KEY = os.environ.get("WB_SAAS_YOOKASSA_SECRET_KEY", "")
YOOKASSA_WEBHOOK_CIDRS = tuple(
    value.strip() for value in os.environ.get("WB_SAAS_YOOKASSA_WEBHOOK_CIDRS", "").split(",") if value.strip()
)

# --------------------------------------------------------------------------
# Host-level backups (see ../scripts/backup_tenant_volumes.py)
# --------------------------------------------------------------------------

# Where tar.gz snapshots of every tenant's Docker volume are written on the
# host. Local-only -- a separate offsite sync (rsync/rclone/S3, your choice)
# is still required, see DEPLOY.md.
BACKUP_DIR = os.environ.get("WB_SAAS_BACKUP_DIR", "/opt/wb-saas/backups")
BACKUP_KEEP_DAYS = int(os.environ.get("WB_SAAS_BACKUP_KEEP_DAYS", "14"))

# --------------------------------------------------------------------------
# Password reset (see logic/password_reset.py + mailer.py)
# --------------------------------------------------------------------------

# How long a reset link stays valid after being requested.
PASSWORD_RESET_TTL_MINUTES = int(os.environ.get("WB_SAAS_PASSWORD_RESET_TTL_MINUTES", "60"))
# Browser password changes use the HIBP range API. A short bounded timeout
# avoids tying up a gateway worker when the remote service is unreachable.
HIBP_PASSWORD_TIMEOUT_SECONDS = max(1, int(os.environ.get("WB_SAAS_HIBP_TIMEOUT_SECONDS", "3")))

SMTP_HOST = os.environ.get("WB_SAAS_SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("WB_SAAS_SMTP_PORT", "587"))
SMTP_USER = os.environ.get("WB_SAAS_SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("WB_SAAS_SMTP_PASSWORD", "")
# "1" for STARTTLS on a plaintext-then-upgrade port (587, the common case),
# "0" only for a pre-configured implicit-TLS/local relay setup.
SMTP_USE_STARTTLS = os.environ.get("WB_SAAS_SMTP_USE_STARTTLS", "1") == "1"
SMTP_FROM_EMAIL = os.environ.get("WB_SAAS_SMTP_FROM_EMAIL", "no-reply@" + PARENT_DOMAIN)

# --------------------------------------------------------------------------
# Telegram support relay (see telegram_bot.py)
# --------------------------------------------------------------------------

# From @BotFather. Empty disables the relay entirely -- telegram_bot.py's
# polling loop exits immediately instead of erroring, same pattern as an
# unconfigured SMTP_HOST in mailer.py.
TELEGRAM_ENABLED = os.environ.get("WB_SAAS_TELEGRAM_ENABLED", "1").strip().lower() not in {"0", "false", "no", "off"}
TELEGRAM_BOT_TOKEN = os.environ.get("WB_SAAS_TELEGRAM_BOT_TOKEN", "")

# Public @username from BotFather. Keeping it in configuration lets tenant
# pages build the one-click deep link without blocking on Telegram's getMe
# endpoint, which can be unreachable from the host for tens of seconds.
TELEGRAM_BOT_USERNAME = os.environ.get("WB_SAAS_TELEGRAM_BOT_USERNAME", "").lstrip("@")

# The operator's own numeric Telegram chat id (from @userinfobot) -- every
# message a customer sends the bot is relayed here so a human sees it.
TELEGRAM_OWNER_CHAT_ID = os.environ.get("WB_SAAS_TELEGRAM_OWNER_CHAT_ID", "")

# --------------------------------------------------------------------------
# Internal API (see internal_routes.py): lets a tenant container ask the
# gateway to email/relay a scheduled report on its behalf. Tenant containers
# are deliberately NOT on the gateway's docker network (see provisioning.py's
# per-tenant network isolation), so this is reached over the same public
# HTTPS domain as everything else, authenticated by this shared secret
# instead of a user session. Baked into every tenant container's environment
# at provision time (see provisioning.py's _run_tenant_container). Empty
# disables the feature -- internal_routes.py rejects every call until set.
# --------------------------------------------------------------------------

INTERNAL_API_SECRET = os.environ.get("WB_SAAS_INTERNAL_API_SECRET", "")
PROVISIONER_BEARER_KEY = os.environ.get('WB_SAAS_PROVISIONER_BEARER_KEY','')
# A separate key may be configured; otherwise the internal key is used with
# purpose-separated HMAC. Empty keys disable optional public analytics.
GROWTH_COOKIE_SECRET = os.environ.get("WB_SAAS_GROWTH_COOKIE_SECRET", "")
# Error reporting is opt-in: no telemetry leaves the deployment until an
# operator sets one of these DSNs.
SENTRY_DSN = os.environ.get("WB_SAAS_SENTRY_DSN", "")
# Keep tenant dashboards reporting in the same Sentry project by default,
# unless a separate tenant DSN is explicitly configured.
_TENANT_DSN_ENV = os.environ.get("WB_SAAS_SENTRY_TENANT_DSN", "").strip()
SENTRY_TENANT_DSN = _TENANT_DSN_ENV or SENTRY_DSN
SENTRY_ENVIRONMENT = os.environ.get("WB_SAAS_SENTRY_ENVIRONMENT", "production")
# Upper bound for one scheduled-report PDF accepted from a tenant.  The
# gateway reads at most one byte beyond this value before rejecting it.
MAX_REPORT_UPLOAD_BYTES = max(1, int(os.environ.get("WB_SAAS_MAX_REPORT_UPLOAD_BYTES", str(20 * 1024 * 1024))))

# Deployment-only master material. provisioning.py derives a different key
# for every tenant and never sends this value outside gateway.
TENANT_ENCRYPTION_MASTER_KEY = os.environ.get("WB_SAAS_TENANT_ENCRYPTION_MASTER_KEY", "")
# Optional retained tenant backup keys. The value is passed only to the
# matching tenant container and should be supplied by a secret manager.
TENANT_ENCRYPTION_KEY_ID = os.environ.get("WB_SAAS_TENANT_ENCRYPTION_KEY_ID", "current")
TENANT_ENCRYPTION_KEYRING = os.environ.get("WB_SAAS_TENANT_ENCRYPTION_KEYRING", "")

# Number of external reverse proxies in front of Traefik that have been
# explicitly configured as trusted in traefik.yml. A direct public Traefik
# deployment has no such proxy, so the address appended on the right is used.
TRUSTED_PROXY_HOPS = max(0, min(8, int(os.environ.get("WB_SAAS_TRUSTED_PROXY_HOPS", "0"))))

# --------------------------------------------------------------------------
# Control-plane database (see logic/db.py)
# --------------------------------------------------------------------------

# "sqlite" (default, current production setup -- a single file is plenty
# for the current handful of tenants) or "postgres" (for when concurrent
# writers to the shared control-plane db -- accounts/billing/sessions,
# NOT tenant data -- actually start contending). Switching requires
# WB_SAAS_DATABASE_URL to be set and psycopg[binary] installed; see
# DEPLOY.md's "Switching the control-plane db to Postgres" section.
DB_BACKEND = os.environ.get("WB_SAAS_DB_BACKEND", "sqlite")

# Postgres DSN, e.g. "postgresql://user:pass@host:5432/wbsaas". Only read
# when DB_BACKEND == "postgres".
DATABASE_URL = os.environ.get("WB_SAAS_DATABASE_URL", "")

# --------------------------------------------------------------------------
# Admin panel (see admin_routes.py, logic/accounts.py's is_admin_account)
# --------------------------------------------------------------------------

# Comma-separated allowlist. Empty (the default) means nobody can reach
# /admin, even a logged-in account -- there is no role column, just this
# allowlist, since this is a single-operator tool rather than a
# multi-admin permission system.
ADMIN_EMAILS = [e.strip().lower() for e in os.environ.get("WB_SAAS_ADMIN_EMAILS", "").split(",") if e.strip()]


# --------------------------------------------------------------------------
# Public-site analytics and search-engine verification
# --------------------------------------------------------------------------
# All three are opt-in: empty value means the corresponding tag is simply not
# rendered, so local dev and CI never ship a tracker or phone home.
YANDEX_METRIKA_ID = os.environ.get("WB_SAAS_YANDEX_METRIKA_ID", "").strip()
YANDEX_VERIFICATION = os.environ.get("WB_SAAS_YANDEX_VERIFICATION", "").strip()
GOOGLE_VERIFICATION = os.environ.get("WB_SAAS_GOOGLE_VERIFICATION", "").strip()


# --------------------------------------------------------------------------
# Mobile apps (Capacitor shell). Store builds must not sell the subscription.
# --------------------------------------------------------------------------
APP_UA_TOKEN = "MarketsHelperApp/"
# Android Digital Asset Links (/.well-known/assetlinks.json); SHA-256 list is comma-separated.
ANDROID_PACKAGE = os.environ.get("WB_SAAS_ANDROID_PACKAGE", "").strip()
ANDROID_SHA256 = [v.strip() for v in os.environ.get("WB_SAAS_ANDROID_SHA256", "").split(",") if v.strip()]
# Apple Team ID + bundle id, e.g. "ABCDE12345.ru.marketshelper.app".
IOS_APP_ID = os.environ.get("WB_SAAS_IOS_APP_ID", "").strip()
# Fallback <lastmod> for public pages that have no explicit date; bump when
# a template changes materially (never "today").
SITE_RELEASE_DATE = "2026-10-08"


def is_store_app(user_agent: str) -> bool:
    return APP_UA_TOKEN in (user_agent or "")


def tenant_host(slug: str) -> str:
    return f"{slug}.{TENANT_SUBDOMAIN_PREFIX}.{PARENT_DOMAIN}"


def tenant_url(slug: str) -> str:
    scheme = "https" if COOKIE_SECURE else "http"
    return f"{scheme}://{tenant_host(slug)}/"


def billing_url() -> str:
    scheme = "https" if COOKIE_SECURE else "http"
    return f"{scheme}://{PARENT_DOMAIN}/billing"


def logout_url() -> str:
    scheme = "https" if COOKIE_SECURE else "http"
    return f"{scheme}://{PARENT_DOMAIN}/logout"


def base_url() -> str:
    scheme = "https" if COOKIE_SECURE else "http"
    return f"{scheme}://{PARENT_DOMAIN}"


def cookie_domain() -> str:
    # A leading dot makes the cookie valid for the parent domain AND every
    # subdomain (that's what lets Traefik's ForwardAuth on *.app.<domain>
    # see the same session cookie that was set while on the parent domain).
    if PARENT_DOMAIN == "localhost":
        return ""  # browsers reject a Domain attribute on "localhost"; omit it for local dev
    return f".{PARENT_DOMAIN}"
