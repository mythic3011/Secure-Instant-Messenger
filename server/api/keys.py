"""
server/api/keys.py — Public key upload and fetch.
Covers: R4
"""

from __future__ import annotations

import base64

import structlog
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.exceptions import InvalidSignature
from fastapi import APIRouter, Depends, HTTPException, status

from server.api.auth import require_auth
from server.core.database import get_db
from shared.protocol import PublicKeyBundle

router = APIRouter(prefix="/v1/keys", tags=["keys"])
log = structlog.get_logger()

_ED25519_PUB_LEN = 32
_X25519_PUB_LEN  = 32


def _verify_key_bundle(identity_pub_b64: str, dh_pub_b64: str, key_sig_b64: str) -> None:
    """
    Verify that the key bundle signature is valid.
    Raises HTTPException 422 if invalid.
    """
    try:
        identity_pub_bytes = base64.b64decode(identity_pub_b64, validate=True)
        dh_pub_bytes       = base64.b64decode(dh_pub_b64, validate=True)
        sig_bytes          = base64.b64decode(key_sig_b64, validate=True)
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
) -> None:
    """
    Upload or replace the caller's public key bundle.
    The server verifies the self-signature before storing.
    """
    _verify_key_bundle(body.identity_pub_b64, body.dh_pub_b64, body.key_sig_b64)

    db = await get_db()
    await db.execute(
        """INSERT INTO public_keys (user_id, identity_pub, dh_pub, key_sig)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(user_id) DO UPDATE SET
             identity_pub = excluded.identity_pub,
             dh_pub       = excluded.dh_pub,
             key_sig      = excluded.key_sig,
             uploaded_at  = unixepoch()""",
        (session["user_id"], body.identity_pub_b64, body.dh_pub_b64, body.key_sig_b64),
    )
    await db.commit()
    log.info("keys_uploaded", user_id=session["user_id"])


@router.get("/{username}", response_model=PublicKeyBundle)
async def get_keys(
    username: str,
    session: dict = Depends(require_auth),
) -> PublicKeyBundle:
    """
    Fetch the public key bundle for a user by username.
    Used by the initiating client to set up a session.
    """
    db = await get_db()
    async with db.execute(
        """SELECT u.id, u.username, pk.identity_pub, pk.dh_pub, pk.key_sig, pk.uploaded_at
           FROM users u JOIN public_keys pk ON pk.user_id = u.id
           WHERE u.username = ? AND u.deleted_at IS NULL""",
        (username,),
    ) as cur:
        row = await cur.fetchone()

    if row is None:
        log.warning("get_keys_not_found", requested_username=username, requester=session["user_id"])
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User or keys not found")

    log.info("keys_fetched", requested_username=username, requester=session["user_id"])
    return PublicKeyBundle(
        user_id=row["id"],
        username=row["username"],
        identity_pub_b64=row["identity_pub"],
        dh_pub_b64=row["dh_pub"],
        key_sig_b64=row["key_sig"],
        uploaded_at=row["uploaded_at"],
    )
