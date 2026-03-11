"""
server/core/config.py — Application configuration via environment variables.
Uses pydantic-settings for type-safe env loading.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import field_validator
import structlog


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or invalid.

    Validators can use this to produce a consistent exception type that
    upstream callers (such as startup code or tests) can catch and display
    nicely.
    """

log = structlog.get_logger()


class Settings(BaseSettings):
    _env_path = None
    if "ENV_FILE" in __import__("os").environ:
        _env_path = __import__("os").environ["ENV_FILE"]
    else:
        # prefer .env.local when available
        import os as _os
        if _os.path.exists(".env.local"):
            _env_path = ".env.local"
        elif _os.path.exists(".env"):
            _env_path = ".env"
    model_config = SettingsConfigDict(
        env_file=_env_path,
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
    # these are optional during development; validators supply temporary
    # keys, but must be provided in production environments.
    token_secret_key: Optional[str] = None  # used as HKDF ikm for token generation
    token_expiry_seconds: int = 900  # 15 minutes

    # TOTP secret encryption (server-side AES-GCM)
    totp_encryption_key: Optional[str] = None  # hex-encoded 32 bytes

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

    # pydantic v2 field validators/generators
    @field_validator("token_secret_key", mode="before")
    def ensure_token_secret(cls, v, info):
        """Generate a random key in development if not provided."""
        if v:
            return v
        env = info.data.get("app_env")
        if env != "production":
            import secrets

            generated = secrets.token_hex(32)
            log.warning("token_secret_key not set, generating temporary value")
            return generated
        raise ConfigError(
            f"token_secret_key must be supplied in production (app_env={env!r})"
        )

    @field_validator("totp_encryption_key", mode="before")
    def ensure_totp_key(cls, v, info):
        if v:
            return v
        env = info.data.get("app_env")
        if env != "production":
            import secrets

            generated = secrets.token_hex(32)
            log.warning("totp_encryption_key not set, generating temporary value")
            return generated
        raise ConfigError(
            f"totp_encryption_key must be supplied in production (app_env={env!r})"
        )

    # derived helper properties
    @property
    def base_url(self) -> str:
        """URL clients can use to reach this server (no scheme)."""
        scheme = "https" if self.app_env == "production" else "http"
        return f"{scheme}://{self.host}:{self.port}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
