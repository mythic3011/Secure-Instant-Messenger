#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEAM_ID="${1:-71}"
REPORT_PATH="${2:-}"
VIDEO_PATH="${3:-}"
TMP_ROOT=""

resolve_path() {
  local candidate="$1"
  if [[ -f "$ROOT_DIR/$candidate" ]]; then
    printf '%s\n' "$ROOT_DIR/$candidate"
  else
    printf '%s\n' "$candidate"
  fi
}

REPORT_ABS=""
VIDEO_ABS=""
REPORT_EXT=""
VIDEO_EXT=""

if [[ -n "$REPORT_PATH" ]]; then
  if [[ ! -f "$ROOT_DIR/$REPORT_PATH" && ! -f "$REPORT_PATH" ]]; then
    echo "[submission] missing report file: $REPORT_PATH" >&2
    exit 1
  fi
  REPORT_ABS="$(resolve_path "$REPORT_PATH")"
  REPORT_EXT="${REPORT_ABS##*.}"
fi

if [[ -n "$VIDEO_PATH" ]]; then
  if [[ ! -f "$ROOT_DIR/$VIDEO_PATH" && ! -f "$VIDEO_PATH" ]]; then
    echo "[submission] missing video file: $VIDEO_PATH" >&2
    exit 1
  fi
  VIDEO_ABS="$(resolve_path "$VIDEO_PATH")"
  VIDEO_EXT="${VIDEO_ABS##*.}"
fi

STAGING_DIR="$ROOT_DIR/submission/$TEAM_ID"
ZIP_PATH="$ROOT_DIR/submission/${TEAM_ID}.zip"
TMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/submission-${TEAM_ID}.XXXXXX")"
STAGING_DIR="$TMP_ROOT/$TEAM_ID"
CODE_DIR="$STAGING_DIR/code"

cleanup() {
  if [[ -n "$TMP_ROOT" && -d "$TMP_ROOT" ]]; then
    rm -rf "$TMP_ROOT"
  fi
}
trap cleanup EXIT

mkdir -p "$CODE_DIR"

# Submission-required code only.
for path in \
  client \
  server \
  shared \
  tests \
  docs/DEPLOY.md \
  install.sh \
  install.bat \
  pyproject.toml \
  uv.lock \
  docker-compose.yml \
  Dockerfile.server \
  .env.example
do
  if [[ -e "$ROOT_DIR/$path" ]]; then
    mkdir -p "$CODE_DIR/$(dirname "$path")"
    cp -R "$ROOT_DIR/$path" "$CODE_DIR/$path"
  fi
done

# Submission-required helper scripts only.
for path in \
  scripts/install/bootstrap-env.sh \
  scripts/install/bootstrap-env.bat \
  scripts/lib/bootstrap_env.py \
  scripts/launch/run-server.sh \
  scripts/launch/run-server.bat \
  scripts/launch/run-client.sh \
  scripts/launch/run-client.bat \
  scripts/run-server.sh \
  scripts/run-server.bat \
  scripts/run-client.sh \
  scripts/run-client.bat \
  scripts/docker-entrypoint.sh
do
  if [[ -e "$ROOT_DIR/$path" ]]; then
    mkdir -p "$CODE_DIR/$(dirname "$path")"
    cp -R "$ROOT_DIR/$path" "$CODE_DIR/$path"
  fi
done

find "$CODE_DIR" -type d \
  \( -name '__pycache__' -o -name '.pytest_cache' -o -name '.mypy_cache' \) \
  -exec rm -rf {} +
find "$CODE_DIR" -type f \
  \( -name '*.pyc' -o -name '*.pyo' -o -name '.DS_Store' \) \
  -delete

if [[ -n "$REPORT_ABS" ]]; then
  cp "$REPORT_ABS" "$STAGING_DIR/report.$REPORT_EXT"
fi

if [[ -n "$VIDEO_ABS" ]]; then
  cp "$VIDEO_ABS" "$STAGING_DIR/video.$VIDEO_EXT"
fi

(
  mkdir -p "$ROOT_DIR/submission"
  cd "$TMP_ROOT"
  rm -f "$ZIP_PATH"
  if command -v zip >/dev/null 2>&1; then
    zip -rq "$ZIP_PATH" "$TEAM_ID"
  else
    python3 - "$ZIP_PATH" "$TEAM_ID" <<'PY'
from __future__ import annotations

import sys
import zipfile
from pathlib import Path

zip_path = Path(sys.argv[1])
root = Path(sys.argv[2])

with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
    for path in sorted(root.rglob("*")):
        zf.write(path, path.as_posix())
PY
  fi
)

if [[ -n "$REPORT_ABS" || -n "$VIDEO_ABS" ]]; then
  echo "[submission] built $ZIP_PATH with optional report/video artifacts"
else
  echo "[submission] built $ZIP_PATH with code only"
fi
