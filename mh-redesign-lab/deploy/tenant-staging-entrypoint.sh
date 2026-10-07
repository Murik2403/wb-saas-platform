#!/usr/bin/env bash
set -euo pipefail

cd /app/tenant-app

uvicorn api_server:app --host 127.0.0.1 --port 8001 &
API_PID=$!

python3 background_worker.py &
BACKGROUND_PID=$!

cleanup() {
    kill "$BACKGROUND_PID" 2>/dev/null || true
    kill "$API_PID" 2>/dev/null || true
    nginx -s quit 2>/dev/null || true
}
trap cleanup TERM INT EXIT

nginx -g 'daemon off;' &
NGINX_PID=$!

wait -n "$BACKGROUND_PID" "$API_PID" "$NGINX_PID"
