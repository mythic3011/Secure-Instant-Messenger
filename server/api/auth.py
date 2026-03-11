"""
server/api/auth.py — Registration, login, logout endpoints.
Covers: R1, R2, R3
"""

from __future__ import annotations

import time

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status

from server.core.config import get_settings
from server.core.database import get_db
from server.core.security import (
    check_rate_limit,
    decrypt_totp_secret,
    encrypt_totp_secret,
    generate_token,
    generate_totp_secret,
    hash_password,
    hash_token,
    make_totp_provisioning_uri,
    verify_password,
    verify_totp,
)
from shared.protocol import (
    LoginRequest,
    LoginResponse,
    RegisterRequest,
    RegisterResponse,
)

router = APIRouter(prefix="/v1/auth", tags=["auth"])
log = structlog.get_logger()


# ---------------------------------------------------------------------------
# Dependency — extract and validate bearer token
# ---------------------------------------------------------------------------

async def require_auth(request: Request) -> dict:
    """
    FastAPI dependency. Validates the Authorization: Bearer <token> header.
    Returns the session row on success; raises 401 on failure.
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing token")

    raw_token  = auth_header.removeprefix("Bearer ").strip()
    token_hash = hash_token(raw_token)
    now        = int(time.time())

    db = await get_db()
    async with db.execute(
        "SELECT s.id, s.user_id, u.username FROM sessions s "
        "JOIN users u ON u.id = s.user_id "
        "WHERE s.token_hash = ? AND s.revoked = 0 AND s.expires_at > ? AND u.deleted_at IS NULL",
        (token_hash, now),
    ) as cur:
        row = await cur.fetchone()

    if row is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")

    return {"session_id": row["id"], "user_id": row["user_id"], "username": row["username"]}


# ---------------------------------------------------------------------------
# R1 — Registration
# ---------------------------------------------------------------------------

@router.post("/register", response_model=RegisterResponse, status_code=status.HTTP_201_CREATED)
async def register(body: RegisterRequest, request: Request) -> RegisterResponse:
    """
    Register a new user.
    - Validates username uniqueness
    - Hashes password with Argon2id
    - Generates TOTP secret, encrypts it at rest
    - Stores Ed25519 + X25519 public keys with self-signature
    """
    settings = get_settings()
    client_ip = request.client.host if request.client else "unknown"

    # Rate limit by IP
    allowed = await check_rate_limit(
        f"register:{client_ip}",
        settings.rate_limit_register_max,
        settings.rate_limit_register_window,
    )
    if not allowed:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Rate limit exceeded")

    db = await get_db()

    # Check username uniqueness (case-insensitive via COLLATE NOCASE in schema)
    async with db.execute(
        "SELECT id FROM users WHERE username = ?", (body.username,)
    ) as cur:
        if await cur.fetchone():
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Username already taken")

    # Hash password
    pw_hash = hash_password(body.password)

    # Generate and encrypt TOTP secret
    totp_secret = generate_totp_secret()

    # Insert user (get ID first so we can use it for TOTP encryption)
    async with db.execute(
        "INSERT INTO users (username, pw_hash, totp_secret) VALUES (?, ?, '') RETURNING id",
        (body.username, pw_hash),
    ) as cur:
        row = await cur.fetchone()
    user_id = row["id"]

    # Encrypt TOTP secret with user_id as AD
    encrypted_totp = encrypt_totp_secret(user_id, totp_secret)
    await db.execute(
        "UPDATE users SET totp_secret = ? WHERE id = ?", (encrypted_totp, user_id)
    )

    # Store public key bundle
    await db.execute(
        "INSERT INTO public_keys (user_id, identity_pub, dh_pub, key_sig) VALUES (?, ?, ?, ?)",
        (user_id, body.identity_pub_b64, body.dh_pub_b64, body.key_sig_b64),
    )
    await db.commit()

    totp_uri = make_totp_provisioning_uri(body.username, totp_secret)
    log.info("user_registered", username=body.username, user_id=user_id)

    return RegisterResponse(user_id=user_id, totp_provisioning_uri=totp_uri)


# ---------------------------------------------------------------------------
# R2 — Login with password + TOTP
# ---------------------------------------------------------------------------

@router.post("/login", response_model=LoginResponse)
async def login(body: LoginRequest, request: Request) -> LoginResponse:
    """
    Authenticate with password + TOTP. Returns an opaque bearer token.
    """
    settings = get_settings()
    client_ip = request.client.host if request.client else "unknown"

    # Rate limit by IP (do NOT include username to avoid user enumeration)
    allowed = await check_rate_limit(
        f"login:{client_ip}",
        settings.rate_limit_login_max,
        settings.rate_limit_login_window,
    )
    if not allowed:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Rate limit exceeded")

    db = await get_db()

    async with db.execute(
        "SELECT id, pw_hash, totp_secret FROM users WHERE username = ? AND deleted_at IS NULL",
        (body.username,),
    ) as cur:
        user = await cur.fetchone()

    # Constant-time: always verify password even if user not found (dummy hash)
    _DUMMY_HASH = "$argon2id$v=19$m=65536,t=3,p=1$AAAAAAAAAAAAAAAAAAAAAA$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    pw_hash = user["pw_hash"] if user else _DUMMY_HASH

    if not verify_password(body.password, pw_hash) or user is None:
        log.info("login_failed", username=body.username, reason="bad_password")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    # Verify TOTP
    totp_secret = decrypt_totp_secret(user["id"], user["totp_secret"])
    if not verify_totp(totp_secret, body.totp_code):
        log.info("login_failed", user_id=user["id"], reason="bad_totp")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    # Issue token
    raw_token, token_hash = generate_token()
    expires_at = int(time.time()) + settings.token_expiry_seconds

    await db.execute(
        "INSERT INTO sessions (user_id, token_hash, expires_at) VALUES (?, ?, ?)",
        (user["id"], token_hash, expires_at),
    )
    await db.commit()

    log.info("login_success", user_id=user["id"])
    return LoginResponse(access_token=raw_token, expires_at=expires_at)


# ---------------------------------------------------------------------------
# R3 — Logout / token revocation
# ---------------------------------------------------------------------------

@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(session: dict = Depends(require_auth)) -> None:
    """Revoke the current session token immediately."""
    db = await get_db()
    await db.execute(
        "UPDATE sessions SET revoked = 1 WHERE id = ?", (session["session_id"],)
    )
    await db.commit()
    log.info("logout", user_id=session["user_id"])
