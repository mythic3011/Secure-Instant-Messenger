#!/usr/bin/env bash
# test-deploy.sh — local deploy smoke test
# Mirrors what the GitHub Actions CI workflow does.
# Usage: ./scripts/test-deploy.sh [--no-docker]

set -euo pipefail
IFS=$'\n\t'

NO_DOCKER=0
for arg in "$@"; do
  [[ $arg == --no-docker ]] && NO_DOCKER=1
done

PASS=0
FAIL=0

ok()   { echo "  [PASS] $*"; (( PASS++ )) || true; }
fail() { echo "  [FAIL] $*"; (( FAIL++ )) || true; }
step() { echo; echo "── $* ──"; }

# ── 1. Python & uv ───────────────────────────────────────────────────────────
step "Prerequisites"

if command -v python3 >/dev/null 2>&1; then
  PY=$(python3 --version)
  ok "python3 found: $PY"
else
  fail "python3 not found"
fi

if command -v uv >/dev/null 2>&1; then
  ok "uv found: $(uv --version)"
else
  fail "uv not found — install: curl -LsSf https://astral.sh/uv/install.sh | sh"
fi

if [[ $NO_DOCKER -eq 0 ]]; then
  if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    ok "docker running: $(docker --version)"
  else
    fail "docker not running — start Docker Desktop or dockerd"
  fi
fi

# ── 2. Dependencies ───────────────────────────────────────────────────────────
step "Install dependencies"
if uv sync --extra dev -q; then
  ok "uv sync --extra dev"
else
  fail "uv sync failed"
fi

# ── 3. Tests ──────────────────────────────────────────────────────────────────
step "Run test suite"
if uv run --extra dev pytest -v --tb=short -q 2>&1 | tee /tmp/pytest-out.txt; then
  PASSED=$(grep -E "^\d+ passed" /tmp/pytest-out.txt | grep -oE "^[0-9]+" || echo "?")
  ok "pytest: $PASSED tests passed"
else
  fail "pytest failed — see output above"
fi

# ── 4. Docker build & health check ───────────────────────────────────────────
if [[ $NO_DOCKER -eq 0 ]]; then
  step "Bootstrap env"
  chmod +x scripts/install/bootstrap-env.sh
  if ./scripts/install/bootstrap-env.sh; then
    ok "scripts/install/bootstrap-env.sh"
  else
    fail "scripts/install/bootstrap-env.sh failed"
  fi

  step "Docker build"
  if docker compose build --quiet; then
    ok "docker compose build"
  else
    fail "docker compose build failed"
  fi

  step "Start server"
  docker compose up -d
  echo "  waiting for health check (up to 120s)..."
  for i in $(seq 1 24); do
    STATUS=$(docker compose ps --format json 2>/dev/null \
      | python3 -c "
import sys, json
for line in sys.stdin:
    try:
        s = json.loads(line)
        if s.get('Service') == 'server':
            print(s.get('Health', 'unknown'))
    except Exception:
        pass
" 2>/dev/null || echo "starting")
    echo "  attempt $i/12: $STATUS"
    if [[ $STATUS == healthy ]]; then
      ok "server healthy"
      break
    fi
    sleep 5
    if [[ $i -eq 24 ]]; then
      fail "server did not become healthy in 60s"
      docker compose logs server
    fi
  done

  step "Smoke test /health"
  HEALTH=$(curl -k -sf https://localhost:8443/health 2>/dev/null || echo "{}")
  STATUS_VAL=$(echo "$HEALTH" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))" 2>/dev/null || echo "")
  if [[ $STATUS_VAL == ok ]]; then
    ok "/health returned status=ok"
  else
    fail "/health unexpected response: $HEALTH"
  fi

  step "Teardown"
  docker compose down -v
  ok "docker compose down"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo
echo "════════════════════════════════"
echo " Results: $PASS passed, $FAIL failed"
echo "════════════════════════════════"
[[ $FAIL -eq 0 ]] && exit 0 || exit 1
