#!/usr/bin/env bash
set -euo pipefail

ENV_EXAMPLE=".env.example"
ENV_LOCAL=".env.local"

if [ ! -f "$ENV_EXAMPLE" ]; then
  echo "$ENV_EXAMPLE not found" >&2
  exit 1
fi

if [ -f "$ENV_LOCAL" ]; then
  echo "$ENV_LOCAL already exists, not overwriting" >&2
  exit 1
fi

cp "$ENV_EXAMPLE" "$ENV_LOCAL"

gen_hex_64 () {
  # 32 bytes = 64 hex chars
  od -vN 32 -An -tx1 /dev/urandom | tr -d ' \n'
}

TOKEN_SECRET="$(gen_hex_64)"
TOTP_KEY="$(gen_hex_64)"

tmp_file="${ENV_LOCAL}.tmp"
> "$tmp_file"

while IFS= read -r line || [ -n "$line" ]; do
  case "$line" in
    TOKEN_SECRET_KEY=REPLACE_WITH_RANDOM_64_HEX_CHARS*)
      echo "TOKEN_SECRET_KEY=${TOKEN_SECRET}" >> "$tmp_file"
      ;;
    TOTP_ENCRYPTION_KEY=REPLACE_WITH_RANDOM_64_HEX_CHARS*)
      echo "TOTP_ENCRYPTION_KEY=${TOTP_KEY}" >> "$tmp_file"
      ;;
    *)
      echo "$line" >> "$tmp_file"
      ;;
  esac
done < "$ENV_LOCAL"

mv "$tmp_file" "$ENV_LOCAL"

echo "Wrote secrets to $ENV_LOCAL"
