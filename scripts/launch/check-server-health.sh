#!/bin/sh

set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)

cd "$PROJECT_ROOT"
export UV_CACHE_DIR="${UV_CACHE_DIR:-$PROJECT_ROOT/.uv-cache}"

exec uv run python scripts/lib/check_server_health.py "$@"
