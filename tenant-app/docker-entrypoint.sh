#!/bin/bash
# `wait -n` below needs bash (Debian slim images, which this Dockerfile is
# based on, include /bin/bash by default -- unlike Alpine).
# Runs the background workers and Streamlit as children of this shell
# (PID 1), so `docker stop` (SIGTERM to PID 1) is caught here and forwarded
# to all of them -- deliberately NOT using `exec` for any of them, because
# that would replace this shell (and its trap) with just one process,
# leaving the others to be cleaned up only by Docker's SIGKILL after the
# stop grace period instead of shutting down cleanly.
set -e

python3 sync.py --loop &
SYNC_PID=$!

python3 agent_sync.py --loop &
AGENT_SYNC_PID=$!

python3 report_scheduler.py --loop &
REPORT_SCHEDULER_PID=$!

python3 -m streamlit run app.py \
    --server.address=0.0.0.0 \
    --server.port=8501 \
    --server.headless=true \
    --browser.gatherUsageStats=false &
STREAMLIT_PID=$!

cleanup() {
    kill "$SYNC_PID" 2>/dev/null || true
    kill "$AGENT_SYNC_PID" 2>/dev/null || true
    kill "$REPORT_SCHEDULER_PID" 2>/dev/null || true
    kill "$STREAMLIT_PID" 2>/dev/null || true
}
trap cleanup TERM INT

# If any process dies on its own, bring the container down instead of
# limping along with only part of the app working.
wait -n "$SYNC_PID" "$AGENT_SYNC_PID" "$REPORT_SCHEDULER_PID" "$STREAMLIT_PID"
EXIT_CODE=$?
cleanup
exit "$EXIT_CODE"
