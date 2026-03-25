"""
server/ws/handler.py — WebSocket connection manager and push delivery.
Tracks online users; pushes messages in real-time when recipient is connected.
"""

from __future__ import annotations

import asyncio
import json
import time

import structlog
from fastapi import WebSocket, WebSocketDisconnect

from server.core.database import get_db

log = structlog.get_logger()

# Map user_id → active WebSocket connection
_connections: dict[str, WebSocket] = {}
_lock = asyncio.Lock()


async def push_to_user(user_id: str, payload: dict) -> bool:
    """
    Push a JSON payload to a connected user.
    Returns True if delivered, False if user is offline.
    """
    async with _lock:
        ws = _connections.get(user_id)
    if ws is None:
        return False
    try:
        await ws.send_text(json.dumps(payload))
        return True
    except Exception as exc:
        log.warning("ws_push_failed", user_id=user_id, error=str(exc))
        async with _lock:
            _connections.pop(user_id, None)
        return False


async def websocket_endpoint(websocket: WebSocket, user_id: str) -> None:
    """
    Handle a WebSocket connection for an authenticated user.
    Connection is already accepted and authenticated by ws_endpoint() in main.py.
    On connect: deliver any queued offline messages.
    On message: handle delivery ACKs from client.
    """
    # websocket.accept() already called by ws_endpoint() in main.py
    # after first-frame authentication succeeded.

    async with _lock:
        _connections[user_id] = websocket

    log.info("ws_connected", user_id=user_id)

    try:
        # Deliver queued offline messages
        await _flush_offline_queue(websocket, user_id)

        # Keep connection alive; handle incoming ACKs
        while True:
            try:
                raw = await asyncio.wait_for(websocket.receive_text(), timeout=60.0)
                await _handle_client_message(user_id, raw)
            except TimeoutError:
                # Send ping to keep connection alive
                await websocket.send_text(json.dumps({"type": "ping"}))

    except WebSocketDisconnect:
        log.info("ws_disconnected_clean", user_id=user_id)
    except Exception as exc:
        log.warning("ws_error", user_id=user_id, error=str(exc))
    finally:
        async with _lock:
            _connections.pop(user_id, None)
        log.info("ws_disconnected", user_id=user_id)


async def _flush_offline_queue(websocket: WebSocket, user_id: str) -> None:
    """Deliver all undelivered messages to a newly connected user."""
    db = await get_db()
    now = int(time.time())

    async with db.execute(
        """SELECT id, conversation_id, sender_id, recipient_id, counter,
                  nonce_b64, ciphertext_b64, eph_pub_b64, ttl_seconds, sent_at
           FROM messages
           WHERE recipient_id = ? AND delivered_at IS NULL
             AND (expires_at IS NULL OR expires_at > ?)
           ORDER BY sent_at ASC""",
        (user_id, now),
    ) as cur:
        rows = await cur.fetchall()

    delivered_ids = []
    for row in rows:
        payload = {
            "type": "message",
            "payload": {
                "id": row["id"],
                "type": "message",
                "sender_id": row["sender_id"],
                "recipient_id": row["recipient_id"],
                "conversation_id": row["conversation_id"],
                "counter": row["counter"],
                "nonce_b64": row["nonce_b64"],
                "ciphertext_b64": row["ciphertext_b64"],
                "eph_pub_b64": row["eph_pub_b64"],
                "ttl_seconds": row["ttl_seconds"],
                "sent_at": row["sent_at"],
                "delivery_status": "delivered",
            },
        }
        try:
            await websocket.send_text(json.dumps(payload))
            delivered_ids.append(row["id"])
        except Exception as exc:
            log.warning(
                "offline_queue_send_failed",
                user_id=user_id,
                msg_id=row["id"],
                error=str(exc),
            )
            break  # connection dropped mid-flush

    if delivered_ids:
        placeholders = ",".join("?" * len(delivered_ids))
        await db.execute(
            f"UPDATE messages SET delivered_at = ? WHERE id IN ({placeholders})",
            [now, *delivered_ids],
        )
        await db.commit()
        log.info("offline_queue_flushed", user_id=user_id, count=len(delivered_ids))


async def _handle_client_message(user_id: str, raw: str) -> None:
    """Process a message received from the client over WebSocket (e.g. ACK)."""
    try:
        msg = json.loads(raw)
    except json.JSONDecodeError:
        log.warning("ws_invalid_json", user_id=user_id)
        return

    if msg.get("type") == "pong":
        log.debug("ws_pong", user_id=user_id)
        return  # keepalive response

    # Client-side ACK: {"type": "ack", "message_id": "..."}
    if msg.get("type") == "ack":
        message_id = msg.get("message_id")
        if not message_id:
            log.warning("ws_ack_missing_message_id", user_id=user_id)
            return
        db = await get_db()
        now = int(time.time())
        async with db.execute(
            "SELECT sender_id, delivered_at FROM messages WHERE id = ? AND recipient_id = ?",
            (message_id, user_id),
        ) as cur:
            row = await cur.fetchone()
        if row and row["delivered_at"] is None:
            await db.execute(
                "UPDATE messages SET delivered_at = ? WHERE id = ?", (now, message_id)
            )
            await db.commit()
            # Notify sender
            await push_to_user(
                row["sender_id"],
                {
                    "type": "ack",
                    "payload": {"message_id": message_id, "delivered_at": now},
                },
            )
            log.info(
                "ws_ack_processed",
                message_id=message_id,
                recipient=user_id,
                sender=row["sender_id"],
            )
    else:
        log.debug("ws_unknown_message_type", user_id=user_id, msg_type=msg.get("type"))
