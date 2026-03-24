"""
shared/env.py — Environment detection utilities.

Reusable by server, client, and scripts. No heavy dependencies.
Detects container vs local environment and provides sane default paths.
"""

from __future__ import annotations

from pathlib import Path


def in_container() -> bool:
    """Detect if running inside a Docker container via /.dockerenv."""
    return Path("/.dockerenv").exists()


def default_data_dir() -> str:
    """Return the default data directory path for the current environment."""
    return "/app/data" if in_container() else "./data"


def default_certs_dir() -> str:
    """Return the default certs directory path for the current environment."""
    return "/app/certs" if in_container() else "./certs"


def default_database_url() -> str:
    """Return the default SQLite database URL for the current environment."""
    data = default_data_dir()
    return f"sqlite+aiosqlite:///{data}/im.db"


def default_tls_cert() -> str:
    """Return the default TLS cert path for the current environment."""
    return f"{default_certs_dir()}/server.crt"


def default_tls_key() -> str:
    """Return the default TLS key path for the current environment."""
    return f"{default_certs_dir()}/server.key"
