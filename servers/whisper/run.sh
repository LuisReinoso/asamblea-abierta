#!/usr/bin/env bash
# Run the whisper server. First start downloads the model (~1.6GB).
set -euo pipefail
cd "$(dirname "$0")"
source venv/bin/activate
exec uvicorn server:app --host 127.0.0.1 --port "${PORT:-8000}" "$@"
