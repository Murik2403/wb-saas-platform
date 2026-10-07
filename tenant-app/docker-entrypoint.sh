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

python3 background_worker.py &
BACKGROUND_PID=$!

python3 -m streamlit run app.py \
    --server.address=0.0.0.0 \
    --server.port=8501 \
    --server.headless=true \
    --browser.gatherUsageStats=false &
STREAMLIT_PID=$!

cleanup() {
    kill "$BACKGROUND_PID" 2>/dev/null || true
    kill "$STREAMLIT_PID" 2>/dev/null || true
}
trap cleanup TERM INT EXIT

# If any process dies on its own, bring the container down instead of
# limping along with only part of the app working.
wait -n "$BACKGROUND_PID" "$STREAMLIT_PID"
EXIT_CODE=$?
cleanup
exit "$EXIT_CODE"
