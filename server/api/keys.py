"""
server/api/keys.py — Public key upload and fetch.
Covers: R4
"""

from __future__ import annotations

import base64

import structlog
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.api.auth import require_auth
from server.core.database import get_db
from server.models import PublicKey, User
from shared.protocol import PublicKeyBundle

router = APIRouter(prefix="/v1/keys", tags=["keys"])
log = structlog.get_logger()

_ED25519_PUB_LEN = 32
_X25519_PUB_LEN = 32


def _verify_key_bundle(identity_pub_b64: str, dh_pub_b64: str, key_sig_b64: str) -> None:
    """
    Verify that the key bundle signature is valid.
    Raises HTTPException 422 if invalid.
    """
    try:
        identity_pub_bytes = base64.b64decode(identity_pub_b64, validate=True)
        dh_pub_bytes = base64.b64decode(dh_pub_b64, validate=True)
        sig_bytes = base64.b64decode(key_sig_b64, validate=True)
    except Exception:
        log.warning("key_bundle_rejected", reason="invalid_base64")
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid base64 in key bundle")

    if len(identity_pub_bytes) != _ED25519_PUB_LEN:
        log.warning("key_bundle_rejected", reason="identity_pub_wrong_length", got=len(identity_pub_bytes))
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="identity_pub must be 32 bytes")
    if len(dh_pub_bytes) != _X25519_PUB_LEN:
        log.warning("key_bundle_rejected", reason="dh_pub_wrong_length", got=len(dh_pub_bytes))
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="dh_pub must be 32 bytes")

    try:
        pub = Ed25519PublicKey.from_public_bytes(identity_pub_bytes)
        pub.verify(sig_bytes, identity_pub_bytes + dh_pub_bytes)
    except InvalidSignature:
        log.warning("key_bundle_rejected", reason="invalid_signature")
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Key bundle signature invalid")
    except Exception:
        log.warning("key_bundle_rejected", reason="malformed_public_key")
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Malformed public key")


@router.post("/upload", status_code=status.HTTP_204_NO_CONTENT)
async def upload_keys(
    body: PublicKeyBundle,
    session: dict = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> None:
    """
    Upload or replace the caller's public key bundle.
    The server verifies the self-signature before storing.
    """
    _verify_key_bundle(body.identity_pub_b64, body.dh_pub_b64, body.key_sig_b64)

    user_id = session["user_id"]

    # Check if key bundle already exists
    stmt = select(PublicKey).where(PublicKey.user_id == user_id)
    result = await db.execute(stmt)
    existing_key = result.scalar_one_or_none()

    if existing_key is not None:
        # Update existing key bundle
        existing_key.identity_pub = body.identity_pub_b64
        existing_key.dh_pub = body.dh_pub_b64
        existing_key.key_sig = body.key_sig_b64
    else:
        # Create new key bundle
        public_key = PublicKey(
            user_id=user_id,
            identity_pub=body.identity_pub_b64,
            dh_pub=body.dh_pub_b64,
            key_sig=body.key_sig_b64,
        )
        db.add(public_key)

    await db.commit()
    log.info("keys_uploaded", user_id=user_id)


@router.get("/{username}", response_model=PublicKeyBundle)
async def get_keys(
    username: str,
    session: dict = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> PublicKeyBundle:
    """
    Fetch the public key bundle for a user by username.
    Used by the initiating client to set up a session.
    """
    stmt = (
        select(User, PublicKey)
        .join(PublicKey, PublicKey.user_id == User.id)
        .where(User.username == username, User.deleted_at.is_(None))
    )
    result = await db.execute(stmt)
    row = result.first()

    if row is None:
        log.warning("get_keys_not_found", requested_username=username, requester=session["user_id"])
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User or keys not found")

    user, public_key = row
    log.info("keys_fetched", requested_username=username, requester=session["user_id"])
    return PublicKeyBundle(
        user_id=user.id,
        username=user.username,
        identity_pub_b64=public_key.identity_pub,
        dh_pub_b64=public_key.dh_pub,
        key_sig_b64=public_key.key_sig,
        uploaded_at=int(public_key.uploaded_at.timestamp()),
    )
