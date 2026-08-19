#!/bin/bash
# `wait -n` below needs bash (Debian slim images, which this Dockerfile is
# based on, include /bin/bash by default -- unlike Alpine).
# Runs both the background WB-API sync worker and Streamlit as children of
# this shell (PID 1), so `docker stop` (SIGTERM to PID 1) is caught here and
# forwarded to both -- deliberately NOT using `exec` for either, because
# that would replace this shell (and its trap) with just one of the two
# processes, leaving the other to be cleaned up only by Docker's SIGKILL
# after the stop grace period instead of shutting down cleanly.
set -e

python3 sync.py --loop &
SYNC_PID=$!

python3 -m streamlit run app.py \
    --server.address=0.0.0.0 \
    --server.port=8501 \
    --server.headless=true \
    --browser.gatherUsageStats=false &
STREAMLIT_PID=$!

cleanup() {
    kill "$SYNC_PID" 2>/dev/null || true
    kill "$STREAMLIT_PID" 2>/dev/null || true
}
trap cleanup TERM INT

# If either process dies on its own, bring the container down instead of
# limping along with only half the app working.
wait -n "$SYNC_PID" "$STREAMLIT_PID"
EXIT_CODE=$?
cleanup
exit "$EXIT_CODE"
