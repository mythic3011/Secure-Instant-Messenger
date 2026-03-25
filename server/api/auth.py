"""
server/api/auth.py — Registration, login, logout endpoints.
Covers: R1, R2, R3
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status

from server.core.database import get_db
from server.services.auth_service import AuthService
from shared.protocol import (
    LoginRequest,
    LoginResponse,
    RegisterRequest,
    RegisterResponse,
)

router = APIRouter(prefix="/v1/auth", tags=["auth"])


# ---------------------------------------------------------------------------
# Dependency — extract and validate bearer token
# ---------------------------------------------------------------------------


async def require_auth(request: Request) -> dict:
    """
    FastAPI dependency. Validates the Authorization: Bearer <token> header.
    Returns the session row on success; raises 401 on failure.
    """
    db = await get_db()
    return await AuthService(db).authenticate_bearer(request)


SessionDep = Depends(require_auth)


# ---------------------------------------------------------------------------
# R1 — Registration
# ---------------------------------------------------------------------------


@router.post(
    "/register", response_model=RegisterResponse, status_code=status.HTTP_201_CREATED
)
async def register(body: RegisterRequest, request: Request) -> RegisterResponse:
    """
    Register a new user.
    - Validates username uniqueness
    - Hashes password with Argon2id
    - Generates TOTP secret, encrypts it at rest
    - Stores Ed25519 + X25519 public keys with self-signature
    """
    db = await get_db()
    return await AuthService(db).register_user(body, request)


# ---------------------------------------------------------------------------
# R2 — Login with password + TOTP
# ---------------------------------------------------------------------------


@router.post("/login", response_model=LoginResponse)
async def login(body: LoginRequest, request: Request) -> LoginResponse:
    """
    Authenticate with password + TOTP. Returns an opaque bearer token.
    """
    db = await get_db()
    return await AuthService(db).login_user(body, request)


# ---------------------------------------------------------------------------
# R3 — Logout / token revocation
# ---------------------------------------------------------------------------


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(session: dict = SessionDep) -> None:
    """Revoke the current session token immediately."""
    db = await get_db()
    await AuthService(db).logout_session(session["session_id"], session["user_id"])
