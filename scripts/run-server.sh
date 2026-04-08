#!/bin/sh

# Legacy compatibility shim.
# Canonical path: scripts/launch/run-server.sh
# No logic should be added here.

set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec "$SCRIPT_DIR/launch/run-server.sh" "$@"
