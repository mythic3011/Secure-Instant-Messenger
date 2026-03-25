"""tests/unit/test_config.py — Config path resolution tests."""

from __future__ import annotations

import os
from unittest.mock import patch

from server.core.config import Settings


def _make_settings(**overrides):
    """Build a Settings instance with required secrets filled in."""
    defaults = {
        "token_secret_key": "a" * 64,
        "totp_encryption_key": "b" * 64,
    }
    defaults.update(overrides)
    return Settings(**defaults)


def test_default_database_url_is_relative():
    """Default database_url must use relative path on host (no /.dockerenv)."""
    with (
        patch.dict(os.environ, {}, clear=True),
        patch("shared.env.Path.exists", return_value=False),
    ):
        from importlib import reload

        import shared.env

        reload(shared.env)
        s = _make_settings()
    assert "/app/data" not in s.database_url
    assert "./data/" in s.database_url


def test_container_database_url_is_absolute():
    """In container (/.dockerenv exists), database_url uses /app/data."""
    with (
        patch.dict(os.environ, {}, clear=True),
        patch("shared.env.Path.exists", return_value=True),
    ):
        from importlib import reload

        import shared.env

        reload(shared.env)
        from shared.env import default_database_url

        url = default_database_url()
    assert "/app/data/im.db" in url


def test_database_url_env_override():
    """DATABASE_URL env var overrides the auto-detected default."""
    container_url = "sqlite+aiosqlite:////app/data/im.db"
    with patch.dict(os.environ, {"DATABASE_URL": container_url}, clear=True):
        s = _make_settings()
    assert s.database_url == container_url


def test_default_tls_paths_are_relative_on_host():
    """Default TLS paths must be relative on host."""
    with (
        patch.dict(os.environ, {}, clear=True),
        patch("shared.env.Path.exists", return_value=False),
    ):
        from importlib import reload

        import shared.env

        reload(shared.env)
        from shared.env import default_tls_cert, default_tls_key

        cert = default_tls_cert()
        key = default_tls_key()
    assert cert.startswith("./")
    assert key.startswith("./")


def test_in_container_tls_paths_are_absolute():
    """In container, TLS paths use /app/certs."""
    with patch("shared.env.Path.exists", return_value=True):
        from importlib import reload

        import shared.env

        reload(shared.env)
        from shared.env import default_tls_cert, default_tls_key

        cert = default_tls_cert()
        key = default_tls_key()
    assert cert.startswith("/app/certs")
    assert key.startswith("/app/certs")
