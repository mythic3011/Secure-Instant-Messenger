#!/usr/bin/env bash

set -euo pipefail
IFS=$'\n\t'

# bootstrap-env.sh <mode>
# modes:
#   dev        — fill missing/weak secrets only (default)
#   production — regenerate all secrets and TLS certs

usage() {
    cat <<USAGE >&2
Usage: $0 [dev|production]
  dev        fill missing/weak secrets only (default)
  production force-regenerate secrets + TLS certificate
USAGE
    exit 1
}

MODE=${1:-dev}
[[ $MODE != dev && $MODE != production ]] && usage

readonly ENV_EXAMPLE=.env.example
readonly ENV_LOCAL=.env.local
readonly CERT_DIR=./certs
readonly DATA_DIR=./data
# Local DB path for dev (Docker uses /app/data/im.db via volume)
readonly DEV_DB_URL="sqlite+aiosqlite:///./data/im.db"
readonly CERT_FILE=$CERT_DIR/server.crt
readonly KEY_FILE=$CERT_DIR/server.key

# ensure example exists
[[ -f $ENV_EXAMPLE ]] || { echo "Error: $ENV_EXAMPLE not found" >&2; exit 1; }

# create local copy if missing
if [[ ! -f $ENV_LOCAL ]]; then
    cp $ENV_EXAMPLE $ENV_LOCAL
    echo "Created $ENV_LOCAL from $ENV_EXAMPLE"
fi

# generate 32 random bytes (64 hex chars)
gen_hex_64() {
    od -vN32 -An -tx1 /dev/urandom | tr -d ' \n'
}

# decide if an existing value is "weak"
is_weak() {
    local val=$1 var=$2 lower
    [[ -z $val ]] && return 0
    case $var in
        *SECRET*|*KEY*|*TOKEN*) ;;  # eligible for replacement
        *) return 1 ;;
    esac
    lower=$(printf '%s' "$val" | tr '[:upper:]' '[:lower:]')
    case $lower in
        replace_with_random_64_hex_chars|changeme|secret|password|default|example) return 0;;
    esac
    (( ${#val} < 64 )) && return 0
    return 1
}

# update (or append) variable in ENV_LOCAL
set_env() {
    local var=$1 val=$2
    if grep -q -E "^$var=" "$ENV_LOCAL"; then
        sed -i.bak "s|^$var=.*|$var=$val|" "$ENV_LOCAL"
    else
        printf '%s=%s\n' "$var" "$val" >> "$ENV_LOCAL"
    fi
    printf '  %s updated\n' "$var"
}

backup_env() {
    local stamp
    stamp=$(date -u +%Y%m%d-%H%M%S)
    cp "$ENV_LOCAL" "${ENV_LOCAL}.backup.$stamp"
    echo "  backed up to ${ENV_LOCAL}.backup.$stamp"
}

# header

echo
printf '═══════════════════════════════════════\n'
echo " Env bootstrap — $MODE mode"
printf '═══════════════════════════════════════\n'

[[ $MODE == production ]] && backup_env

# walk through example variables and conditionally set values
while IFS= read -r line || [ -n "$line" ]; do
    [[ $line =~ ^# ]] && continue
    [[ $line =~ ^([A-Z_][A-Z0-9_]*)= ]] || continue
    var=${BASH_REMATCH[1]}

    case $var in
        TOKEN_SECRET_KEY|TOTP_ENCRYPTION_KEY)
            current=$(grep "^$var=" "$ENV_LOCAL" 2>/dev/null | cut -d= -f2- || echo)
            if [[ $MODE == production ]] || is_weak "$current" "$var"; then
                set_env "$var" "$(gen_hex_64)"
            else
                echo "  $var already strong"
            fi
            ;;

        APP_ENV)
            if [[ $MODE == production ]]; then
                set_env APP_ENV production
            else
                current=$(grep "^APP_ENV=" "$ENV_LOCAL" 2>/dev/null | cut -d= -f2- || echo)
                if [[ -z $current ]]; then
                    set_env APP_ENV development
                else
                    echo "  APP_ENV=$current"
                fi
            fi
            ;;
    esac
done < "$ENV_EXAMPLE"

# TLS generation

echo
echo "── TLS certificates ──"

mkdir -p "$CERT_DIR"
mkdir -p "$DATA_DIR"

if [[ $MODE == production || ! -f $CERT_FILE || ! -f $KEY_FILE ]]; then
    if ! command -v openssl >/dev/null 2>&1; then
        echo "  ⚠ openssl not installed – skipping TLS cert generation"
    else
        echo "  generating self-signed TLS certificate..."
        openssl req -x509 -newkey rsa:2048 \
            -keyout "$KEY_FILE" -out "$CERT_FILE" \
            -days 365 -nodes \
            -subj "/C=HK/ST=HK/L=HongKong/O=LocalDev/OU=Dev/CN=localhost" \
            -addext "subjectAltName=DNS:localhost,DNS:*.orb.local,DNS:server.comp3334-project.orb.local,IP:127.0.0.1" \
            >/dev/null 2>&1
        chmod 600 "$KEY_FILE"
        echo "  TLS cert at $CERT_FILE"
        echo "  TLS key at $KEY_FILE"
    fi
else
    echo "  TLS cert/key already exist"
fi

# Override Docker DB path with local path in dev mode
if [[ $MODE != production ]]; then
    set_env DATABASE_URL "$DEV_DB_URL"
fi
set_env TLS_CERT_FILE "$CERT_FILE"
set_env TLS_KEY_FILE "$KEY_FILE"

echo
echo ".env.local + TLS ready ($MODE mode)"
