from __future__ import annotations

import time

import aiosqlite
import structlog
from fastapi import HTTPException, Request, status

from server.core.config import get_settings
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

log = structlog.get_logger()
_DUMMY_HASH = (
    "$argon2id$v=19$m=65536,t=3,p=1$AAAAAAAAAAAAAAAAAAAAAA$"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
)


class AuthService:
    """Encapsulates authentication and session workflows."""

    def __init__(self, db: aiosqlite.Connection):
        self.db = db

    async def authenticate_bearer(self, request: Request) -> dict[str, str]:
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            log.warning(
                "auth_rejected", reason="missing_bearer_token", path=request.url.path
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing token"
            )

        raw_token = auth_header.removeprefix("Bearer ").strip()
        token_hash = hash_token(raw_token)
        now = int(time.time())

        async with self.db.execute(
            "SELECT s.id, s.user_id, u.username FROM sessions s "
            "JOIN users u ON u.id = s.user_id "
            "WHERE s.token_hash = ? AND s.revoked = 0 "
            "AND s.expires_at > ? AND u.deleted_at IS NULL",
            (token_hash, now),
        ) as cur:
            row = await cur.fetchone()

        if row is None:
            log.warning(
                "auth_rejected",
                reason="invalid_or_expired_token",
                path=request.url.path,
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired token",
            )

        return {
            "session_id": row["id"],
            "user_id": row["user_id"],
            "username": row["username"],
        }

    async def register_user(
        self, body: RegisterRequest, request: Request
    ) -> RegisterResponse:
        settings = get_settings()
        client_ip = request.client.host if request.client else "unknown"

        allowed = await check_rate_limit(
            f"register:{client_ip}",
            settings.rate_limit_register_max,
            settings.rate_limit_register_window,
        )
        if not allowed:
            log.warning("rate_limit_exceeded", endpoint="register", client_ip=client_ip)
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Rate limit exceeded",
            )

        async with self.db.execute(
            "SELECT id FROM users WHERE username = ?", (body.username,)
        ) as cur:
            if await cur.fetchone():
                log.info(
                    "register_conflict", username=body.username, client_ip=client_ip
                )
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Username already taken",
                )

        pw_hash = hash_password(body.password)
        totp_secret = generate_totp_secret()

        async with self.db.execute(
            "INSERT INTO users (username, pw_hash, totp_secret) VALUES (?, ?, '') RETURNING id",
            (body.username, pw_hash),
        ) as cur:
            row = await cur.fetchone()
            log.info("user_created", username=body.username, client_ip=client_ip)
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to create user",
            )
        user_id = row["id"]

        encrypted_totp = encrypt_totp_secret(user_id, totp_secret)
        await self.db.execute(
            "UPDATE users SET totp_secret = ? WHERE id = ?", (encrypted_totp, user_id)
        )
        await self.db.execute(
            "INSERT INTO public_keys (user_id, identity_pub, dh_pub, key_sig) VALUES (?, ?, ?, ?)",
            (user_id, body.identity_pub_b64, body.dh_pub_b64, body.key_sig_b64),
        )
        await self.db.commit()

        totp_uri = make_totp_provisioning_uri(body.username, totp_secret)
        log.info("user_registered", username=body.username, user_id=user_id)
        return RegisterResponse(user_id=user_id, totp_provisioning_uri=totp_uri)

    async def login_user(self, body: LoginRequest, request: Request) -> LoginResponse:
        settings = get_settings()
        client_ip = request.client.host if request.client else "unknown"

        allowed = await check_rate_limit(
            f"login:{client_ip}",
            settings.rate_limit_login_max,
            settings.rate_limit_login_window,
        )
        if not allowed:
            log.warning("rate_limit_exceeded", endpoint="login", client_ip=client_ip)
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Rate limit exceeded",
            )

        async with self.db.execute(
            "SELECT id, pw_hash, totp_secret FROM users WHERE username = ? AND deleted_at IS NULL",
            (body.username,),
        ) as cur:
            user = await cur.fetchone()

        pw_hash = user["pw_hash"] if user else _DUMMY_HASH
        if not verify_password(body.password, pw_hash) or user is None:
            log.info("login_failed", username=body.username, reason="bad_password")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials"
            )

        totp_secret = decrypt_totp_secret(user["id"], user["totp_secret"])
        if not verify_totp(totp_secret, body.totp_code):
            log.info("login_failed", user_id=user["id"], reason="bad_totp")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials"
            )

        raw_token, token_hash = generate_token()
        expires_at = int(time.time()) + settings.token_expiry_seconds

        await self.db.execute(
            "INSERT INTO sessions (user_id, token_hash, expires_at) VALUES (?, ?, ?)",
            (user["id"], token_hash, expires_at),
        )
        await self.db.commit()

        log.info("login_success", user_id=user["id"], client_ip=client_ip)
        return LoginResponse(access_token=raw_token, expires_at=expires_at)

    async def logout_session(self, session_id: str, user_id: str) -> None:
        await self.db.execute(
            "UPDATE sessions SET revoked = 1 WHERE id = ?", (session_id,)
        )
        await self.db.commit()
        log.info("logout", user_id=user_id)
