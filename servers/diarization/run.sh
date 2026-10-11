#!/usr/bin/env bash
# Run the diarization server. Requires HUGGINGFACE_TOKEN in env.
set -euo pipefail
cd "$(dirname "$0")"
source venv/bin/activate
exec uvicorn server:app --host 127.0.0.1 --port "${PORT:-8001}" "$@"
