#!/usr/bin/env bash
set -euo pipefail

cd /app/tenant-app

uvicorn api_server:app --host 127.0.0.1 --port 8001 &
API_PID=$!

python3 sync.py --loop &
SYNC_PID=$!

python3 agent_sync.py --loop &
AGENT_SYNC_PID=$!

python3 report_scheduler.py --loop &
REPORT_SCHEDULER_PID=$!

cleanup() {
    kill "$API_PID" 2>/dev/null || true
    kill "$SYNC_PID" 2>/dev/null || true
    kill "$AGENT_SYNC_PID" 2>/dev/null || true
    kill "$REPORT_SCHEDULER_PID" 2>/dev/null || true
    nginx -s quit 2>/dev/null || true
}
trap cleanup TERM INT EXIT

nginx -g 'daemon off;' &
NGINX_PID=$!

wait -n "$API_PID" "$SYNC_PID" "$AGENT_SYNC_PID" "$NGINX_PID" "$REPORT_SCHEDULER_PID"
