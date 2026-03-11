"""
server/main.py — FastAPI application entrypoint.
Wires up all routers, WebSocket endpoint, startup/shutdown lifecycle,
and the TTL cleanup background task.
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from scalar_fastapi import get_scalar_api_reference
import structlog
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from scalar_fastapi import get_scalar_api_reference

from server.api.auth import require_auth, router as auth_router
from server.api.conversations import router as conv_router
from server.api.friends import router as friends_router
from server.api.keys import router as keys_router
from server.api.messages import router as msg_router
from server.core.config import get_settings
from server.core.database import close_db, get_db, init_db
from server.ws.handler import websocket_endpoint

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.processors.JSONRenderer(),
    ]
)

log = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await init_db()
    asyncio.create_task(_ttl_cleanup_loop())
    log.info("server_started", port=get_settings().port)
    yield
    # Shutdown
    await close_db()
    log.info("server_stopped")


app = FastAPI(
    title="COMP3334 Secure IM",
    version="1.0.0",
    # Disable automatic docs in production to reduce attack surface
    docs_url="/v1/docs" if get_settings().app_env == "development" else None,
    redoc_url=None,
    lifespan=lifespan,
)

# Add Scalar API reference
if get_settings().app_env == "development":
    @app.get("/v1/scalar", include_in_schema=False)
    async def scalar_reference():
        return get_scalar_api_reference()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # tighten in production
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/", include_in_schema=False)
async def root():
    """Basic landing page; not authenticated.

    Returns structured JSON with service info, current mode, version, and
    helpful links when running in development.
    """
    settings = get_settings()
    base = {
        "service": "COMP3334 Secure IM",
        "status": "ok",
        "version": app.version,
        "timestamp": int(time.time()),
    }
    if settings.app_env == "development":
        # include dev-only helpers
        base["dev_endpoints"] = ["/v1/scalar", "/v1/docs"]
        base["environment"] = settings.app_env
    return base

# Register API routers
app.include_router(auth_router)
app.include_router(keys_router)
app.include_router(friends_router)
app.include_router(msg_router)
app.include_router(conv_router)


# ---------------------------------------------------------------------------
# WebSocket endpoint — authenticated via token query param
# ---------------------------------------------------------------------------

@app.websocket("/v1/ws")
async def ws_endpoint(websocket: WebSocket) -> None:
    """
    WebSocket connection. Client must pass ?token=<bearer_token> in the URL.
    We cannot use Authorization header in WebSocket handshake from most clients.
    """
    from fastapi import Request
    from starlette.datastructures import Headers

    token = websocket.query_params.get("token")
    if not token:
        await websocket.close(code=4001)
        return

    # Reuse require_auth logic by constructing a mock request
    from server.core.security import hash_token
    from server.core.database import get_db as _get_db

    token_hash = hash_token(token)
    now = int(time.time())
    db = await _get_db()
    async with db.execute(
        "SELECT s.user_id FROM sessions s "
        "WHERE s.token_hash = ? AND s.revoked = 0 AND s.expires_at > ?",
        (token_hash, now),
    ) as cur:
        row = await cur.fetchone()

    if row is None:
        await websocket.close(code=4001)
        return

    await websocket_endpoint(websocket, row["user_id"])


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Startup / shutdown handled by lifespan context manager above
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# R12 — TTL cleanup background task
# ---------------------------------------------------------------------------

async def _ttl_cleanup_loop() -> None:
    """
    Periodically delete expired messages from the server (R12).
    Also deletes messages older than MAX_MESSAGE_AGE_DAYS even without TTL.
    """
    settings = get_settings()
    while True:
        await asyncio.sleep(settings.ttl_cleanup_interval)
        try:
            db = await get_db()
            now = int(time.time())
            max_age_cutoff = now - (settings.max_message_age_days * 86400)

            # Delete TTL-expired messages
            result = await db.execute(
                "DELETE FROM messages WHERE expires_at IS NOT NULL AND expires_at <= ?",
                (now,),
            )
            ttl_deleted = result.rowcount

            # Delete messages older than max retention age
            result = await db.execute(
                "DELETE FROM messages WHERE stored_at < ?",
                (max_age_cutoff,),
            )
            age_deleted = result.rowcount

            await db.commit()

            if ttl_deleted or age_deleted:
                log.info("ttl_cleanup", ttl_deleted=ttl_deleted, age_deleted=age_deleted)
        except Exception as exc:
            log.warning("ttl_cleanup_error", error=str(exc))


if __name__ == "__main__":
    settings = get_settings()
    uvicorn.run(
        "server.main:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level,
        ssl_certfile=settings.tls_cert_file if settings.app_env == "production" else None,
        ssl_keyfile=settings.tls_key_file if settings.app_env == "production" else None,
    )
