#!/bin/sh

set -eu

VERBOSE=0

usage() {
    cat >&2 <<'USAGE'
Usage: scripts/install/check-prereqs.sh [--verbose]
USAGE
    exit 2
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --verbose)
            VERBOSE=1
            ;;
        *)
            usage
            ;;
    esac
    shift
done

say() {
    printf '%s\n' "$1"
}

verbose() {
    if [ "$VERBOSE" -eq 1 ]; then
        printf '%s\n' "$1"
    fi
}

fail_missing() {
    tool=$1
    reason=$2
    printf '[prereqs] missing prerequisite: %s\n' "$tool" >&2
    printf '[prereqs] required for: %s\n' "$reason" >&2
    printf '[prereqs] install %s manually, then rerun ./install.sh --fix\n' "$tool" >&2
    exit 1
}

check_tool() {
    tool=$1
    reason=$2
    if command -v "$tool" >/dev/null 2>&1; then
        verbose "[prereqs] $tool: ok"
        return
    fi
    fail_missing "$tool" "$reason"
}

check_tool python3 "running project scripts and the Python application"
check_tool uv "project dependency management and reproducible local execution"
check_tool openssl "local TLS certificate generation for HTTPS development"

say "[prereqs] check passed"
