"""
tests/unit/test_auth.py — Unit tests for server/core/security.py
"""

from __future__ import annotations

import pytest

from server.core.security import (
    decrypt_totp_secret,
    encrypt_totp_secret,
    generate_token,
    generate_totp_secret,
    hash_password,
    hash_token,
    verify_password,
    verify_totp,
)

# ---------------------------------------------------------------------------
# Password hashing — Argon2id
# ---------------------------------------------------------------------------


def test_hash_password_roundtrip():
    pw = "correct-horse-battery-staple"
    h = hash_password(pw)
    assert verify_password(pw, h) is True


def test_verify_password_wrong():
    h = hash_password("correct-password")
    assert verify_password("wrong-password", h) is False


def test_hash_password_unique_salts():
    pw = "same-password"
    h1 = hash_password(pw)
    h2 = hash_password(pw)
    # Argon2id includes salt in output — two hashes of same pw must differ
    assert h1 != h2
    # But both must verify
    assert verify_password(pw, h1) is True
    assert verify_password(pw, h2) is True


# ---------------------------------------------------------------------------
# Bearer token
# ---------------------------------------------------------------------------


def test_generate_token_returns_pair():
    raw, token_hash = generate_token()
    assert isinstance(raw, str)
    assert isinstance(token_hash, str)
    assert raw != token_hash


def test_hash_token_consistent():
    raw, stored_hash = generate_token()
    assert hash_token(raw) == stored_hash


def test_different_tokens_different_hashes():
    _, h1 = generate_token()
    _, h2 = generate_token()
    assert h1 != h2


# ---------------------------------------------------------------------------
# TOTP secret encryption
# ---------------------------------------------------------------------------


def test_totp_encrypt_decrypt_roundtrip(monkeypatch):
    # Patch settings to provide a known key
    import server.core.security as sec

    monkeypatch.setattr(
        sec,
        "get_settings",
        lambda: type("S", (), {"totp_encryption_key": "a" * 64})(),
    )
    user_id = "user-abc-123"
    secret = generate_totp_secret()
    blob = encrypt_totp_secret(user_id, secret)
    recovered = decrypt_totp_secret(user_id, blob)
    assert recovered == secret


def test_totp_decrypt_wrong_user_id_fails(monkeypatch):
    import server.core.security as sec

    monkeypatch.setattr(
        sec,
        "get_settings",
        lambda: type("S", (), {"totp_encryption_key": "b" * 64})(),
    )
    user_id = "user-abc-123"
    secret = generate_totp_secret()
    blob = encrypt_totp_secret(user_id, secret)
    with pytest.raises(ValueError):
        decrypt_totp_secret("different-user-id", blob)


def test_totp_verify_correct_code():
    import pyotp

    secret = generate_totp_secret()
    code = pyotp.TOTP(secret).now()
    assert verify_totp(secret, code) is True


def test_totp_verify_wrong_code():
    secret = generate_totp_secret()
    assert verify_totp(secret, "000000") is False
