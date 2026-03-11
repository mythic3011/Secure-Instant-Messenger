"""
shared/protocol.py — Wire format definitions shared by server and client.
All models use pydantic v2. All binary fields are base64-encoded strings on the wire.

DO NOT add business logic here. This file defines data shapes only.
"""

from __future__ import annotations

import enum
from typing import Literal

from pydantic import BaseModel, Field, field_validator
import base64
import uuid


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PROTOCOL_VERSION = "1"
KDF_INFO_PREFIX  = "COMP3334-IM-v1"
NONCE_BYTES      = 12     # AES-GCM nonce size
KEY_BYTES        = 32     # AES-256 key size
MAX_MESSAGE_BYTES = 64_000  # ~64 KB plaintext limit
REPLAY_WINDOW    = 50     # accept counters within last 50 of max seen


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class DeliveryStatus(str, enum.Enum):
    SENT      = "sent"       # server acknowledged receipt
    DELIVERED = "delivered"  # recipient client acknowledged
    READ      = "read"       # optional, for future


class FriendRequestStatus(str, enum.Enum):
    PENDING   = "pending"
    ACCEPTED  = "accepted"
    DECLINED  = "declined"
    CANCELLED = "cancelled"


class MessageType(str, enum.Enum):
    MESSAGE = "message"
    ACK     = "ack"        # delivery acknowledgement (E2EE-protected)
    SYSTEM  = "system"     # key change warning, etc.


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=32, pattern=r"^[a-zA-Z0-9_\-]+$")
    password: str = Field(min_length=12, max_length=128)
    identity_pub_b64: str  # Ed25519 public key, base64
    dh_pub_b64: str        # X25519 public key, base64
    key_sig_b64: str       # Ed25519 sig over (identity_pub || dh_pub), base64
    totp_uri: str | None = None  # returned by server after provisioning


class RegisterResponse(BaseModel):
    user_id: str
    totp_provisioning_uri: str   # otpauth:// URI for QR code scan


class LoginRequest(BaseModel):
    username: str
    password: str
    totp_code: str = Field(min_length=6, max_length=6)


class LoginResponse(BaseModel):
    access_token: str
    expires_at: int   # unix timestamp


class LogoutRequest(BaseModel):
    pass   # token comes from Authorization header


# ---------------------------------------------------------------------------
# Key management
# ---------------------------------------------------------------------------

class PublicKeyBundle(BaseModel):
    """
    The bundle of public keys for a user.
    Server stores and distributes this.
    Clients verify key_sig before using the keys.
    """
    user_id:          str
    username:         str
    identity_pub_b64: str   # Ed25519 pub
    dh_pub_b64:       str   # X25519 pub
    key_sig_b64:      str   # Ed25519 sig over (identity_pub || dh_pub)
    uploaded_at:      int   # unix timestamp


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------

class MessageEnvelope(BaseModel):
    """
    Wire format for a single encrypted message.

    Associated Data (AD) bound to the AES-GCM tag:
        sender_id || "|" || recipient_id || "|" || conversation_id
        || "|" || counter.to_bytes(8, 'big')
        || "|" || (ttl_seconds or 0).to_bytes(4, 'big')
        || "|" || sent_at.to_bytes(8, 'big')

    Tampering with any AD field invalidates the GCM authentication tag.
    """
    id:              str = Field(default_factory=lambda: str(uuid.uuid4()))
    type:            MessageType = MessageType.MESSAGE
    sender_id:       str
    recipient_id:    str
    conversation_id: str          # SHA256(sorted(sender_id, recipient_id))[:16]
    counter:         int = Field(ge=0)
    nonce_b64:       str          # base64(12 random bytes) — never reuse per key
    ciphertext_b64:  str          # base64(AES-256-GCM output)
    eph_pub_b64:     str | None = None   # X25519 ephemeral pub, first message only
    ttl_seconds:     int | None = None   # None = no expiry
    sent_at:         int          # unix timestamp (client clock)
    delivery_status: DeliveryStatus = DeliveryStatus.SENT

    @field_validator("nonce_b64", "ciphertext_b64")
    @classmethod
    def must_be_base64(cls, v: str) -> str:
        try:
            base64.b64decode(v, validate=True)
        except Exception:
            raise ValueError("field must be valid base64")
        return v

    def compute_ad(self) -> bytes:
        """
        Compute the Associated Data bytes for AES-GCM.
        Must match exactly on both encrypt and decrypt sides.
        """
        parts = [
            self.sender_id.encode(),
            b"|",
            self.recipient_id.encode(),
            b"|",
            self.conversation_id.encode(),
            b"|",
            self.counter.to_bytes(8, "big"),
            b"|",
            (self.ttl_seconds or 0).to_bytes(4, "big"),
            b"|",
            self.sent_at.to_bytes(8, "big"),
        ]
        return b"".join(parts)


class SendMessageRequest(BaseModel):
    envelope: MessageEnvelope


class SendMessageResponse(BaseModel):
    id:           str
    stored_at:    int
    delivered_at: int | None = None


class FetchMessagesRequest(BaseModel):
    conversation_id: str
    before_id:       str | None = None   # cursor for pagination
    limit:           int = Field(default=50, le=100)


class FetchMessagesResponse(BaseModel):
    messages:    list[MessageEnvelope]
    has_more:    bool
    next_cursor: str | None = None


# ---------------------------------------------------------------------------
# Delivery ACK (sent by recipient, E2EE-protected)
# ---------------------------------------------------------------------------

class DeliveryAck(BaseModel):
    """
    Recipient sends this after successfully decrypting a message.
    The ack itself is encrypted so the server cannot correlate
    decryption success with specific messages (best-effort).
    """
    message_id:      str
    conversation_id: str
    ack_nonce_b64:   str       # encrypted ack payload nonce
    ack_ct_b64:      str       # encrypted ack payload (contains message_id)


# ---------------------------------------------------------------------------
# WebSocket push messages (server → client)
# ---------------------------------------------------------------------------

class WsPush(BaseModel):
    type:    Literal["message", "ack", "friend_request", "system"]
    payload: dict   # one of the above models, serialised


# ---------------------------------------------------------------------------
# Friends
# ---------------------------------------------------------------------------

class FriendRequestCreate(BaseModel):
    recipient_username: str


class FriendRequestAction(BaseModel):
    action: Literal["accept", "decline", "cancel"]


class FriendRequestOut(BaseModel):
    id:           str
    sender_id:    str
    sender_name:  str
    recipient_id: str
    status:       FriendRequestStatus
    created_at:   int


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------

class ConversationOut(BaseModel):
    id:              str
    peer_id:         str
    peer_username:   str
    last_message_at: int | None
    unread_count:    int


class ConversationListResponse(BaseModel):
    conversations: list[ConversationOut]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_conversation_id(user_a: str, user_b: str) -> str:
    """
    Stable, symmetric conversation ID.
    SHA256(sorted(user_a, user_b))[:16] hex string.
    """
    import hashlib
    pair = "|".join(sorted([user_a, user_b])).encode()
    return hashlib.sha256(pair).hexdigest()[:16]
