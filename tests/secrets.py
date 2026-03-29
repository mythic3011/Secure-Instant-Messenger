"""Centralized test-only secret fixtures."""

TEST_ACCOUNT_PASSWORD = "Password123!"  # noqa: S105  # pragma: allowlist secret
TEST_TOKEN_SECRET_KEY = "a" * 64
TEST_TOTP_ENCRYPTION_KEY = "b" * 64
