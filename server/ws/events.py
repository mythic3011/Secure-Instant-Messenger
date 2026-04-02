"""Canonical WebSocket event builders for server outbound message payloads."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from server.models.message import Message
from shared.protocol import DeliveryStatus, MessageEnvelope, MessageType


def _unix_timestamp(value: datetime | int) -> int:
    if isinstance(value, datetime):
        return int(value.timestamp())
    return int(value)


def build_message_payload(message: Message | MessageEnvelope) -> dict[str, Any]:
    delivered_at = getattr(message, "delivered_at", None)
    delivery_status = getattr(message, "delivery_status", None)
    if isinstance(delivery_status, DeliveryStatus):
        delivery_status_value = delivery_status.value
    elif isinstance(delivery_status, str):
        delivery_status_value = delivery_status
    else:
        delivery_status_value = (
            DeliveryStatus.DELIVERED.value
            if delivered_at is not None
            else DeliveryStatus.SENT.value
        )

    return {
        "id": message.id,
        "type": MessageType.MESSAGE.value,
        "sender_id": message.sender_id,
        "recipient_id": message.recipient_id,
        "conversation_id": message.conversation_id,
        "counter": message.counter,
        "nonce_b64": message.nonce_b64,
        "ciphertext_b64": message.ciphertext_b64,
        "eph_pub_b64": message.eph_pub_b64,
        "conv_dh_pub_b64": message.conv_dh_pub_b64,
        "chain_index": getattr(message, "chain_index", 0),
        "ttl_seconds": message.ttl_seconds,
        "sent_at": _unix_timestamp(message.sent_at),
        "delivery_status": delivery_status_value,
    }


def build_message_event(message: Message | MessageEnvelope) -> dict[str, Any]:
    return {
        "type": MessageType.MESSAGE.value,
        "payload": build_message_payload(message),
    }


def build_friend_request_event(
    *,
    event: str,
    request_id: str,
    sender_id: str,
    recipient_id: str,
    conversation_id: str | None = None,
) -> dict[str, Any]:
    """Build a payload for the existing ``friend_request`` websocket branch.

    ``event="accepted"`` gives the client an explicit post-commit refresh
    signal without introducing a new top-level websocket message type.
    """

    payload: dict[str, Any] = {
        "event": event,
        "request_id": request_id,
        "sender_id": sender_id,
        "recipient_id": recipient_id,
    }
    if conversation_id is not None:
        payload["conversation_id"] = conversation_id

    return {
        "type": "friend_request",
        "payload": payload,
    }
