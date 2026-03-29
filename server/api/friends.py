"""
server/api/friends.py — Friend request workflow, blocking, removing.
Covers: R13, R14, R15, R16
"""

from __future__ import annotations

import secrets

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import and_, delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from server.api.auth import require_auth
from server.core.database import get_db
from server.models import (
    Block,
    Conversation,
    FriendRequest,
    Friendship,
    User,
)
from server.models import (
    FriendRequestStatus as DBFriendRequestStatus,
)
from shared.protocol import (
    FriendRequestAction,
    FriendRequestCreate,
    FriendRequestOut,
)
from shared.protocol import (
    FriendRequestStatus as WireFriendRequestStatus,
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
    db: AsyncSession = Depends(get_db),
) -> FriendRequestOut:
    """Send a friend request to another user by username."""
    sender_id = session["user_id"]

    # Resolve recipient
    stmt = select(User).where(User.username == body.recipient_username, User.deleted_at.is_(None))
    result = await db.execute(stmt)
    recipient = result.scalar_one_or_none()
    if recipient is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    recipient_id = recipient.id

    if recipient_id == sender_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Cannot add yourself",
        )

    # Check if blocked
    stmt = select(Block).where(Block.blocker_id == recipient_id, Block.blocked_id == sender_id)
    result = await db.execute(stmt)
    if result.scalar_one_or_none() is not None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Action not allowed")

    # Check if already friends
    a, b = sorted([sender_id, recipient_id])
    stmt = select(Friendship).where(Friendship.user_a_id == a, Friendship.user_b_id == b)
    result = await db.execute(stmt)
    if result.scalar_one_or_none() is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Already friends")

    # Upsert request (re-send if previously declined/cancelled)
    stmt = select(FriendRequest).where(
        FriendRequest.sender_id == sender_id,
        FriendRequest.recipient_id == recipient_id,
    )
    result = await db.execute(stmt)
    existing_request = result.scalar_one_or_none()

    if existing_request is not None:
        if existing_request.status in (
            DBFriendRequestStatus.ACCEPTED,
            DBFriendRequestStatus.PENDING,
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Request already pending or accepted",
            )
        # Re-send if previously declined/cancelled
        existing_request.status = DBFriendRequestStatus.PENDING
        await db.commit()
        request_id = existing_request.id
        created_at = existing_request.created_at
    else:
        # Create new request
        request_id = secrets.token_hex(16)
        friend_request = FriendRequest(
            id=request_id,
            sender_id=sender_id,
            recipient_id=recipient_id,
            status=DBFriendRequestStatus.PENDING,
        )
        db.add(friend_request)
        await db.commit()
        created_at = friend_request.created_at

    log.info("friend_request_sent", sender=sender_id, recipient=recipient_id)

    return FriendRequestOut(
        id=request_id,
        sender_id=sender_id,
        sender_name=session["username"],
        recipient_id=recipient_id,
        status=WireFriendRequestStatus.PENDING,
        created_at=int(created_at.timestamp()),
    )


# ---------------------------------------------------------------------------
# R14 — List pending requests
# ---------------------------------------------------------------------------

@router.get("/pending", response_model=list[FriendRequestOut])
async def list_pending(
    session: dict = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> list[FriendRequestOut]:
    """List incoming pending friend requests for the current user."""
    stmt = (
        select(FriendRequest, User.username)
        .join(User, User.id == FriendRequest.sender_id)
        .where(
            FriendRequest.recipient_id == session["user_id"],
            FriendRequest.status == DBFriendRequestStatus.PENDING,
        )
        .order_by(FriendRequest.created_at.desc())
    )
    result = await db.execute(stmt)
    rows = result.all()

    log.debug("list_pending_requests", user_id=session["user_id"], count=len(rows))
    return [
        FriendRequestOut(
            id=fr.id,
            sender_id=fr.sender_id,
            sender_name=username,
            recipient_id=fr.recipient_id,
            status=WireFriendRequestStatus(fr.status.value),
            created_at=int(fr.created_at.timestamp()),
        )
        for fr, username in rows
    ]


# ---------------------------------------------------------------------------
# R14 — Accept / decline / cancel
# ---------------------------------------------------------------------------

@router.put("/request/{request_id}", status_code=status.HTTP_204_NO_CONTENT)
async def handle_request(
    request_id: str,
    body: FriendRequestAction,
    session: dict = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Accept, decline (recipient), or cancel (sender) a friend request."""
    user_id = session["user_id"]

    stmt = select(FriendRequest).where(FriendRequest.id == request_id)
    result = await db.execute(stmt)
    req = result.scalar_one_or_none()

    if req is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Request not found")

    if req.status != DBFriendRequestStatus.PENDING:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Request is not pending")

    action = body.action
    if action == "cancel" and req.sender_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only sender can cancel",
        )
    if action in ("accept", "decline") and req.recipient_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only recipient can accept/decline",
        )

    status_map = {
        "accept": DBFriendRequestStatus.ACCEPTED,
        "decline": DBFriendRequestStatus.DECLINED,
        "cancel": DBFriendRequestStatus.CANCELLED,
    }
    req.status = status_map[action]

    if action == "accept":
        a, b = sorted([req.sender_id, req.recipient_id])
        # Check if friendship already exists
        stmt = select(Friendship).where(Friendship.user_a_id == a, Friendship.user_b_id == b)
        result = await db.execute(stmt)
        if result.scalar_one_or_none() is None:
            friendship = Friendship(user_a_id=a, user_b_id=b)
            db.add(friendship)

        # Create conversation with random ID
        conv_id = secrets.token_hex(16)
        # Check if conversation already exists
        stmt = select(Conversation).where(Conversation.user_a_id == a, Conversation.user_b_id == b)
        result = await db.execute(stmt)
        if result.scalar_one_or_none() is None:
            conversation = Conversation(id=conv_id, user_a_id=a, user_b_id=b)
            db.add(conversation)

    await db.commit()
    log.info("friend_request_action", request_id=request_id, action=action, user_id=user_id)


# ---------------------------------------------------------------------------
# R15 — Remove friend
# ---------------------------------------------------------------------------

@router.delete("/{peer_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_friend(
    peer_id: str,
    session: dict = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Remove a friend (mutual — removes friendship for both sides)."""
    user_id = session["user_id"]
    a, b = sorted([user_id, peer_id])

    stmt = delete(Friendship).where(Friendship.user_a_id == a, Friendship.user_b_id == b)
    await db.execute(stmt)
    await db.commit()
    log.info("friend_removed", user_id=user_id, peer_id=peer_id)


# ---------------------------------------------------------------------------
# R15 — Block user
# ---------------------------------------------------------------------------

@router.post("/{peer_id}/block", status_code=status.HTTP_204_NO_CONTENT)
async def block_user(
    peer_id: str,
    session: dict = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Block a user. Also removes friendship and cancels pending requests."""
    user_id = session["user_id"]

    if peer_id == user_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Cannot block yourself",
        )

    # Insert block
    stmt = select(Block).where(Block.blocker_id == user_id, Block.blocked_id == peer_id)
    result = await db.execute(stmt)
    if result.scalar_one_or_none() is None:
        block = Block(blocker_id=user_id, blocked_id=peer_id)
        db.add(block)

    # Remove friendship
    a, b = sorted([user_id, peer_id])
    stmt = delete(Friendship).where(Friendship.user_a_id == a, Friendship.user_b_id == b)
    await db.execute(stmt)

    # Cancel any pending requests between them
    stmt = (
        update(FriendRequest)
        .where(
            FriendRequest.status == DBFriendRequestStatus.PENDING,
            or_(
                and_(FriendRequest.sender_id == user_id, FriendRequest.recipient_id == peer_id),
                and_(FriendRequest.sender_id == peer_id, FriendRequest.recipient_id == user_id),
            ),
        )
        .values(status=DBFriendRequestStatus.CANCELLED)
    )
    await db.execute(stmt)
    await db.commit()
    log.info("user_blocked", blocker=user_id, blocked=peer_id)
