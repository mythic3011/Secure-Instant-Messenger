"""
server/core/config.py — Application configuration via environment variables.
Uses pydantic-settings for type-safe env loading.
"""

from __future__ import annotations

from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env.local",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Server
    app_env: str = "development"
    host: str = "0.0.0.0"
    port: int = 8443
    log_level: str = "info"

    # Database
    database_url: str = "sqlite+aiosqlite:////app/data/im.db"

    # Auth tokens — opaque bearer tokens, SHA256-hashed in DB
    token_secret_key: str  # used as HKDF ikm for token generation
    token_expiry_seconds: int = 900  # 15 minutes

    # TOTP secret encryption (server-side AES-GCM)
    totp_encryption_key: str  # hex-encoded 32 bytes

    # TLS
    tls_cert_file: str = "/app/certs/server.crt"
    tls_key_file: str = "/app/certs/server.key"

    # Rate limiting
    rate_limit_login_max: int = 5
    rate_limit_login_window: int = 300
    rate_limit_register_max: int = 3
    rate_limit_register_window: int = 3600

    # Message retention
    max_message_age_days: int = 30
    ttl_cleanup_interval: int = 300


@lru_cache
def get_settings() -> Settings:
    return Settings()
