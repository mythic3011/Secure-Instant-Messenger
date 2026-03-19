"""
client/api/client.py — httpx async HTTP client + WebSocket listener.
Handles all server communication for the client application.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import ssl
import warnings
from typing import Callable, Awaitable, Literal

import httpx
import websockets
import websockets.exceptions

from shared.protocol import (
    ConversationListResponse,
    DeliveryAck,
    FetchMessagesResponse,
    FriendRequestAction,
    FriendRequestCreate,
    FriendRequestOut,
    LoginRequest,
    LoginResponse,
    MessageEnvelope,
    PublicKeyBundle,
    RegisterRequest,
    RegisterResponse,
    SendMessageRequest,
    SendMessageResponse,
)

log = logging.getLogger(__name__)

MessageCallback = Callable[[dict], Awaitable[None]]


def _make_ssl_ctx(
    verify: bool = True,
    ca_cert: str | None = None,
) -> ssl.SSLContext:
    """
    Return an SSL context for WebSocket connections.

    Priority:
      1. ca_cert provided → load it as trusted CA (for self-signed certs)
      2. verify=False     → disable verification with deprecation warning
      3. default          → system CA bundle
    """
    ctx = ssl.create_default_context()
    if ca_cert:
        ctx.load_verify_locations(ca_cert)
    elif not verify:
        warnings.warn(
            "--no-verify-tls is insecure; use --ca-cert instead",
            stacklevel=2,
        )
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


class IMClient:
    def __init__(
        self,
        base_url: str,
        verify_tls: bool = True,
        ca_cert: str | None = None,
        pin_sha256: str | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token: str | None = None
        self._http: httpx.AsyncClient | None = None
        self._ws_task: asyncio.Task | None = None
        self._on_message: MessageCallback | None = None
        self._verify_tls = verify_tls
        self._ca_cert = ca_cert
        self._pin_sha256 = pin_sha256.lower() if pin_sha256 else None

    async def __aenter__(self) -> "IMClient":
        # httpx: use ca_cert path if provided, else fall back to verify_tls bool
        verify: bool | str = self._ca_cert if self._ca_cert else self._verify_tls
        self._http = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=10.0,
            verify=verify,
            trust_env=False,
        )
        return self

    async def __aexit__(self, *_) -> None:
        await self.disconnect()
        if self._http:
            await self._http.aclose()

    def _headers(self) -> dict:
        if self._token:
            return {"Authorization": f"Bearer {self._token}"}
        return {}

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        assert self._http is not None, "Use IMClient as async context manager"
        log.debug("→ %s %s", method.upper(), path)
        resp = await self._http.request(method, path, headers=self._headers(), **kwargs)
        log.debug("← %s %s %d (%d bytes)", method.upper(), path, resp.status_code, len(resp.content))
        if resp.status_code >= 400:
            log.warning("Request failed: %s %s → HTTP %d: %s", method.upper(), path, resp.status_code, resp.text[:200])
            raise IMClientError(resp.status_code, resp.text)
        return resp

    # Auth
    async def register(self, body: RegisterRequest) -> RegisterResponse:
        resp = await self._request("POST", "/v1/auth/register", json=body.model_dump())
        return RegisterResponse.model_validate(resp.json())

    async def login(self, body: LoginRequest) -> LoginResponse:
        resp = await self._request("POST", "/v1/auth/login", json=body.model_dump())
        result = LoginResponse.model_validate(resp.json())
        self._token = result.access_token
        return result

    async def logout(self) -> None:
        await self._request("POST", "/v1/auth/logout")
        self._token = None

    # Keys
    async def upload_keys(self, bundle: PublicKeyBundle) -> None:
        await self._request("POST", "/v1/keys/upload", json=bundle.model_dump())

    async def get_keys(self, username: str) -> PublicKeyBundle:
        resp = await self._request("GET", f"/v1/keys/{username}")
        return PublicKeyBundle.model_validate(resp.json())

    # Messages
    async def send_message(self, envelope: MessageEnvelope) -> SendMessageResponse:
        body = SendMessageRequest(envelope=envelope)
        resp = await self._request("POST", "/v1/messages", json=body.model_dump())
        return SendMessageResponse.model_validate(resp.json())

    async def fetch_messages(
        self,
        conversation_id: str,
        before_id: str | None = None,
        limit: int = 50,
    ) -> FetchMessagesResponse:
        params: dict = {"conversation_id": conversation_id, "limit": limit}
        if before_id:
            params["before_id"] = before_id
        resp = await self._request("GET", "/v1/messages", params=params)
        return FetchMessagesResponse.model_validate(resp.json())

    async def send_ack(self, ack: DeliveryAck) -> None:
        await self._request("POST", "/v1/messages/ack", json=ack.model_dump())

    # Friends
    async def send_friend_request(self, username: str) -> FriendRequestOut:
        body = FriendRequestCreate(recipient_username=username)
        resp = await self._request("POST", "/v1/friends/request", json=body.model_dump())
        return FriendRequestOut.model_validate(resp.json())

    async def list_pending_requests(self) -> list[FriendRequestOut]:
        resp = await self._request("GET", "/v1/friends/pending")
        return [FriendRequestOut.model_validate(r) for r in resp.json()]

    async def handle_friend_request(
        self,
        request_id: str,
        action: Literal["accept", "decline", "cancel"],
    ) -> None:
        body = FriendRequestAction(action=action)
        await self._request("PUT", f"/v1/friends/request/{request_id}", json=body.model_dump())

    async def remove_friend(self, peer_id: str) -> None:
        await self._request("DELETE", f"/v1/friends/{peer_id}")

    async def block_user(self, peer_id: str) -> None:
        await self._request("POST", f"/v1/friends/{peer_id}/block")

    # Conversations
    async def list_conversations(self) -> ConversationListResponse:
        resp = await self._request("GET", "/v1/conversations")
        return ConversationListResponse.model_validate(resp.json())

    async def mark_read(self, conversation_id: str) -> None:
        await self._request("POST", f"/v1/conversations/{conversation_id}/read")

    # WebSocket
    async def connect_ws(self, on_message: MessageCallback) -> None:
        self._on_message = on_message
        self._ws_task = asyncio.create_task(self._ws_loop())

    async def disconnect(self) -> None:
        if self._ws_task:
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass
            self._ws_task = None

    async def _verify_pin(self, ssl_object: ssl.SSLObject) -> None:
        """Verify the server cert matches the expected SHA256 pin."""
        cert_der = ssl_object.getpeercert(binary_form=True)
        if cert_der is None:
            raise ssl.SSLError("No peer certificate received")
        cert_hash = hashlib.sha256(cert_der).hexdigest()
        if cert_hash != self._pin_sha256:
            raise ssl.SSLError(
                f"Certificate pin mismatch: expected {self._pin_sha256}, got {cert_hash}"
            )

    async def _ws_loop(self) -> None:
        """WebSocket listener with automatic reconnect."""
        ws_url = self._base_url.replace("https://", "wss://").replace("http://", "ws://")
        ws_url = f"{ws_url}/v1/ws"
        use_tls = ws_url.startswith("wss://")
        ssl_ctx = _make_ssl_ctx(verify=self._verify_tls, ca_cert=self._ca_cert) if use_tls else None

        retry_count = 0
        while True:
            try:
                log.debug("WS connecting to %s (attempt #%d)", ws_url, retry_count + 1)
                async with websockets.connect(ws_url, ssl=ssl_ctx, proxy=None) as ws:
                    # Cert pin check after TLS handshake
                    if self._pin_sha256 and use_tls:
                        ssl_obj = ws.socket.getpeercert(binary_form=True)  # type: ignore[attr-defined]
                        if ssl_obj is not None:
                            cert_hash = hashlib.sha256(ssl_obj).hexdigest()
                            if cert_hash != self._pin_sha256:
                                raise ssl.SSLError(
                                    f"Certificate pin mismatch: expected {self._pin_sha256}, got {cert_hash}"
                                )
                    # First-frame auth: send token in the WebSocket payload,
                    # not the URL, to keep it out of server/proxy access logs.
                    await ws.send(json.dumps({"type": "auth", "token": self._token}))
                    retry_count = 0
                    log.info("WebSocket connected")
                    async for raw in ws:
                        try:
                            msg = json.loads(raw)
                            if msg.get("type") != "ping" and self._on_message:
                                await self._on_message(msg)
                        except json.JSONDecodeError as exc:
                            log.warning("WS JSON parse error: %s", exc)
                        except Exception as exc:
                            log.warning("WS handler error: %s", exc)
            except websockets.exceptions.ConnectionClosed as exc:
                log.info("WS closed (code=%s), reconnecting in 3s…", exc.code)
            except asyncio.CancelledError:
                return
            except Exception as exc:
                log.warning("WebSocket error (%s): %s", type(exc).__name__, exc)

            retry_count += 1
            log.debug("WS retry #%d in 3s", retry_count)
            await asyncio.sleep(3)


class IMClientError(Exception):
    def __init__(self, status_code: int, raw: str) -> None:
        self.status_code = status_code
        self.detail = self._parse_detail(status_code, raw)
        super().__init__(f"HTTP {status_code}: {self.detail}")

    @staticmethod
    def _parse_detail(status_code: int, raw: str) -> str:
        if not raw:
            return _HTTP_STATUS.get(status_code, f"HTTP {status_code}")
        try:
            body = json.loads(raw)
            if isinstance(body, dict):
                if isinstance(body.get("detail"), list):
                    msgs = [e.get("msg", str(e)) for e in body["detail"] if isinstance(e, dict)]
                    return "; ".join(msgs) if msgs else raw
                if isinstance(body.get("detail"), str):
                    return body["detail"]
        except (json.JSONDecodeError, KeyError, TypeError):
            pass
        return raw


_HTTP_STATUS: dict[int, str] = {
    400: "Bad request",
    401: "Unauthorised",
    403: "Forbidden",
    404: "Not found",
    409: "Conflict",
    422: "Validation error",
    429: "Too many requests — please wait",
    500: "Server error",
    502: "Cannot reach server (bad gateway)",
    503: "Server unavailable",
}