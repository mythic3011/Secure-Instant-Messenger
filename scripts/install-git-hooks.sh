#!/usr/bin/env bash
set -euo pipefail

HOOKS_DIR="scripts/git-hooks"
PRE_COMMIT="$HOOKS_DIR/pre-commit"

if [ ! -f "$PRE_COMMIT" ]; then
  echo "Pre-commit hook not found at $PRE_COMMIT" >&2
  exit 2
fi

chmod +x "$PRE_COMMIT"
git config core.hooksPath "$HOOKS_DIR"

echo "Installed git hooks (core.hooksPath=$HOOKS_DIR)"
echo "Pre-commit will run 'black .' before each commit."

exit 0
