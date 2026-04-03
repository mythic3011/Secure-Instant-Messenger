"""
server/api/auth.py — Registration, login, logout endpoints.
Covers: R1, R2, R3
"""

from __future__ import annotations

import time
import uuid

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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
from server.models import PublicKey, Session, User
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

async def require_auth(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    """
    FastAPI dependency. Validates the Authorization: Bearer <token> header.
    Returns the session row on success; raises 401 on failure.
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        log.warning("auth_rejected", reason="missing_bearer_token", path=request.url.path)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing token")

    raw_token = auth_header.removeprefix("Bearer ").strip()
    token_hash = hash_token(raw_token)
    now = int(time.time())

    # Query session with user join using ORM
    stmt = (
        select(Session, User)
        .join(User, User.id == Session.user_id)
        .where(
            Session.token_hash == token_hash,
            Session.revoked == 0,
            Session.expires_at > now,
            User.deleted_at.is_(None),
        )
    )
    result = await db.execute(stmt)
    row = result.first()

    if row is None:
        log.warning("auth_rejected", reason="invalid_or_expired_token", path=request.url.path)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")

    session, user = row
    return {"session_id": session.id, "user_id": user.id, "username": user.username}


# ---------------------------------------------------------------------------
# R1 — Registration
# ---------------------------------------------------------------------------

@router.post("/register", response_model=RegisterResponse, status_code=status.HTTP_201_CREATED)
async def register(
    body: RegisterRequest, request: Request, db: AsyncSession = Depends(get_db)
) -> RegisterResponse:
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
        log.warning("rate_limit_exceeded", endpoint="register", client_ip=client_ip)
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Rate limit exceeded")

    # Check username uniqueness (case-insensitive via COLLATE NOCASE in schema)
    stmt = select(User).where(User.username == body.username)
    result = await db.execute(stmt)
    if result.scalar_one_or_none() is not None:
        log.info("register_conflict", username=body.username, client_ip=client_ip)
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Username already taken")

    # Hash password
    pw_hash = hash_password(body.password)

    # Generate and encrypt TOTP secret
    totp_secret = generate_totp_secret()

    # Create user with empty TOTP secret first
    user_id = uuid.uuid4().hex
    user = User(
        id=user_id,
        username=body.username,
        pw_hash=pw_hash,
        totp_secret="",  # Will be updated after encryption
    )
    db.add(user)
    await db.flush()  # Get the ID

    # Encrypt TOTP secret with user_id as AD
    encrypted_totp = encrypt_totp_secret(user_id, totp_secret)
    user.totp_secret = encrypted_totp

    # Store public key bundle
    public_key = PublicKey(
        user_id=user_id,
        identity_pub=body.identity_pub_b64,
        dh_pub=body.dh_pub_b64,
        key_sig=body.key_sig_b64,
    )
    db.add(public_key)
    await db.commit()

    totp_uri = make_totp_provisioning_uri(body.username, totp_secret)
    log.info("user_registered", username=body.username, user_id=user_id)

    return RegisterResponse(user_id=user_id, totp_provisioning_uri=totp_uri)


# ---------------------------------------------------------------------------
# R2 — Login with password + TOTP
# ---------------------------------------------------------------------------

@router.post("/login", response_model=LoginResponse)
async def login(
    body: LoginRequest, request: Request, db: AsyncSession = Depends(get_db)
) -> LoginResponse:
    """
    Authenticate with password + TOTP. Returns an opaque bearer token.
    """
    settings = get_settings()
    client_ip = request.client.host if request.client else "unknown"

    # Rate limit by IP (do NOT include username to avoid user enumeration)
    rate_limit_key = f"login:{client_ip}"
    allowed = await check_rate_limit(
        rate_limit_key,
        settings.rate_limit_login_max,
        settings.rate_limit_login_window,
        is_failed_attempt=True,
    )
    if not allowed:
        log.warning("rate_limit_exceeded", endpoint="login", client_ip=client_ip)
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Rate limit exceeded")

    # Query user using ORM
    stmt = select(User).where(User.username == body.username, User.deleted_at.is_(None))
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    # Constant-time: always verify password even if user not found (dummy hash)
    _DUMMY_HASH = "$argon2id$v=19$m=65536,t=3,p=1$AAAAAAAAAAAAAAAAAAAAAA$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    pw_hash = user.pw_hash if user else _DUMMY_HASH

    if not verify_password(body.password, pw_hash) or user is None:
        allowed = await check_rate_limit(
            rate_limit_key,
            settings.rate_limit_login_max,
            settings.rate_limit_login_window,
            is_failed_attempt=True,
        )
        if not allowed:
            log.warning("rate_limit_exceeded", endpoint="login", client_ip=client_ip)
            raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Rate limit exceeded")

        log.info("login_failed", username=body.username, reason="bad_password")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    # Verify TOTP
    totp_secret = decrypt_totp_secret(user.id, user.totp_secret)
    if not verify_totp(totp_secret, body.totp_code):
        allowed = await check_rate_limit(
            rate_limit_key,
            settings.rate_limit_login_max,
            settings.rate_limit_login_window,
            is_failed_attempt=True,
        )
        if not allowed:
            log.warning("rate_limit_exceeded", endpoint="login", client_ip=client_ip)
            raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Rate limit exceeded")

        log.info("login_failed", user_id=user.id, reason="bad_totp")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    # Issue token
    raw_token, token_hash = generate_token()
    expires_at = int(time.time()) + settings.token_expiry_seconds

    session = Session(
        id=uuid.uuid4().hex,
        user_id=user.id,
        token_hash=token_hash,
        expires_at=expires_at,
    )
    db.add(session)

    await db.commit()

    log.info("login_success", user_id=user.id, client_ip=client_ip)
    return LoginResponse(access_token=raw_token, expires_at=expires_at)


# ---------------------------------------------------------------------------
# R3 — Logout / token revocation
# ---------------------------------------------------------------------------

@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    session: dict = Depends(require_auth), db: AsyncSession = Depends(get_db)
) -> None:
    """Revoke the current session token immediately."""
    stmt = select(Session).where(Session.id == session["session_id"])
    result = await db.execute(stmt)
    session_obj = result.scalar_one_or_none()

    if session_obj:
        session_obj.revoked = True
        await db.commit()

    log.info("logout", user_id=session["user_id"])
