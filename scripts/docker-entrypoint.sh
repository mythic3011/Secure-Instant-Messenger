#!/bin/sh
# docker-entrypoint.sh — auto-generate self-signed TLS cert if not present,
# then exec the server.
#
# Mount a volume at /app/certs to persist certs across restarts,
# or let this script generate a fresh one each boot (fine for dev).
#
# Environment variables honoured:
#   TLS_CERT_FILE  (default: /app/certs/server.crt)
#   TLS_KEY_FILE   (default: /app/certs/server.key)
#   TLS_CN         (default: localhost)

set -eu

CERT_FILE="${TLS_CERT_FILE:-/app/certs/server.crt}"
KEY_FILE="${TLS_KEY_FILE:-/app/certs/server.key}"
CN="${TLS_CN:-localhost}"
CERT_DIR="$(dirname "$CERT_FILE")"

# ── Generate cert if missing ────────────────────────────────────────────────
if [ ! -f "$CERT_FILE" ] || [ ! -f "$KEY_FILE" ]; then
    echo "[entrypoint] Generating self-signed TLS certificate (CN=$CN)..."
    mkdir -p "$CERT_DIR"
    openssl req -x509 \
        -newkey rsa:2048 \
        -keyout "$KEY_FILE" \
        -out    "$CERT_FILE" \
        -days   365 \
        -nodes \
        -subj   "/C=HK/O=COMP3334/CN=localhost" \
        -addext "subjectAltName=DNS:localhost,DNS:*.orb.local,DNS:server.comp3334-project.orb.local,IP:127.0.0.1" \
    2>/dev/null
    chmod 600 "$KEY_FILE"
    echo "[entrypoint] TLS cert ready at $CERT_FILE"
    FINGERPRINT=$(openssl x509 -in "$CERT_FILE" -outform DER | sha256sum | cut -d' ' -f1)
    echo "[entrypoint] Cert SHA256 pin: $FINGERPRINT"
    echo "[entrypoint] Use: --ca-cert $CERT_FILE  or  --pin-cert $FINGERPRINT"
else
    echo "[entrypoint] TLS cert already exists at $CERT_FILE"
    FINGERPRINT=$(openssl x509 -in "$CERT_FILE" -outform DER | sha256sum | cut -d' ' -f1)
    echo "[entrypoint] Cert SHA256 pin: $FINGERPRINT"
fi

# ── Exec server ─────────────────────────────────────────────────────────────
exec "$@"