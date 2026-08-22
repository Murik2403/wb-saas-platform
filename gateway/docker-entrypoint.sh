#!/bin/bash
# Unlike tenant-app/docker-entrypoint.sh (where sync + Streamlit are both
# essential and either dying takes the whole container down), the Telegram
# support relay is a nice-to-have sidecar: registration/login/billing must
# keep working even if Telegram is unreachable, unconfigured, or the bot
# process itself crashes. So it's just backgrounded, never bringing down
# uvicorn -- and telegram_bot.py's own run() already never raises (see its
# docstring) and returns immediately when unconfigured.
set -e

python3 telegram_bot.py &

# exec (not background+wait) makes uvicorn PID 1, so it receives SIGTERM
# directly from `docker stop` with no trap/forwarding needed -- same
# reason gateway's old Dockerfile CMD ran uvicorn directly before this
# script existed.
exec uvicorn app:app --host 0.0.0.0 --port 8000
