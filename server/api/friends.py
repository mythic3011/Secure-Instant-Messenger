"""
server/api/friends.py — Friend request workflow, blocking, removing.
Covers: R13, R14, R15, R16
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, HTTPException, status

from server.api.auth import require_auth
from server.core.database import get_db
from shared.protocol import (
    FriendRequestAction,
    FriendRequestCreate,
    FriendRequestOut,
    FriendRequestStatus,
)

router = APIRouter(prefix="/v1/friends", tags=["friends"])
log = structlog.get_logger()


# ---------------------------------------------------------------------------
# R13 — Send friend request
# ---------------------------------------------------------------------------

@router.post("/request", response_model=FriendRequestOut, status_code=status.HTTP_201_CREATED)
async def send_friend_request(
    body: FriendRequestCreate,
    session: dict = Depends(require_auth),
) -> FriendRequestOut:
    """Send a friend request to another user by username."""
    db = await get_db()
    sender_id = session["user_id"]

    # Resolve recipient
    async with db.execute(
        "SELECT id FROM users WHERE username = ? AND deleted_at IS NULL",
        (body.recipient_username,),
    ) as cur:
        recipient = await cur.fetchone()
    if recipient is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    recipient_id = recipient["id"]

    if recipient_id == sender_id:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Cannot add yourself")

    # Check if blocked
    async with db.execute(
        "SELECT 1 FROM blocks WHERE blocker_id = ? AND blocked_id = ?",
        (recipient_id, sender_id),
    ) as cur:
        if await cur.fetchone():
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Action not allowed")

    # Check if already friends
    a, b = sorted([sender_id, recipient_id])
    async with db.execute(
        "SELECT 1 FROM friendships WHERE user_a_id = ? AND user_b_id = ?", (a, b)
    ) as cur:
        if await cur.fetchone():
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Already friends")

    # Upsert request (re-send if previously declined/cancelled)
    async with db.execute(
        """INSERT INTO friend_requests (sender_id, recipient_id, status)
           VALUES (?, ?, 'pending')
           ON CONFLICT(sender_id, recipient_id) DO UPDATE SET
             status = 'pending', updated_at = unixepoch()
           WHERE status IN ('declined', 'cancelled')
           RETURNING id, created_at""",
        (sender_id, recipient_id),
    ) as cur:
        row = await cur.fetchone()

    if row is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Request already pending or accepted")

    await db.commit()
    log.info("friend_request_sent", sender=sender_id, recipient=recipient_id)

    return FriendRequestOut(
        id=row["id"],
        sender_id=sender_id,
        sender_name=session["username"],
        recipient_id=recipient_id,
        status=FriendRequestStatus.PENDING,
        created_at=row["created_at"],
    )


# ---------------------------------------------------------------------------
# R14 — List pending requests
# ---------------------------------------------------------------------------

@router.get("/pending", response_model=list[FriendRequestOut])
async def list_pending(session: dict = Depends(require_auth)) -> list[FriendRequestOut]:
    """List incoming pending friend requests for the current user."""
    db = await get_db()
    async with db.execute(
        """SELECT fr.id, fr.sender_id, u.username AS sender_name,
                  fr.recipient_id, fr.status, fr.created_at
           FROM friend_requests fr
           JOIN users u ON u.id = fr.sender_id
           WHERE fr.recipient_id = ? AND fr.status = 'pending'
           ORDER BY fr.created_at DESC""",
        (session["user_id"],),
    ) as cur:
        rows = await cur.fetchall()

    log.debug("list_pending_requests", user_id=session["user_id"], count=len(rows))
    return [
        FriendRequestOut(
            id=r["id"],
            sender_id=r["sender_id"],
            sender_name=r["sender_name"],
            recipient_id=r["recipient_id"],
            status=FriendRequestStatus(r["status"]),
            created_at=r["created_at"],
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# R14 — Accept / decline / cancel
# ---------------------------------------------------------------------------

@router.put("/request/{request_id}", status_code=status.HTTP_204_NO_CONTENT)
async def handle_request(
    request_id: str,
    body: FriendRequestAction,
    session: dict = Depends(require_auth),
) -> None:
    """Accept, decline (recipient), or cancel (sender) a friend request."""
    db = await get_db()
    user_id = session["user_id"]

    async with db.execute(
        "SELECT sender_id, recipient_id, status FROM friend_requests WHERE id = ?",
        (request_id,),
    ) as cur:
        req = await cur.fetchone()

    if req is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Request not found")

    if req["status"] != "pending":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Request is not pending")

    action = body.action
    if action == "cancel" and req["sender_id"] != user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only sender can cancel")
    if action in ("accept", "decline") and req["recipient_id"] != user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only recipient can accept/decline")

    status_map = {"accept": "accepted", "decline": "declined", "cancel": "cancelled"}
    await db.execute(
        "UPDATE friend_requests SET status = ?, updated_at = unixepoch() WHERE id = ?",
        (status_map[action], request_id),
    )

    if action == "accept":
        a, b = sorted([req["sender_id"], req["recipient_id"]])
        await db.execute(
            "INSERT OR IGNORE INTO friendships (user_a_id, user_b_id) VALUES (?, ?)", (a, b)
        )

    await db.commit()
    log.info("friend_request_action", request_id=request_id, action=action, user_id=user_id)


# ---------------------------------------------------------------------------
# R15 — Remove friend
# ---------------------------------------------------------------------------

@router.delete("/{peer_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_friend(
    peer_id: str,
    session: dict = Depends(require_auth),
) -> None:
    """Remove a friend (mutual — removes friendship for both sides)."""
    db = await get_db()
    user_id = session["user_id"]
    a, b = sorted([user_id, peer_id])

    await db.execute(
        "DELETE FROM friendships WHERE user_a_id = ? AND user_b_id = ?", (a, b)
    )
    await db.commit()
    log.info("friend_removed", user_id=user_id, peer_id=peer_id)


# ---------------------------------------------------------------------------
# R15 — Block user
# ---------------------------------------------------------------------------

@router.post("/{peer_id}/block", status_code=status.HTTP_204_NO_CONTENT)
async def block_user(
    peer_id: str,
    session: dict = Depends(require_auth),
) -> None:
    """Block a user. Also removes friendship and cancels pending requests."""
    db = await get_db()
    user_id = session["user_id"]

    if peer_id == user_id:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Cannot block yourself")

    # Insert block
    await db.execute(
        "INSERT OR IGNORE INTO blocks (blocker_id, blocked_id) VALUES (?, ?)",
        (user_id, peer_id),
    )

    # Remove friendship
    a, b = sorted([user_id, peer_id])
    await db.execute("DELETE FROM friendships WHERE user_a_id = ? AND user_b_id = ?", (a, b))

    # Cancel any pending requests between them
    await db.execute(
        """UPDATE friend_requests SET status = 'cancelled', updated_at = unixepoch()
           WHERE status = 'pending'
           AND ((sender_id = ? AND recipient_id = ?) OR (sender_id = ? AND recipient_id = ?))""",
        (user_id, peer_id, peer_id, user_id),
    )
    await db.commit()
    log.info("user_blocked", blocker=user_id, blocked=peer_id)
