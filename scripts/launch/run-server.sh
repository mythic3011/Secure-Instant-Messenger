#!/usr/bin/env bash

set -euo pipefail
IFS=$'\n\t'

SCRIPT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"
export UV_CACHE_DIR="${UV_CACHE_DIR:-$PROJECT_ROOT/.uv-cache}"

CHECK_ONLY=0
if [[ "${1:-}" == "--check" ]]; then
    CHECK_ONLY=1
fi

if ! command -v uv >/dev/null 2>&1; then
    echo "[run-server] uv not found. Install uv and run 'uv sync' first." >&2
    exit 1
fi

if [[ ! -f .env.local ]]; then
    echo "[run-server] .env.local not found. Run './install.sh --fix' first." >&2
    exit 1
fi

if ! uv run python -c "import fastapi, uvicorn, aiosqlite, cryptography, argon2, pyotp, httpx" >/dev/null 2>&1; then
    echo "[run-server] Missing Python dependencies. Run 'uv sync' first." >&2
    exit 1
fi

if [[ $CHECK_ONLY -eq 1 ]]; then
    uv run python -c "from server.main import app; print(app.title)" >/dev/null
    echo "[run-server] check passed"
    exit 0
fi

exec uv run python -m server.main
