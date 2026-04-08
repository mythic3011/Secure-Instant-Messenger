#!/bin/sh

# Legacy compatibility shim.
# Canonical path: scripts/launch/run-client.sh
# No logic should be added here.

set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec "$SCRIPT_DIR/launch/run-client.sh" "$@"
