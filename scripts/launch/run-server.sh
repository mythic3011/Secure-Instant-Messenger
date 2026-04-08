#!/bin/sh

set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)
cd "$PROJECT_ROOT"
export UV_CACHE_DIR="${UV_CACHE_DIR:-$PROJECT_ROOT/.uv-cache}"

CHECK_ONLY=0
if [ "${1:-}" = "--check" ]; then
    CHECK_ONLY=1
fi

if ! command -v uv >/dev/null 2>&1; then
    printf '[run-server] uv not found. Install uv and run ./install.sh --fix first.\n' >&2
    exit 1
fi

if [ ! -f .env.local ]; then
    printf '[run-server] .env.local not found. Run ./install.sh --fix first.\n' >&2
    exit 1
fi

if [ ! -f certs/server.crt ]; then
    printf '[run-server] HTTPS readiness missing: certs/server.crt not found. Run ./install.sh --fix first.\n' >&2
    exit 1
fi

if [ ! -f certs/server.key ]; then
    printf '[run-server] HTTPS readiness missing: certs/server.key not found. Run ./install.sh --fix first.\n' >&2
    exit 1
fi

if ! uv run python -c "import fastapi, uvicorn, aiosqlite, cryptography, argon2, pyotp, httpx" >/dev/null 2>&1; then
    printf '[run-server] environment not prepared or dependencies may be unsynced\n' >&2
    printf '[run-server] run ./install.sh --fix\n' >&2
    printf '[run-server] for read-only validation, run ./install.sh --check\n' >&2
    exit 1
fi

if [ "$CHECK_ONLY" -eq 1 ]; then
    uv run python -c "from server.main import app; print(app.title)" >/dev/null
    printf '[run-server] check passed\n'
    exit 0
fi

exec uv run python -m server.main
