#!/usr/bin/env bash
set -euo pipefail

# ./bootstrap-env.sh [dev|production]
# dev        — generate missing/weak secrets only, never overwrite strong values
# production — force-regenerate secrets + TLS certs regardless of current value

MODE="${1:-dev}"
[[ "$MODE" != "dev" && "$MODE" != "production" ]] && {
    echo "Usage: $0 [dev|production]"
    echo "  dev        — fill missing/weak secrets only"
    echo "  production — force-regenerate secrets and TLS cert"
    exit 1
}

ENV_EXAMPLE=".env.example"
ENV_LOCAL=".env.local"

[ ! -f "$ENV_EXAMPLE" ] && { echo "Error: $ENV_EXAMPLE not found"; exit 1; }

if [ ! -f "$ENV_LOCAL" ]; then
    cp "$ENV_EXAMPLE" "$ENV_LOCAL"
    echo "Created $ENV_LOCAL from $ENV_EXAMPLE"
fi

gen_hex_64 () {
  od -vN 32 -An -tx1 /dev/urandom | tr -d ' \n'
}

is_weak () {
  local val="$1"
  local var="$2"

  [[ -z "$val" ]] && return 0
  [[ "$var" =~ (SECRET|KEY|TOKEN)$ ]] || return 1

  local lower_val
  lower_val=$(echo "$val" | tr '[:upper:]' '[:lower:]')
  case "$lower_val" in
    "replace_with_random_64_hex_chars"|"changeme"|"secret"|"password"|"default"|"example")
      return 0 ;;
  esac

  [[ ${#val} -lt 64 ]] && return 0
  return 1
}

set_env () {
  local var="$1" val="$2"
  python3 - <<PYEOF
import re
var = ${var@Q}
val = ${val@Q}
path = ${ENV_LOCAL@Q}
with open(path, "r") as f:
    content = f.read()
pattern = r'^' + re.escape(var) + r'=.*$'
replacement = var + '=' + val
if re.search(pattern, content, flags=re.MULTILINE):
    content = re.sub(pattern, replacement, content, flags=re.MULTILINE)
else:
    content += '\n' + replacement + '\n'
with open(path, "w") as f:
    f.write(content)
PYEOF
  echo "  ✔ ${var} updated"
}

echo ""
echo "═══════════════════════════════════════"
echo " Env bootstrap — ${MODE} mode"
echo "═══════════════════════════════════════"

if [[ "$MODE" == "production" ]]; then
  BACKUP_DATE=$(date -u +%Y%m%d-%H%M%S 2>/dev/null || date +%Y%m%d-%H%M%S)
  cp "$ENV_LOCAL" "${ENV_LOCAL}.backup.${BACKUP_DATE}"
  echo "  Existing ${ENV_LOCAL} backed up to ${ENV_LOCAL}.backup.${BACKUP_DATE}"
fi

while IFS= read -r line || [ -n "$line" ]; do
  [[ "$line" =~ ^#.*$|^$ ]] && continue
  [[ "$line" =~ ^([A-Z_][A-Z0-9_]*)= ]] || continue
  var="${BASH_REMATCH[1]}"

  case "$var" in
    TOKEN_SECRET_KEY|TOTP_ENCRYPTION_KEY)
      current=$(grep "^${var}=" "$ENV_LOCAL" 2>/dev/null | cut -d'=' -f2- || echo "")
      if [[ "$MODE" == "production" ]] || is_weak "$current" "$var"; then
        new=$(gen_hex_64)
        set_env "$var" "$new"
      else
        echo "  ✔ ${var} already strong, keep existing"
      fi
      ;;
    APP_ENV)
      if [[ "$MODE" == "production" ]]; then
        set_env "APP_ENV" "production"
      else
        current=$(grep "^APP_ENV=" "$ENV_LOCAL" 2>/dev/null | cut -d'=' -f2- || echo "")
        if [[ -z "$current" ]]; then
          set_env "APP_ENV" "development"
        else
          echo "  ✔ APP_ENV=${current}"
        fi
      fi
      ;;
    *)
      :
      ;;
  esac
done < "$ENV_EXAMPLE"

echo ""
echo "── TLS certificates ──"

CERT_DIR="/app/certs"
CERT_FILE="${CERT_DIR}/server.crt"
KEY_FILE="${CERT_DIR}/server.key"

mkdir -p "$CERT_DIR"

if [[ "$MODE" == "production" || ! -f "$CERT_FILE" || ! -f "$KEY_FILE" ]]; then
  if ! command -v openssl >/dev/null 2>&1; then
    echo "  ⚠ openssl not found — skipping TLS cert generation"
  else
    echo "  Generating self-signed TLS certificate..."
    # 365 days self-signed, non-interactive, CN=localhost by default
    openssl req -x509 -newkey rsa:2048 \
      -keyout "$KEY_FILE" \
      -out "$CERT_FILE" \
      -days 365 \
      -nodes \
      -subj "/C=HK/ST=HK/L=HongKong/O=LocalDev/OU=Dev/CN=localhost" >/dev/null 2>&1
    chmod 600 "$KEY_FILE"
    echo "  ✔ TLS cert generated at $CERT_FILE"
    echo "  ✔ TLS key  generated at $KEY_FILE"
  fi
else
  echo "  ✔ TLS cert/key already exist, keep existing"
fi

set_env "TLS_CERT_FILE" "$CERT_FILE"
set_env "TLS_KEY_FILE" "$KEY_FILE"

echo ""
echo "✔ .env.local + TLS ready (${MODE} mode)"
