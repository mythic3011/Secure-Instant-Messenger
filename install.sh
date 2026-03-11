#!/usr/bin/env bash
# install.sh — One-shot setup for COMP3334 Secure IM
# Usage: ./install.sh [--dev]
#
# Steps:
#   1. Check required tools (uv, openssl, python3)
#   2. Install Python dependencies
#   3. Bootstrap .env.local via scripts/bootstrap-env.sh
#   4. Generate self-signed TLS cert (if not already present)
#   5. Initialise server database schema
#
# Flags:
#   --dev   Also install dev dependencies (pytest, ruff, mypy)
#
# Re-running this script is safe: each step is idempotent.

set -euo pipefail

# ── Colours ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()    { echo -e "${GREEN}[install]${NC} $*"; }
warning() { echo -e "${YELLOW}[install]${NC} $*"; }
error()   { echo -e "${RED}[install]${NC} $*" >&2; exit 1; }

# ── Args ─────────────────────────────────────────────────────────────────────
DEV=0
for arg in "$@"; do
  case "$arg" in
    --dev) DEV=1 ;;
    *) error "Unknown argument: $arg" ;;
  esac
done

# ── Step 0: tool checks ───────────────────────────────────────────────────────
info "Checking required tools…"
command -v python3  >/dev/null 2>&1 || error "python3 not found — install Python 3.12+"
command -v openssl  >/dev/null 2>&1 || error "openssl not found — see DEPLOY.md §1"
command -v uv       >/dev/null 2>&1 || {
  warning "uv not found — installing via astral.sh…"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  # Make uv available in current shell session
  export PATH="$HOME/.local/bin:$PATH"
  command -v uv >/dev/null 2>&1 || error "uv install failed — add ~/.local/bin to PATH and retry"
}
info "All tools present ✓"

# ── Step 1: Python dependencies ──────────────────────────────────────────────
info "Installing Python dependencies…"
if [ "$DEV" -eq 1 ]; then
  uv sync --extra dev
  info "Installed with dev extras (pytest, ruff, mypy) ✓"
else
  uv sync
  info "Installed production dependencies ✓"
fi

# ── Step 2: Bootstrap .env.local ─────────────────────────────────────────────
info "Configuring environment…"
BOOTSTRAP="scripts/bootstrap-env.sh"
if [ ! -f "$BOOTSTRAP" ]; then
  error "$BOOTSTRAP not found — is this the project root?"
fi
chmod +x "$BOOTSTRAP"
"$BOOTSTRAP"   # exits non-zero and prints message if .env.local already exists → caught by set -e
# bootstrap-env.sh already prints "Wrote secrets to .env.local" on success

# ── Step 3: TLS certificate ───────────────────────────────────────────────────
info "Checking TLS certificate…"
CERT_DIR="certs"
CERT_FILE="$CERT_DIR/server.crt"
KEY_FILE="$CERT_DIR/server.key"

if [ -f "$CERT_FILE" ] && [ -f "$KEY_FILE" ]; then
  warning "TLS cert already exists at $CERT_FILE — skipping generation"
else
  mkdir -p "$CERT_DIR"
  openssl req -x509 -newkey rsa:4096 \
    -keyout "$KEY_FILE" \
    -out    "$CERT_FILE" \
    -days 365 -nodes \
    -subj "/CN=localhost" \
    2>/dev/null
  chmod 600 "$KEY_FILE"   # private key: owner read/write only
  info "Generated self-signed TLS cert at $CERT_FILE ✓"
fi

# ── Step 4: Database init ────────────────────────────────────────────────────
info "Initialising database schema…"
# Run the migration runner embedded in the server (reads DATABASE_URL from .env.local)
ENV_FILE=".env.local" uv run python3 -c "
import asyncio, server.core.database as db
asyncio.run(db.init_db())
print('Database initialised')
asyncio.run(db.close_db())
"
info "Database ready ✓"

# ── Done ─────────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}  Setup complete!${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo "  Start server:  uv run uvicorn server.main:app --host 0.0.0.0 --port 8443"
echo "  Start client:  uv run python -m client.main --server https://localhost:8443"
echo "  Run tests:     uv run pytest -v          (requires --dev)"
echo ""
echo "  See DEPLOY.md for full usage instructions."
echo ""