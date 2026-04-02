"""
shared/protocol.py — Wire format definitions shared by server and client.
All models use pydantic v2. All binary fields are base64-encoded strings on the wire.

DO NOT add business logic here. This file defines data shapes only.
"""

from __future__ import annotations

import base64
import uuid
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PROTOCOL_VERSION = "1"
KDF_INFO_PREFIX = "COMP3334-IM-v1"
NONCE_BYTES = 12  # AES-GCM nonce size
KEY_BYTES = 32  # AES-256 key size
MAX_MESSAGE_BYTES = 64_000  # ~64 KB plaintext limit
REPLAY_WINDOW = 50  # accept counters within last 50 of max seen
MAX_SKIP = 50  # max skipped messages in ratchet chain

# ---------------------------------------------------------------------------
# Session lifecycle limits
#
# SECURITY TRADE-OFF: Session re-key limit
#   After the initial 2-DH setup, the client uses a symmetric ratchet to derive
#   a fresh message key for each message. This provides per-message forward
#   secrecy within a conversation. We still enforce a hard session message limit
#   so long-lived conversations periodically establish a fresh root key.
#
#   This does not replace a full Signal-style Double Ratchet. It is a simpler
#   bound on long-lived session exposure and a clear re-key trigger for the UI.
#
#   Value rationale:
#   - Too low: frequent re-keying adds latency and UX friction
#   - Too high: larger exposure window for long-lived root-key compromise
#   - 500 messages ≈ a moderately active conversation for several days
#   - periodic re-keying keeps the simplified design explainable for the report
# ---------------------------------------------------------------------------
MAX_SESSION_MESSAGES = 500  # force re-key after this many messages per session

# ---------------------------------------------------------------------------
# Metadata exposure annotations (READ behavior)
#
# The following metadata is visible to the server in plaintext:
#   - sender_id, recipient_id: who is talking to whom
#   - conversation_id: which conversation a message belongs to
#   - message timing (sent_at, delivered_at): when messages are sent
#   - delivery_status: whether a message was sent or delivered
#   - message size (ciphertext length): approximate plaintext length
#
# The server CANNOT see:
#   - message plaintext (encrypted with AES-256-GCM)
#   - session keys (never leave the client)
#   - private keys (encrypted at rest with Argon2id-derived key)
#
# This metadata exposure is inherent to any server-mediated messaging system.
# Hiding it would require a mixnet or onion routing, which is out of scope.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class DeliveryStatus(StrEnum):
    SENT = "sent"  # server acknowledged receipt
    DELIVERED = "delivered"  # recipient client acknowledged
    READ = "read"  # reserved for future UX/protocol extension, not currently implemented


class FriendRequestStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    DECLINED = "declined"
    CANCELLED = "cancelled"


class MessageType(StrEnum):
    MESSAGE = "message"
    ACK = "ack"  # delivery acknowledgement event
    SYSTEM = "system"  # key change warning, etc.


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=32, pattern=r"^[a-zA-Z0-9_\-]+$")
    password: str = Field(min_length=12, max_length=128)
    identity_pub_b64: str  # Ed25519 public key, base64
    dh_pub_b64: str  # X25519 public key, base64
    key_sig_b64: str  # Ed25519 sig over (identity_pub || dh_pub), base64
    totp_uri: str | None = None  # returned by server after provisioning

    @field_validator("password")
    @classmethod
    def validate_password_complexity(cls, v: str) -> str:
        """Enforce mixed-case, digit, and special-character password rules."""
        if not any(c.isupper() for c in v):
            raise ValueError("Password must contain at least one uppercase letter")
        if not any(c.islower() for c in v):
            raise ValueError("Password must contain at least one lowercase letter")
        if not any(c.isdigit() for c in v):
            raise ValueError("Password must contain at least one digit")
        if not any(c in "!@#$%^&*()_+-=[]{}|;:,.<>?" for c in v):
            raise ValueError(
                "Password must contain at least one special character (!@#$%^&*()_+-=[]{}|;:,.<>?)"
            )
        return v


class RegisterResponse(BaseModel):
    user_id: str
    totp_provisioning_uri: str  # otpauth:// URI for QR code scan


class LoginRequest(BaseModel):
    username: str
    password: str
    totp_code: str = Field(min_length=6, max_length=6)


class LoginResponse(BaseModel):
    access_token: str
    expires_at: int  # unix timestamp


class LogoutRequest(BaseModel):
    pass  # token comes from Authorization header


# ---------------------------------------------------------------------------
# Key management
# ---------------------------------------------------------------------------


class PublicKeyBundle(BaseModel):
    """
    The bundle of public keys for a user.
    Server stores and distributes this.
    Clients verify key_sig before using the keys.
    """

    user_id: str
    username: str
    identity_pub_b64: str  # Ed25519 pub
    dh_pub_b64: str  # X25519 pub
    key_sig_b64: str  # Ed25519 sig over (identity_pub || dh_pub)
    uploaded_at: int  # unix timestamp


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

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    type: MessageType = MessageType.MESSAGE
    sender_id: str
    recipient_id: str
    conversation_id: str  # server-assigned random ID
    counter: int = Field(ge=0)
    nonce_b64: str  # base64(12 random bytes) — never reuse per key
    ciphertext_b64: str  # base64(AES-256-GCM output)
    eph_pub_b64: str | None = None  # X25519 ephemeral pub, first message only
    conv_dh_pub_b64: str | None = None  # X25519 per-conversation DH pub, first message only
    chain_index: int = 0  # ratchet chain index for this message
    ttl_seconds: int | None = None  # None = no expiry
    sent_at: int  # unix timestamp (client clock)
    delivery_status: DeliveryStatus = DeliveryStatus.SENT

    @field_validator("nonce_b64", "ciphertext_b64")
    @classmethod
    def must_be_base64(cls, v: str) -> str:
        try:
            base64.b64decode(v, validate=True)
        except Exception as exc:
            raise ValueError("field must be valid base64") from exc
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
    id: str
    stored_at: int
    delivered_at: int | None = None


class FetchMessagesRequest(BaseModel):
    conversation_id: str
    before_id: str | None = None  # cursor for pagination
    limit: int = Field(default=50, le=100)


class FetchMessagesResponse(BaseModel):
    messages: list[MessageEnvelope]
    has_more: bool
    next_cursor: str | None = None


# ---------------------------------------------------------------------------
# Delivery ACK (sent by recipient, E2EE-protected)
# ---------------------------------------------------------------------------


class DeliveryAck(BaseModel):
    """
    Recipient sends this after successfully decrypting a message.
    The ACK is plaintext — the server learns which message was decrypted.
    This is a known metadata exposure documented in ARCHITECTURE.md §14.
    Encrypting the ACK payload would require the server to forward opaque
    blobs to the sender, adding complexity without meaningful privacy gain
    (the server already knows sender, recipient, and timing).
    """

    message_id: str
    conversation_id: str


# ---------------------------------------------------------------------------
# WebSocket push messages (server -> client)
# ---------------------------------------------------------------------------


class WsPush(BaseModel):
    type: Literal["message", "ack", "friend_request", "system"]
    payload: dict  # one of the above models, serialised


# ---------------------------------------------------------------------------
# Friends
# ---------------------------------------------------------------------------


class FriendRequestCreate(BaseModel):
    recipient_username: str


class FriendRequestAction(BaseModel):
    action: Literal["accept", "decline", "cancel"]


class FriendRequestOut(BaseModel):
    id: str
    sender_id: str
    sender_name: str
    recipient_id: str
    status: FriendRequestStatus
    created_at: int


class FriendRequestPushPayload(BaseModel):
    event: FriendRequestStatus
    request_id: str
    sender_id: str
    recipient_id: str
    conversation_id: str | None = None


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------


class ConversationOut(BaseModel):
    id: str
    peer_id: str
    peer_username: str
    last_message_at: int | None
    unread_count: int


class ConversationListResponse(BaseModel):
    conversations: list[ConversationOut]
