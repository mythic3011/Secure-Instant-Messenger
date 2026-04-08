#!/bin/sh

set -eu

VERBOSE=0

usage() {
    cat >&2 <<'USAGE'
Usage: scripts/install/install-prereqs.sh [--verbose]
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

verbose() {
    if [ "$VERBOSE" -eq 1 ]; then
        printf '%s\n' "$1"
    fi
}

tool_reason() {
    case "$1" in
        python3)
            printf '%s\n' "running project scripts and the Python application"
            ;;
        uv)
            printf '%s\n' "project dependency management and reproducible local execution"
            ;;
        openssl)
            printf '%s\n' "local TLS certificate generation for HTTPS development"
            ;;
        *)
            printf '%s\n' "project setup"
            ;;
    esac
}

detect_manager() {
    platform=$(uname -s)
    case "$platform" in
        Darwin)
            if command -v brew >/dev/null 2>&1; then
                printf '%s\n' "brew"
                return
            fi
            printf '[install-prereqs] unsupported package manager on macOS: brew not found\n' >&2
            exit 2
            ;;
        Linux)
            for manager in apt-get dnf yum pacman; do
                if command -v "$manager" >/dev/null 2>&1; then
                    printf '%s\n' "$manager"
                    return
                fi
            done
            printf '[install-prereqs] unsupported package manager on Linux: expected apt-get, dnf, yum, or pacman\n' >&2
            exit 2
            ;;
        *)
            printf '[install-prereqs] unsupported platform: %s\n' "$platform" >&2
            exit 2
            ;;
    esac
}

package_name() {
    tool=$1
    manager=$2
    case "$manager:$tool" in
        brew:python3)
            printf '%s\n' "python@3.12"
            ;;
        brew:uv|brew:openssl)
            printf '%s\n' "$tool"
            ;;
        apt-get:python3|dnf:python3|yum:python3)
            printf '%s\n' "python3"
            ;;
        apt-get:uv|dnf:uv|yum:uv|pacman:uv)
            printf '%s\n' "uv"
            ;;
        apt-get:openssl|dnf:openssl|yum:openssl|pacman:openssl)
            printf '%s\n' "openssl"
            ;;
        pacman:python3)
            printf '%s\n' "python"
            ;;
        *)
            printf '[install-prereqs] unsupported package mapping: %s via %s\n' "$tool" "$manager" >&2
            exit 2
            ;;
    esac
}

install_command_text() {
    tool=$1
    manager=$2
    package=$(package_name "$tool" "$manager")
    case "$manager" in
        brew)
            printf 'brew install %s\n' "$package"
            ;;
        apt-get|dnf|yum)
            if [ "$(id -u)" -eq 0 ]; then
                printf '%s install -y %s\n' "$manager" "$package"
            else
                printf 'sudo %s install -y %s\n' "$manager" "$package"
            fi
            ;;
        pacman)
            if [ "$(id -u)" -eq 0 ]; then
                printf 'pacman -S --noconfirm %s\n' "$package"
            else
                printf 'sudo pacman -S --noconfirm %s\n' "$package"
            fi
            ;;
        *)
            printf '[install-prereqs] unsupported package manager: %s\n' "$manager" >&2
            exit 2
            ;;
    esac
}

run_install() {
    tool=$1
    manager=$2
    package=$(package_name "$tool" "$manager")

    case "$manager" in
        brew)
            brew install "$package"
            ;;
        apt-get|dnf|yum)
            if [ "$(id -u)" -eq 0 ]; then
                "$manager" install -y "$package"
            else
                if ! command -v sudo >/dev/null 2>&1; then
                    printf '[install-prereqs] sudo is required to install %s via %s\n' "$tool" "$manager" >&2
                    exit 1
                fi
                sudo "$manager" install -y "$package"
            fi
            ;;
        pacman)
            if [ "$(id -u)" -eq 0 ]; then
                pacman -S --noconfirm "$package"
            else
                if ! command -v sudo >/dev/null 2>&1; then
                    printf '[install-prereqs] sudo is required to install %s via pacman\n' "$tool" >&2
                    exit 1
                fi
                sudo pacman -S --noconfirm "$package"
            fi
            ;;
        *)
            printf '[install-prereqs] unsupported package manager: %s\n' "$manager" >&2
            exit 2
            ;;
    esac
}

maybe_install() {
    tool=$1
    reason=$(tool_reason "$tool")

    if command -v "$tool" >/dev/null 2>&1; then
        verbose "[install-prereqs] $tool: already installed"
        return
    fi

    manager=$(detect_manager)
    command_text=$(install_command_text "$tool" "$manager")

    printf '[install-prereqs] missing prerequisite: %s\n' "$tool" >&2
    printf '[install-prereqs] required for: %s\n' "$reason" >&2
    printf '[install-prereqs] package manager command: %s\n' "$command_text" >&2
    printf '[install-prereqs] this is a system-level install, not a project-local change\n' >&2

    if [ ! -t 0 ]; then
        printf '[install-prereqs] interactive confirmation required to install %s\n' "$tool" >&2
        printf '[install-prereqs] rerun ./install.sh --fix in an interactive shell, or install %s manually first\n' "$tool" >&2
        exit 1
    fi

    printf 'Install %s now? [y/N] ' "$tool" >&2

    if ! IFS= read -r answer; then
        answer=
    fi

    case "$answer" in
        y|Y|yes|YES|Yes)
            run_install "$tool" "$manager"
            if ! command -v "$tool" >/dev/null 2>&1; then
                printf '[install-prereqs] installation finished but %s is still unavailable in PATH\n' "$tool" >&2
                exit 1
            fi
            printf '[install-prereqs] installed: %s\n' "$tool"
            ;;
        *)
            printf '[install-prereqs] user declined installation for %s\n' "$tool" >&2
            exit 1
            ;;
    esac
}

for tool in python3 uv openssl; do
    maybe_install "$tool"
done

printf '[install-prereqs] install pass complete\n'
