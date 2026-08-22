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
TENANT_IMAGE = os.environ.get("WB_SAAS_TENANT_IMAGE", "wb-dashboard-tenant:latest")

# Docker network shared by the gateway, Traefik, and every tenant container.
DOCKER_NETWORK = os.environ.get("WB_SAAS_DOCKER_NETWORK", "wbsaas_net")

# Internal port the tenant Streamlit app listens on inside its container.
TENANT_INTERNAL_PORT = int(os.environ.get("WB_SAAS_TENANT_INTERNAL_PORT", "8501"))

# Per-tenant resource caps, so one client's runaway sync/report can't starve
# every other client sharing the same host. Tune to your VPS size.
TENANT_MEM_LIMIT = os.environ.get("WB_SAAS_TENANT_MEM_LIMIT", "384m")
TENANT_CPU_QUOTA = float(os.environ.get("WB_SAAS_TENANT_CPU_QUOTA", "1.0"))  # in CPU cores

# Idle-sleep auto stop & wake up configuration (for hosting hundreds of tenants)
IDLE_AUTO_STOP_ENABLED = os.environ.get("WB_SAAS_IDLE_AUTO_STOP_ENABLED", "1") == "1"
TENANT_IDLE_TIMEOUT_MINUTES = int(os.environ.get("WB_SAAS_TENANT_IDLE_TIMEOUT_MINUTES", "30"))

# Name of the Traefik ForwardAuth middleware (defined once, via labels on
# the gateway service in docker-compose.yml) that every tenant router
# references. Must match exactly what's in docker-compose.yml.
FORWARD_AUTH_MIDDLEWARE = os.environ.get("WB_SAAS_FORWARD_AUTH_MIDDLEWARE", "wb-saas-auth@file")

# Name of the Traefik certresolver configured in traefik/traefik.yml.
TLS_CERT_RESOLVER = os.environ.get("WB_SAAS_TLS_CERT_RESOLVER", "letsencrypt")

PROVISION_HEALTH_TIMEOUT_SECONDS = int(os.environ.get("WB_SAAS_PROVISION_TIMEOUT", "180"))

# Set to "1" in production behind HTTPS; cookies are only sent over TLS then.
COOKIE_SECURE = os.environ.get("WB_SAAS_COOKIE_SECURE", "0") == "1"

SESSION_COOKIE_NAME = "wb_saas_session"

# Comma-separated list of admin email addresses with access to /admin
ADMIN_EMAILS = [
    email.strip().lower()
    for email in os.environ.get("WB_SAAS_ADMIN_EMAILS", "").split(",")
    if email.strip()
]

# --------------------------------------------------------------------------
# Billing (see logic/billing.py + yookassa_client.py)
# --------------------------------------------------------------------------

TRIAL_DAYS = int(os.environ.get("WB_SAAS_TRIAL_DAYS", "14"))
BILLING_PERIOD_DAYS = int(os.environ.get("WB_SAAS_BILLING_PERIOD_DAYS", "30"))
SUBSCRIPTION_PRICE_RUB = float(os.environ.get("WB_SAAS_SUBSCRIPTION_PRICE_RUB", "2990"))
# How long a client keeps access after a failed/missing payment before
# being locked out and their container stopped. Long enough that a
# temporarily-declined card isn't an instant outage, short enough that it's
# not effectively a second free trial.
GRACE_PERIOD_DAYS = int(os.environ.get("WB_SAAS_GRACE_PERIOD_DAYS", "5"))

YOOKASSA_SHOP_ID = os.environ.get("WB_SAAS_YOOKASSA_SHOP_ID", "")
YOOKASSA_SECRET_KEY = os.environ.get("WB_SAAS_YOOKASSA_SECRET_KEY", "")

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

SMTP_HOST = os.environ.get("WB_SAAS_SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("WB_SAAS_SMTP_PORT", "587"))
SMTP_USER = os.environ.get("WB_SAAS_SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("WB_SAAS_SMTP_PASSWORD", "")
# "1" for STARTTLS on a plaintext-then-upgrade port (587, the common case),
# "0" only for a pre-configured implicit-TLS/local relay setup.
SMTP_USE_STARTTLS = os.environ.get("WB_SAAS_SMTP_USE_STARTTLS", "1") == "1"
SMTP_FROM_EMAIL = os.environ.get("WB_SAAS_SMTP_FROM_EMAIL", "no-reply@" + PARENT_DOMAIN)


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
