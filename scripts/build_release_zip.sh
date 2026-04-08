#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUTPUT_DIR="$ROOT_DIR/release"
ZIP_PATH="$OUTPUT_DIR/project-release.zip"

required_paths=(
  README.md
  docs/ARCHITECTURE.md
  docs/BUG_REPORT.md
  docs/DEPLOY.md
  docs/Project.pdf
  docs/TASKS.md
  docs/Tutorial
  client
  server
  shared
  tests
  scripts
  pyproject.toml
  uv.lock
  docker-compose.yml
  Dockerfile.server
)

for path in "${required_paths[@]}"; do
  if [[ ! -e "$ROOT_DIR/$path" ]]; then
    echo "[release] missing required path: $path" >&2
    exit 1
  fi
done

mkdir -p "$OUTPUT_DIR"
rm -f "$ZIP_PATH"

(
  cd "$ROOT_DIR"
  git archive --format=zip --output="$ZIP_PATH" HEAD \
    --prefix=comp3334_project/ \
    client \
    server \
    shared \
    tests \
    scripts \
    docs/ARCHITECTURE.md \
    docs/BUG_REPORT.md \
    docs/DEPLOY.md \
    docs/Project.pdf \
    docs/TASKS.md \
    docs/Tutorial \
    README.md \
    pyproject.toml \
    uv.lock \
    docker-compose.yml \
    Dockerfile.server
)

echo "[release] built $ZIP_PATH"
