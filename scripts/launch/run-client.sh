#!/bin/sh

set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)
cd "$PROJECT_ROOT"
export UV_CACHE_DIR="${UV_CACHE_DIR:-$PROJECT_ROOT/.uv-cache}"

CHECK_ONLY=0
SKIP_HEALTH_CHECK=${RUN_CLIENT_SKIP_HEALTHCHECK:-0}
SERVER_URL="https://localhost:8443"
SERVER_URL_SET=0
CA_CERT=

original_argc=$#
processed=0
while [ "$processed" -lt "$original_argc" ]; do
    arg=$1
    shift
    processed=$((processed + 1))

    case "$arg" in
        --check)
            CHECK_ONLY=1
            ;;
        --no-health-check)
            SKIP_HEALTH_CHECK=1
            ;;
        --ca-cert)
            if [ "$processed" -ge "$original_argc" ]; then
                printf '[run-client] --ca-cert requires a path\n' >&2
                exit 2
            fi
            CA_CERT=$1
            shift
            processed=$((processed + 1))
            set -- "$@" "$arg" "$CA_CERT"
            ;;
        http://*|https://*)
            if [ "$SERVER_URL_SET" -eq 0 ]; then
                SERVER_URL=$arg
                SERVER_URL_SET=1
            else
                set -- "$@" "$arg"
            fi
            ;;
        *)
            set -- "$@" "$arg"
            ;;
    esac
done

if ! command -v uv >/dev/null 2>&1; then
    printf '[run-client] missing prerequisite: uv\n' >&2
    printf '[run-client] install uv manually, then run ./install.sh --check\n' >&2
    exit 1
fi

if ! uv run python -c "import textual, httpx, aiosqlite, cryptography, pyotp" >/dev/null 2>&1; then
    printf '[run-client] environment not prepared or dependencies may be unsynced\n' >&2
    printf '[run-client] run ./install.sh --fix\n' >&2
    printf '[run-client] for read-only validation, run ./install.sh --check\n' >&2
    exit 1
fi

if [ "$CHECK_ONLY" -eq 1 ]; then
    uv run python -c "import client.main" >/dev/null
    printf '[run-client] check passed\n'
    exit 0
fi

if [ "$SKIP_HEALTH_CHECK" != "1" ]; then
    if [ -n "$CA_CERT" ]; then
        if ! uv run python scripts/lib/check_server_health.py --server "$SERVER_URL" --ca-cert "$CA_CERT" >/dev/null; then
            printf '[run-client] server health check failed\n' >&2
            printf '[run-client] rerun with --no-health-check to bypass preflight\n' >&2
            exit 1
        fi
    else
        if ! uv run python scripts/lib/check_server_health.py --server "$SERVER_URL" >/dev/null; then
            printf '[run-client] server health check failed\n' >&2
            printf '[run-client] if using a local cert, pass --ca-cert ./certs/server.crt\n' >&2
            printf '[run-client] rerun with --no-health-check to bypass preflight\n' >&2
            exit 1
        fi
    fi
fi

exec uv run python -m client.main --server "$SERVER_URL" "$@"
