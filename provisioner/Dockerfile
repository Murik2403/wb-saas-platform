FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt ./
RUN python -m pip install --no-cache-dir 'pip>=26.2,<27' \
    && pip install --no-cache-dir -r requirements.txt

COPY app.py ./

EXPOSE 8001

# This is intentionally the only root process in the deployment: it holds
# the Docker socket and enforces the tenant-container allowlist below. Its
# Compose service drops all Linux capabilities and is reachable only through
# the internal provisioning network.
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8001"]
