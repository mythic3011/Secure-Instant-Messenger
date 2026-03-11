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
        log.debug("→ %s %s", method.upper(), path)
        resp = await self._http.request(method, path, headers=self._headers(), **kwargs)
        log.debug("← %s %s %d (%d bytes)", method.upper(), path, resp.status_code, len(resp.content))
        if resp.status_code >= 400:
            log.warning("Request failed: %s %s → HTTP %d: %s", method.upper(), path, resp.status_code, resp.text[:200])
            raise IMClientError(resp.status_code, resp.text)
        return resp

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    async def register(self, body: RegisterRequest) -> RegisterResponse:
        log.info("Registering user '%s'", body.username)
        resp = await self._request("POST", "/v1/auth/register", json=body.model_dump())
        result = RegisterResponse.model_validate(resp.json())
        log.info("Registration successful for user '%s'", body.username)
        return result

    async def login(self, body: LoginRequest) -> LoginResponse:
        log.info("Logging in as '%s'", body.username)
        resp = await self._request("POST", "/v1/auth/login", json=body.model_dump())
        result = LoginResponse.model_validate(resp.json())
        self._token = result.access_token
        log.info("Login successful for user '%s'", body.username)
        return result

    async def logout(self) -> None:
        log.info("Logging out")
        await self._request("POST", "/v1/auth/logout")
        self._token = None
        log.info("Logged out; token cleared")

    # ------------------------------------------------------------------
    # Keys
    # ------------------------------------------------------------------

    async def upload_keys(self, bundle: PublicKeyBundle) -> None:
        log.info("Uploading public key bundle")
        await self._request("POST", "/v1/keys/upload", json=bundle.model_dump())
        log.debug("Public key bundle uploaded successfully")

    async def get_keys(self, username: str) -> PublicKeyBundle:
        log.debug("Fetching public keys for user '%s'", username)
        resp = await self._request("GET", f"/v1/keys/{username}")
        result = PublicKeyBundle.model_validate(resp.json())
        log.debug("Received public key bundle for user '%s'", username)
        return result

    # ------------------------------------------------------------------
    # Messages
    # ------------------------------------------------------------------

    async def send_message(self, envelope: MessageEnvelope) -> SendMessageResponse:
        log.info("Sending message in conversation '%s'", envelope.conversation_id)
        body = SendMessageRequest(envelope=envelope)
        resp = await self._request("POST", "/v1/messages", json=body.model_dump())
        result = SendMessageResponse.model_validate(resp.json())
        log.debug("Message sent; server message_id=%s", getattr(result, 'message_id', '?'))
        return result

    async def fetch_messages(
        self,
        conversation_id: str,
        before_id: str | None = None,
        limit: int = 50,
    ) -> FetchMessagesResponse:
        log.debug("Fetching messages for conversation '%s' (limit=%d, before_id=%s)",
                  conversation_id, limit, before_id)
        params: dict = {"conversation_id": conversation_id, "limit": limit}
        if before_id:
            params["before_id"] = before_id
        resp = await self._request("GET", "/v1/messages", params=params)
        result = FetchMessagesResponse.model_validate(resp.json())
        log.debug("Fetched %d messages for conversation '%s'",
                  len(getattr(result, 'messages', [])), conversation_id)
        return result

    async def send_ack(self, ack: DeliveryAck) -> None:
        log.debug("Sending delivery ack for message_id=%s", ack.message_id)
        await self._request("POST", "/v1/messages/ack", json=ack.model_dump())

    # ------------------------------------------------------------------
    # Friends
    # ------------------------------------------------------------------

    async def send_friend_request(self, username: str) -> FriendRequestOut:
        log.info("Sending friend request to '%s'", username)
        body = FriendRequestCreate(recipient_username=username)
        resp = await self._request("POST", "/v1/friends/request", json=body.model_dump())
        result = FriendRequestOut.model_validate(resp.json())
        log.info("Friend request sent to '%s' (request_id=%s)", username, getattr(result, 'id', '?'))
        return result

    async def list_pending_requests(self) -> list[FriendRequestOut]:
        log.debug("Listing pending friend requests")
        resp = await self._request("GET", "/v1/friends/pending")
        results = [FriendRequestOut.model_validate(r) for r in resp.json()]
        log.debug("Found %d pending friend request(s)", len(results))
        return results

    async def handle_friend_request(self, request_id: str, action: str) -> None:
        log.info("Handling friend request request_id=%s action=%s", request_id, action)
        body = FriendRequestAction(action=action)
        await self._request("PUT", f"/v1/friends/request/{request_id}", json=body.model_dump())
        log.info("Friend request %s → action '%s' applied", request_id, action)

    async def remove_friend(self, peer_id: str) -> None:
        log.info("Removing friend peer_id=%s", peer_id)
        await self._request("DELETE", f"/v1/friends/{peer_id}")
        log.info("Friend peer_id=%s removed", peer_id)

    async def block_user(self, peer_id: str) -> None:
        log.info("Blocking user peer_id=%s", peer_id)
        await self._request("POST", f"/v1/friends/{peer_id}/block")
        log.info("User peer_id=%s blocked", peer_id)

    # ------------------------------------------------------------------
    # Conversations
    # ------------------------------------------------------------------

    async def list_conversations(self) -> ConversationListResponse:
        log.debug("Listing conversations")
        resp = await self._request("GET", "/v1/conversations")
        result = ConversationListResponse.model_validate(resp.json())
        log.debug("Fetched %d conversation(s)", len(getattr(result, 'conversations', [])))
        return result

    async def mark_read(self, conversation_id: str) -> None:
        log.debug("Marking conversation '%s' as read", conversation_id)
        await self._request("POST", f"/v1/conversations/{conversation_id}/read")

    # ------------------------------------------------------------------
    # WebSocket
    # ------------------------------------------------------------------

    async def connect_ws(self, on_message: MessageCallback) -> None:
        """
        Connect to the WebSocket endpoint and start listening in the background.
        on_message is called for every push received from the server.
        """
        log.info("Starting WebSocket listener")
        self._on_message = on_message
        self._ws_task = asyncio.create_task(self._ws_loop())

    async def disconnect(self) -> None:
        if self._ws_task:
            log.info("Disconnecting WebSocket")
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass
            self._ws_task = None
            log.info("WebSocket disconnected")

    async def _ws_loop(self) -> None:
        """WebSocket listener with automatic reconnect."""
        ws_url = self._base_url.replace("https://", "wss://").replace("http://", "ws://")
        ws_url = f"{ws_url}/v1/ws?token={self._token}"
        use_tls = ws_url.startswith("wss://")

        retry_count = 0
        while True:
            try:
                log.debug("WS connecting to %s (attempt #%d)", ws_url.split('?')[0], retry_count + 1)
                async with websockets.connect(ws_url, ssl=use_tls) as ws:
                    retry_count = 0
                    log.info("WebSocket connected")
                    async for raw in ws:
                        try:
                            msg = json.loads(raw)
                            msg_type = msg.get("type", "unknown")
                            if msg_type == "ping":
                                log.debug("WS ping received")
                            else:
                                log.debug("WS message received: type=%s", msg_type)
                                if self._on_message:
                                    await self._on_message(msg)
                        except json.JSONDecodeError as exc:
                            log.warning("WS message JSON parse error: %s", exc)
                        except Exception as exc:
                            log.warning("WS message handler error: %s", exc)
            except websockets.exceptions.ConnectionClosed as exc:
                log.info("WebSocket connection closed (code=%s), reconnecting in 3s...", exc.code)
            except asyncio.CancelledError:
                log.debug("WS listener cancelled")
                return
            except Exception as exc:
                log.warning("WebSocket error (%s): %s", type(exc).__name__, exc)

            retry_count += 1
            log.debug("WS retry #%d in 3s", retry_count)
            await asyncio.sleep(3)


class IMClientError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"HTTP {status_code}: {detail}")
