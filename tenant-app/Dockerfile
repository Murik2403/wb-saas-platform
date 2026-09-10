# Image for a single tenant's WB Dashboard instance. One container per
# client (see gateway/README / ../DEPLOY.md for why); this Dockerfile does
# not know or care about multi-tenancy -- it's exactly the existing app,
# containerized.
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
    && chmod +x docker-entrypoint.sh \
    && mkdir -p /app/data \
    && chown -R appuser:appgroup /app

# Streamlit's own liveness endpoint; Traefik/Docker use this to know the
# container is actually ready to serve, not just "process started".
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
    CMD python3 -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8501/_stcore/health', timeout=3).status==200 else 1)"

EXPOSE 8501

# One tenant's data must survive `docker rm`+re-`run` (image upgrades) --
# only the named volume mounted here at deploy time is persistent.
VOLUME ["/app/data"]

USER appuser

ENTRYPOINT ["./docker-entrypoint.sh"]
