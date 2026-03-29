#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
SOURCE_HOOK="${REPO_ROOT}/scripts/git-hooks/pre-commit"
TARGET_DIR="${REPO_ROOT}/.git/hooks"
TARGET_HOOK="${TARGET_DIR}/pre-commit"

if [[ ! -d "${REPO_ROOT}/.git" ]]; then
    printf 'error: %s is not a git repository root\n' "${REPO_ROOT}" >&2
    exit 1
fi

if [[ ! -f "${SOURCE_HOOK}" ]]; then
    printf 'error: missing source hook: %s\n' "${SOURCE_HOOK}" >&2
    exit 1
fi

mkdir -p "${TARGET_DIR}"
cp "${SOURCE_HOOK}" "${TARGET_HOOK}"
chmod +x "${TARGET_HOOK}"

printf 'installed git hook: %s\n' "${TARGET_HOOK}"
printf 'note: hook requires uv and project dev dependencies\n'
