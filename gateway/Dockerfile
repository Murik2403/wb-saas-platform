# The gateway: registration/login/billing site + tenant provisioning. Runs
# once per deployment (not once per client, unlike ../tenant-app/Dockerfile).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt ./
RUN python -m pip install --no-cache-dir 'pip>=26.2,<27' && pip install --no-cache-dir -r requirements.txt

COPY . .
RUN groupadd --gid 10001 appgroup \
    && useradd --uid 10001 --gid appgroup --create-home --home-dir /app appuser \
    && mkdir -p /app/data \
    && chmod +x docker-entrypoint.sh \
    && chown -R appuser:appgroup /app

HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=5 \
    CMD python3 -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3).status==200 else 1)"

EXPOSE 8000

USER appuser

# Tenant lifecycle requests go to the private provisioner service; this image
# deliberately never receives the host Docker socket.
ENTRYPOINT ["./docker-entrypoint.sh"]
