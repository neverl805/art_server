#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$ROOT"
if [ -f .env ]; then
    set -a
    . ./.env
    set +a
fi
exec python -m uvicorn main:app --host "${MONITOR_HOST:-127.0.0.1}" --port "${MONITOR_PORT:-8000}" --no-access-log
