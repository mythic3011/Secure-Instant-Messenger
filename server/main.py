"""
server/main.py — FastAPI application entrypoint.
Wires up all routers, WebSocket endpoint, lifespan, graceful shutdown,
accurate TTL cleanup, and health/readiness probes.
"""

from __future__ import annotations

import asyncio
import os
import signal
import time
from contextlib import asynccontextmanager

import structlog
import uvicorn
from fastapi import FastAPI, WebSocket
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


# ── Lifespan ──────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    cleanup_task = asyncio.create_task(_ttl_cleanup_loop())
    log.info("server_started", port=get_settings().port)
    try:
        yield
    finally:
        # Cancel background task cleanly on shutdown
        cleanup_task.cancel()
        try:
            await cleanup_task
        except asyncio.CancelledError:
            pass
        await close_db()
        log.info("server_stopped")


# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="COMP3334 Secure IM",
    version="1.0.0",
    docs_url="/v1/docs" if get_settings().app_env == "development" else None,
    redoc_url=None,
    lifespan=lifespan,
)

if get_settings().app_env == "development":
    @app.get("/v1/scalar", include_in_schema=False)
    async def scalar_reference():
        return get_scalar_api_reference(
            openapi_url=app.openapi_url,
            title=app.title,
        )

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/", include_in_schema=False)
async def root():
    settings = get_settings()
    base = {
        "service": "COMP3334 Secure IM",
        "status": "ok",
        "version": app.version,
        "timestamp": int(time.time()),
    }
    if settings.app_env == "development":
        base["dev_endpoints"] = ["/v1/scalar", "/v1/docs"]
        base["environment"] = settings.app_env
    return base


# ── Health / readiness probes ─────────────────────────────────────────────────
@app.get("/health")
async def health() -> dict:
    """Liveness probe — returns ok if process is alive."""
    return {"status": "ok", "timestamp": int(time.time())}


@app.get("/ready")
async def ready():
    """
    Readiness probe — verifies DB is reachable.
    Returns HTTP 503 if DB check fails (container orchestrators use this
    to hold traffic until the server is truly ready).
    """
    try:
        db = await get_db()
        async with db.execute("SELECT 1") as cur:
            await cur.fetchone()
        return {"status": "ready", "timestamp": int(time.time())}
    except Exception as exc:
        log.warning("readiness_check_failed", error=str(exc))
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "error": str(exc)},
        )


# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(auth_router)
app.include_router(keys_router)
app.include_router(friends_router)
app.include_router(msg_router)
app.include_router(conv_router)


# ── WebSocket ─────────────────────────────────────────────────────────────────
@app.websocket("/v1/ws")
async def ws_endpoint(websocket: WebSocket) -> None:
    """
    WebSocket connection. Client passes ?token=<bearer_token> in the URL.
    Authorization header is not available in WS handshake on most clients.
    """
    token = websocket.query_params.get("token")
    if not token:
        await websocket.close(code=4001)
        return

    from server.core.security import hash_token

    token_hash = hash_token(token)
    now = int(time.time())
    db = await get_db()
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


# ── TTL cleanup background task (R12) ─────────────────────────────────────────
async def _ttl_cleanup_loop() -> None:
    """
    Accurately delete expired messages (R12).

    Uses drift-corrected sleep: subtracts DB query time from the interval
    so the schedule stays accurate even under load. Previous implementation
    used a fixed asyncio.sleep(300) which drifted by query duration each cycle.
    """
    settings = get_settings()
    interval = float(settings.ttl_cleanup_interval)

    while True:
        tick = time.monotonic()
        try:
            db = await get_db()
            now = int(time.time())
            max_age_cutoff = now - (settings.max_message_age_days * 86400)

            result = await db.execute(
                "DELETE FROM messages WHERE expires_at IS NOT NULL AND expires_at <= ?",
                (now,),
            )
            ttl_deleted = result.rowcount

            result = await db.execute(
                "DELETE FROM messages WHERE stored_at < ?",
                (max_age_cutoff,),
            )
            age_deleted = result.rowcount

            await db.commit()

            if ttl_deleted or age_deleted:
                log.info("ttl_cleanup", ttl_deleted=ttl_deleted, age_deleted=age_deleted)

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("ttl_cleanup_error", error=str(exc))

        # Drift-corrected: sleep only the remaining time in this interval
        elapsed = time.monotonic() - tick
        await asyncio.sleep(max(0.0, interval - elapsed))


# ── Entrypoint ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    settings = get_settings()

    # Auto-detect TLS: use SSL if cert files exist.
    # Docker entrypoint (scripts/docker-entrypoint.sh) auto-generates them.
    cert = settings.tls_cert_file
    key  = settings.tls_key_file
    use_tls = os.path.isfile(cert) and os.path.isfile(key)

    if use_tls:
        log.info("tls_enabled", cert=cert, key=key)
    else:
        log.warning(
            "tls_disabled",
            reason="cert/key not found — plain HTTP (dev only)",
            cert=cert,
            key=key,
        )

    uvicorn.run(
        "server.main:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level,
        ssl_certfile=cert if use_tls else None,
        ssl_keyfile=key  if use_tls else None,
    )