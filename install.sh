#!/bin/sh

set -eu

MODE=check
VERBOSE=0

usage() {
    cat >&2 <<'USAGE'
Usage: ./install.sh [--check|--fix] [--verbose]
USAGE
    exit 2
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --check)
            MODE=check
            ;;
        --fix)
            MODE=fix
            ;;
        --verbose)
            VERBOSE=1
            ;;
        *)
            usage
            ;;
    esac
    shift
done

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_ROOT=$SCRIPT_DIR
PREREQ_SCRIPT="$PROJECT_ROOT/scripts/install/check-prereqs.sh"
INSTALL_PREREQ_SCRIPT="$PROJECT_ROOT/scripts/install/install-prereqs.sh"
BOOTSTRAP_SCRIPT="$PROJECT_ROOT/scripts/install/bootstrap-env.sh"

if [ ! -f "$PREREQ_SCRIPT" ] || [ ! -f "$INSTALL_PREREQ_SCRIPT" ] || [ ! -f "$BOOTSTRAP_SCRIPT" ]; then
    printf '[install] installer scripts missing under %s/scripts/install\n' "$PROJECT_ROOT" >&2
    exit 1
fi

cd "$PROJECT_ROOT"
export UV_CACHE_DIR="${UV_CACHE_DIR:-$PROJECT_ROOT/.uv-cache}"

VERBOSE_FLAG=
if [ "$VERBOSE" -eq 1 ]; then
    VERBOSE_FLAG=--verbose
fi

if [ "$MODE" = "check" ]; then
    sh "$PREREQ_SCRIPT" $VERBOSE_FLAG
    sh "$BOOTSTRAP_SCRIPT" --check $VERBOSE_FLAG
    printf '[install] check passed\n'
    exit 0
fi

sh "$INSTALL_PREREQ_SCRIPT" $VERBOSE_FLAG
sh "$PREREQ_SCRIPT" $VERBOSE_FLAG
sh "$BOOTSTRAP_SCRIPT" --fix $VERBOSE_FLAG
printf '[install] fix completed\n'
