"""
client/api/client.py — httpx async HTTP client + WebSocket listener.
Handles all server communication for the client application.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Callable, Awaitable

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


class IMClient:
    """
    Async HTTP + WebSocket client for the COMP3334 IM server.

    Usage:
        client = IMClient("https://localhost:8443")
        await client.register(...)
        await client.login(...)
        await client.connect_ws(on_message_callback)
    """

    def __init__(self, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._token: str | None = None
        self._http: httpx.AsyncClient | None = None
        self._ws_task: asyncio.Task | None = None
        self._on_message: MessageCallback | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "IMClient":
        use_tls = self._base_url.startswith("https://")
        self._http = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=10.0,
            verify=use_tls,
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
        resp = await self._http.request(method, path, headers=self._headers(), **kwargs)
        if resp.status_code >= 400:
            raise IMClientError(resp.status_code, resp.text)
        return resp

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Keys
    # ------------------------------------------------------------------

    async def upload_keys(self, bundle: PublicKeyBundle) -> None:
        await self._request("POST", "/v1/keys/upload", json=bundle.model_dump())

    async def get_keys(self, username: str) -> PublicKeyBundle:
        resp = await self._request("GET", f"/v1/keys/{username}")
        return PublicKeyBundle.model_validate(resp.json())

    # ------------------------------------------------------------------
    # Messages
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Friends
    # ------------------------------------------------------------------

    async def send_friend_request(self, username: str) -> FriendRequestOut:
        body = FriendRequestCreate(recipient_username=username)
        resp = await self._request("POST", "/v1/friends/request", json=body.model_dump())
        return FriendRequestOut.model_validate(resp.json())

    async def list_pending_requests(self) -> list[FriendRequestOut]:
        resp = await self._request("GET", "/v1/friends/pending")
        return [FriendRequestOut.model_validate(r) for r in resp.json()]

    async def handle_friend_request(self, request_id: str, action: str) -> None:
        body = FriendRequestAction(action=action)
        await self._request("PUT", f"/v1/friends/request/{request_id}", json=body.model_dump())

    async def remove_friend(self, peer_id: str) -> None:
        await self._request("DELETE", f"/v1/friends/{peer_id}")

    async def block_user(self, peer_id: str) -> None:
        await self._request("POST", f"/v1/friends/{peer_id}/block")

    # ------------------------------------------------------------------
    # Conversations
    # ------------------------------------------------------------------

    async def list_conversations(self) -> ConversationListResponse:
        resp = await self._request("GET", "/v1/conversations")
        return ConversationListResponse.model_validate(resp.json())

    async def mark_read(self, conversation_id: str) -> None:
        await self._request("POST", f"/v1/conversations/{conversation_id}/read")

    # ------------------------------------------------------------------
    # WebSocket
    # ------------------------------------------------------------------

    async def connect_ws(self, on_message: MessageCallback) -> None:
        """
        Connect to the WebSocket endpoint and start listening in the background.
        on_message is called for every push received from the server.
        """
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

    async def _ws_loop(self) -> None:
        """WebSocket listener with automatic reconnect."""
        ws_url = self._base_url.replace("https://", "wss://").replace("http://", "ws://")
        ws_url = f"{ws_url}/v1/ws?token={self._token}"
        use_tls = ws_url.startswith("wss://")

        while True:
            try:
                async with websockets.connect(ws_url, ssl=use_tls) as ws:
                    log.info("WebSocket connected")
                    async for raw in ws:
                        try:
                            msg = json.loads(raw)
                            if self._on_message and msg.get("type") != "ping":
                                await self._on_message(msg)
                        except Exception as exc:
                            log.warning("WS message parse error: %s", exc)
            except websockets.exceptions.ConnectionClosed:
                log.info("WebSocket disconnected, reconnecting in 3s...")
            except asyncio.CancelledError:
                return
            except Exception as exc:
                log.warning("WebSocket error: %s", exc)

            await asyncio.sleep(3)


class IMClientError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"HTTP {status_code}: {detail}")
