#!/bin/sh

set -eu

MODE=fix
VERBOSE=0

usage() {
    cat >&2 <<'USAGE'
Usage: scripts/install/bootstrap-env.sh [--check|--fix] [--verbose]
USAGE
    exit 2
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --check)
            MODE=check
            ;;
        --fix)
            MODE=fix
            ;;
        --verbose)
            VERBOSE=1
            ;;
        *)
            usage
            ;;
    esac
    shift
done

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)
PYPROJECT="$PROJECT_ROOT/pyproject.toml"
ENV_EXAMPLE="$PROJECT_ROOT/.env.example"
ENV_LOCAL="$PROJECT_ROOT/.env.local"
CERT_DIR="$PROJECT_ROOT/certs"
CERT_FILE="$CERT_DIR/server.crt"
KEY_FILE="$CERT_DIR/server.key"

say() {
    printf '%s\n' "$1"
}

verbose() {
    if [ "$VERBOSE" -eq 1 ]; then
        printf '%s\n' "$1"
    fi
}

if [ ! -f "$PYPROJECT" ]; then
    printf '[bootstrap] project metadata missing: %s\n' "$PYPROJECT" >&2
    exit 1
fi

if [ "$MODE" = "check" ]; then
    say "[bootstrap] project root: $PROJECT_ROOT"
    say "[bootstrap] check passed"
    exit 0
fi

cd "$PROJECT_ROOT"
export UV_CACHE_DIR="${UV_CACHE_DIR:-$PROJECT_ROOT/.uv-cache}"

if [ -f "$ENV_EXAMPLE" ] && [ ! -f "$ENV_LOCAL" ]; then
    cp "$ENV_EXAMPLE" "$ENV_LOCAL"
    verbose "[bootstrap] created .env.local from .env.example"
fi

mkdir -p "$CERT_DIR"
if [ ! -f "$CERT_FILE" ] || [ ! -f "$KEY_FILE" ]; then
    verbose "[bootstrap] generating local TLS certificate"
    openssl req -x509 -newkey rsa:2048 \
        -keyout "$KEY_FILE" \
        -out "$CERT_FILE" \
        -days 365 -nodes \
        -subj "/C=HK/ST=HK/L=HongKong/O=LocalDev/OU=Dev/CN=localhost" \
        -addext "subjectAltName=DNS:localhost,DNS:*.orb.local,DNS:server.comp3334-project.orb.local,IP:127.0.0.1" \
        >/dev/null 2>&1
    chmod 600 "$KEY_FILE"
fi

uv sync
say "[bootstrap] project bootstrap complete"
