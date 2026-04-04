#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

# Activate whichever venv exists (.venv for dev, venv for Pi/production)
if [ -d ".venv" ]; then
    source .venv/bin/activate
else
    source venv/bin/activate
fi

export MEDIA_INGEST_ROOT="${MEDIA_INGEST_ROOT:-$PWD}"

# Ensure required directories exist
mkdir -p data/media/audio data/media/video data/meta/thumbs data/meta/subs data/tmp data/db

python -m app.worker &
WORKER_PID=$!

caddy run --config Caddyfile &
CADDY_PID=$!

trap "kill $WORKER_PID $CADDY_PID 2>/dev/null" EXIT

uvicorn app.main:app --host "${APP_HOST:-127.0.0.1}" --port "${APP_PORT:-8080}"
