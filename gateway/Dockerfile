# The gateway: registration/login/billing site + tenant provisioning. Runs
# once per deployment (not once per client, unlike ../tenant-app/Dockerfile).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN mkdir -p /app/data && chmod +x docker-entrypoint.sh

HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=5 \
    CMD python3 -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3).status==200 else 1)"

EXPOSE 8000

# Needs the host's Docker socket mounted (see docker-compose.yml) to create
# and manage tenant containers -- that's provisioning.py's job.
ENTRYPOINT ["./docker-entrypoint.sh"]
