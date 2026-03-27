#!/usr/bin/env bash

set -euo pipefail
IFS=$'\n\t'
export UV_CACHE_DIR="${UV_CACHE_DIR:-$PWD/.uv-cache}"

CHECK_ONLY=0
SERVER_URL="https://localhost:8443"

if [[ "${1:-}" == "--check" ]]; then
    CHECK_ONLY=1
elif [[ -n "${1:-}" ]]; then
    SERVER_URL="$1"
fi

if ! command -v uv >/dev/null 2>&1; then
    echo "[run-client] uv not found. Install uv and run 'uv sync' first." >&2
    exit 1
fi

if ! uv run python -c "import textual, httpx, aiosqlite, cryptography, pyotp" >/dev/null 2>&1; then
    echo "[run-client] Missing Python dependencies. Run 'uv sync' first." >&2
    exit 1
fi

if [[ $CHECK_ONLY -eq 1 ]]; then
    uv run python -m client.main --help >/dev/null
    echo "[run-client] check passed"
    exit 0
fi

exec uv run python -m client.main --server "$SERVER_URL"
