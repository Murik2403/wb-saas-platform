FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.lock ./
RUN python -m pip install --no-cache-dir --require-hashes -r requirements.lock

COPY app.py backup_crypto.py decrypt_backup.py demo_refresh.py ./
RUN groupadd --gid 10001 appgroup \
    && useradd --uid 10001 --gid appgroup --create-home --home-dir /app appuser \
    && chown -R appuser:appgroup /app

EXPOSE 8001

# This is intentionally the only root process in the deployment: it holds
# the Docker socket and enforces the tenant-container allowlist below. Its
# Compose service drops all Linux capabilities and is reachable only through
# the internal provisioning network.
USER appuser
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8001"]
