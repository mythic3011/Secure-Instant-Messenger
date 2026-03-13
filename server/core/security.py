"""
server/core/security.py — Auth token management, Argon2id, TOTP encryption, rate limiting.
"""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
import time

import argon2.low_level as argon2_ll
import pyotp
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from server.core.config import get_settings

# ---------------------------------------------------------------------------
# Password hashing — Argon2id
#
# Why Argon2id with t=3, m=64MiB, p=1:
#   Argon2id is the PHC winner and recommended by OWASP. The 'id' variant
#   combines Argon2i (side-channel resistant) and Argon2d (GPU resistant).
#   Parameters follow OWASP's "first recommended option":
#     t=3 iterations, m=64MiB memory, p=1 parallelism.
#   Higher memory cost is the primary defense against GPU/ASIC attacks.
#   p=1 is sufficient for a single-server deployment; higher parallelism
#   would reduce wall-clock time but increase peak memory per request.
# ---------------------------------------------------------------------------

# OWASP recommended parameters
_ARGON2_TIME_COST   = 3
_ARGON2_MEMORY_COST = 65536   # 64 MiB
_ARGON2_PARALLELISM = 1
_ARGON2_HASH_LEN    = 32


def hash_password(password: str) -> str:
    """
    Hash a password with Argon2id. Returns a self-contained string that
    includes the salt and parameters (argon2-cffi format).
    """
    import argon2
    ph = argon2.PasswordHasher(
        time_cost=_ARGON2_TIME_COST,
        memory_cost=_ARGON2_MEMORY_COST,
        parallelism=_ARGON2_PARALLELISM,
        hash_len=_ARGON2_HASH_LEN,
    )
    return ph.hash(password)


def verify_password(password: str, pw_hash: str) -> bool:
    """
    Verify a password against an Argon2id hash.
    Returns False (not raises) on mismatch — caller decides response.
    """
    import argon2
    ph = argon2.PasswordHasher()
    try:
        return ph.verify(pw_hash, password)
    except argon2.exceptions.VerifyMismatchError:
        return False


# ---------------------------------------------------------------------------
# Bearer token — opaque, SHA256-hashed in DB
#
# Why opaque tokens over JWT:
#   1. Instant revocation — revoking a JWT requires a blocklist or short expiry
#      with refresh tokens, adding complexity. Opaque tokens are revoked by
#      flipping revoked=1 in the DB, effective immediately.
#   2. SHA256 in DB — if the DB is stolen, the attacker gets hashes, not tokens.
#      JWT secrets in the DB would allow forging new tokens.
#   3. No key management — RS256 JWT requires RSA key rotation. We already have
#      a DB for every request, so stateless verification adds no benefit.
# ---------------------------------------------------------------------------

def generate_token() -> tuple[str, str]:
    """
    Generate a new opaque bearer token.
    Returns (raw_token, token_hash).
    raw_token is sent to the client; token_hash is stored in DB.
    """
    raw = secrets.token_urlsafe(32)   # 256-bit CSPRNG
    token_hash = hashlib.sha256(raw.encode()).hexdigest()
    return raw, token_hash


def hash_token(raw_token: str) -> str:
    """Hash a received bearer token for DB lookup."""
    return hashlib.sha256(raw_token.encode()).hexdigest()


# ---------------------------------------------------------------------------
# TOTP secret encryption — AES-256-GCM with server-side key
# ---------------------------------------------------------------------------

def _totp_enc_key(user_id: str) -> bytes:
    """
    Derive a per-user AES-256 key for TOTP secret encryption.
    Uses HKDF-SHA256 with the server's TOTP_ENCRYPTION_KEY as IKM.
    """
    settings = get_settings()
    ikm = bytes.fromhex(settings.totp_encryption_key)
    return HKDF(
        algorithm=SHA256(),
        length=32,
        salt=b"COMP3334-totp-v1",
        info=user_id.encode(),
    ).derive(ikm)


def encrypt_totp_secret(user_id: str, totp_secret: str) -> str:
    """
    Encrypt a TOTP secret for storage in DB.
    Returns base64(nonce || ciphertext_with_tag).
    """
    key   = _totp_enc_key(user_id)
    nonce = os.urandom(12)
    ct    = AESGCM(key).encrypt(nonce, totp_secret.encode(), user_id.encode())
    return base64.b64encode(nonce + ct).decode()


def decrypt_totp_secret(user_id: str, encrypted_blob: str) -> str:
    """
    Decrypt a TOTP secret from DB storage.
    Raises ValueError on decryption failure (wrong key or corrupted data).
    """
    key  = _totp_enc_key(user_id)
    raw  = base64.b64decode(encrypted_blob)
    nonce, ct = raw[:12], raw[12:]
    try:
        return AESGCM(key).decrypt(nonce, ct, user_id.encode()).decode()
    except Exception as exc:
        raise ValueError("Failed to decrypt TOTP secret.") from exc


def generate_totp_secret() -> str:
    """Generate a new random TOTP secret (base32)."""
    return pyotp.random_base32()


def make_totp_provisioning_uri(username: str, secret: str) -> str:
    """Return an otpauth:// URI for QR code scanning."""
    return pyotp.TOTP(secret).provisioning_uri(
        name=username,
        issuer_name="COMP3334-IM",
    )


def verify_totp(secret: str, code: str) -> bool:
    """Verify a 6-digit TOTP code. Allows ±1 window for clock skew."""
    return pyotp.TOTP(secret).verify(code, valid_window=1)


# ---------------------------------------------------------------------------
# Rate limiting — simple DB-backed token bucket
# ---------------------------------------------------------------------------

async def check_rate_limit(key: str, max_attempts: int, window_seconds: int) -> bool:
    """
    Atomic rate limit check using INSERT ... ON CONFLICT (upsert).
    Returns True if the request is allowed, False if rate-limited.
    key format: "login:<ip>" or "register:<ip>"

    Why upsert instead of the previous SELECT-then-UPDATE:
      The old implementation had a TOCTOU race — two concurrent requests
      could both read attempts=4 (below max=5) and both pass, exceeding
      the limit. The upsert makes the read-check-write a single SQL
      statement, which SQLite executes under its internal write lock.
    """
    from server.core.database import get_db
    db = await get_db()
    now = int(time.time())

    # Fast path: if currently locked out, reject without writing
    async with db.execute(
        "SELECT locked_until FROM rate_limits WHERE key = ?", (key,),
    ) as cur:
        row = await cur.fetchone()
    if row and row["locked_until"] and now < row["locked_until"]:
        return False

    # Atomic upsert — increment or reset window in one statement.
    # SQLite's ON CONFLICT executes atomically under the write lock.
    await db.execute(
        """INSERT INTO rate_limits (key, attempts, window_start, locked_until)
           VALUES (?1, 1, ?2, NULL)
           ON CONFLICT(key) DO UPDATE SET
               attempts = CASE
                   WHEN ?2 - rate_limits.window_start > ?3 THEN 1
                   ELSE rate_limits.attempts + 1
               END,
               window_start = CASE
                   WHEN ?2 - rate_limits.window_start > ?3 THEN ?2
                   ELSE rate_limits.window_start
               END,
               locked_until = CASE
                   WHEN ?2 - rate_limits.window_start <= ?3
                        AND rate_limits.attempts + 1 > ?4
                   THEN ?2 + ?3
                   ELSE NULL
               END""",
        (key, now, window_seconds, max_attempts),
    )
    await db.commit()

    # Read back to determine if this request was allowed
    async with db.execute(
        "SELECT attempts, locked_until FROM rate_limits WHERE key = ?", (key,),
    ) as cur:
        row = await cur.fetchone()

    if row and row["locked_until"] and now < row["locked_until"]:
        return False
    return row["attempts"] <= max_attempts if row else True
