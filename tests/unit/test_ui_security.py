from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest
from textual.css.query import NoMatches

from client.api.client import IMClientConnectionError, IMClientError
from client.crypto.session import (
    IdentityKeyCache,
    KeyChangeWarning,
    LocalStorageSecurityError,
)
from client.ui import app as app_module
from client.ui.app import IMApp
from client.ui.contracts import (
    ChatHistoryResult,
    ConversationSummaryViewModel,
    SendResult,
    TrustViewModel,
    UIBanner,
    UIScreenState,
)
from client.ui.screens.chat import ChatScreen
from client.ui.screens.conversations import ConversationItem, ConversationListScreen
from client.ui.screens.friends import FriendsScreen
from client.ui.screens.login import LoginScreen
from client.ui.screens.settings import SettingsScreen
from client.use_cases.friends import FriendRequestHandled
from client.use_cases.login import LoginSucceeded
from shared.protocol import FriendRequestStatus
from tests.secrets import TEST_ACCOUNT_PASSWORD


class _FakeStatic:
    def __init__(self) -> None:
        self.value = ""
        self.classes: set[str] = set()

    def update(self, value: str) -> None:
        self.value = value

    def add_class(self, *classes: str) -> None:
        self.classes.update(classes)

    def remove_class(self, *classes: str) -> None:
        for class_name in classes:
            self.classes.discard(class_name)


class _FakeButton:
    def __init__(self, button_id: str) -> None:
        self.id = button_id
        self.disabled = False


class _FakeListView:
    def __init__(self) -> None:
        self.items: list[object] = []
        self.scrolled = False

    def clear(self) -> None:
        self.items.clear()

    def append(self, item: object) -> None:
        self.items.append(item)

    def scroll_end(self, animate: bool = False) -> None:
        self.scrolled = True


class _FakeFriendsScreen:
    def __init__(self) -> None:
        self.pending_payloads: list[list[dict]] = []
        self.error = ""
        self.status = ""

    def populate_pending(self, requests: list[dict]) -> None:
        self.pending_payloads.append(requests)

    def show_error(self, msg: str) -> None:
        self.error = msg
        self.status = ""

    def show_status(self, msg: str) -> None:
        self.status = msg
        self.error = ""


class _FakeLoginScreen:
    def __init__(self) -> None:
        self.error = _FakeStatic()

    def query_one(self, selector: str, *_args, **_kwargs):
        if selector == "#error":
            return self.error
        msg = f"unexpected selector: {selector}"
        raise KeyError(msg)


class _FakeRegisterScreen:
    def __init__(self, *, query_error: Exception | None = None) -> None:
        self.error = _FakeStatic()
        self._query_error = query_error
        self.totp_uri: str | None = None

    def query_one(self, selector: str, *_args, **_kwargs):
        if selector == "#error":
            if self._query_error is not None:
                raise self._query_error
            return self.error
        msg = f"unexpected selector: {selector}"
        raise KeyError(msg)

    def show_totp_setup(self, totp_uri: str) -> None:
        self.totp_uri = totp_uri


def _patch_chat_widgets(
    monkeypatch: pytest.MonkeyPatch,
    chat: ChatScreen,
) -> tuple[_FakeStatic, _FakeListView, _FakeButton]:
    warning = _FakeStatic()
    messages = _FakeListView()
    send_button = _FakeButton("btn_send")
    widgets = {
        "#warning": warning,
        "#messages": messages,
        "#btn_send": send_button,
    }
    monkeypatch.setattr(chat, "query_one", lambda selector, *_args, **_kwargs: widgets[selector])
    return warning, messages, send_button


def _patch_settings_widgets(
    monkeypatch: pytest.MonkeyPatch, settings: SettingsScreen
) -> dict[str, _FakeStatic]:
    widgets = {
        "#fingerprint": _FakeStatic(),
        "#trust_state": _FakeStatic(),
        "#trust_badge": _FakeStatic(),
        "#trust_hint": _FakeStatic(),
        "#action_hint": _FakeStatic(),
        "#status": _FakeStatic(),
    }
    monkeypatch.setattr(
        settings, "query_one", lambda selector, *_args, **_kwargs: widgets[selector]
    )
    return widgets


def _imclient_error(status_code: int, detail: str) -> IMClientError:
    return IMClientError(status_code, f'{{"detail":"{detail}"}}')


def _as_any(value: object) -> Any:
    return cast(Any, value)


def _identity_cache_raw(value: dict[str, dict[str, object]]) -> dict[str, object]:
    return cast(dict[str, object], value)


@pytest.mark.asyncio
async def test_chat_history_local_decrypt_failure_is_handled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = IMApp("https://example.test", "alice")
    warning_events: list[tuple[tuple, dict]] = []

    async def _raise_get_messages(*_args, **_kwargs):
        raise LocalStorageSecurityError("broken local history")

    monkeypatch.setattr("client.state.store.get_messages", _raise_get_messages)
    monkeypatch.setattr(
        app_module.log,
        "warning",
        lambda *args, **kwargs: warning_events.append((args, kwargs)),
    )

    result = await app.load_chat_history_state("conv-1")

    chat = ChatScreen("conv-1", "bob", "bob", "alice")
    warning_widget, messages, send_button = _patch_chat_widgets(monkeypatch, chat)
    chat.render_history_result(result)

    assert result.state.kind == "degraded"
    assert result.state.primary_banner is not None
    assert result.state.primary_banner.code == "local_history_unavailable"
    assert warning_events
    assert "Local chat history is unavailable" in warning_widget.value
    assert len(messages.items) == 1
    assert send_button.disabled is False


@pytest.mark.asyncio
async def test_send_message_missing_storage_key_is_handled(monkeypatch: pytest.MonkeyPatch) -> None:
    app = IMApp("https://example.test", "alice")
    app._client = SimpleNamespace(send_message=None)  # type: ignore[assignment]
    app._user_id = "alice"
    warning_events: list[tuple[tuple, dict]] = []

    async def _send_message(_envelope) -> None:
        return None

    async def _ensure_session(*_args, **_kwargs):
        return SimpleNamespace(send_chain=object())

    async def _raise_save_message(**_kwargs) -> None:
        raise LocalStorageSecurityError("no storage key")

    monkeypatch.setattr(app._client, "send_message", _send_message)
    monkeypatch.setattr(app, "_ensure_session", _ensure_session)
    monkeypatch.setattr(
        app_module,
        "build_and_encrypt",
        lambda **_kwargs: SimpleNamespace(id="msg-1", eph_pub_b64=None, conv_dh_pub_b64=None),
    )
    monkeypatch.setattr(app_module, "save_message", _raise_save_message)
    monkeypatch.setattr(
        app_module.log,
        "warning",
        lambda *args, **kwargs: warning_events.append((args, kwargs)),
    )

    result = await app.send_message_state(
        conversation_id="conv-1",
        peer_id="bob",
        plaintext="hello",
    )

    chat = ChatScreen("conv-1", "bob", "bob", "alice")
    warning_widget, messages, send_button = _patch_chat_widgets(monkeypatch, chat)
    chat.apply_send_result(result)

    assert result.ok is False
    assert result.state.kind == "blocked"
    assert result.state.primary_banner is not None
    assert result.state.primary_banner.code == "local_storage_unavailable"
    assert warning_events
    assert "Local secure storage is unavailable" in warning_widget.value
    assert messages.items == []
    assert send_button.disabled is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("exc_factory", "expected_code", "expected_message"),
    [
        (
            lambda: _imclient_error(409, "Peer key unavailable"),
            "server_error",
            "Peer key unavailable",
        ),
        (
            lambda: httpx.ConnectError("offline"),
            "network_unavailable",
            "The server is unreachable. Retry when the connection recovers.",
        ),
    ],
)
async def test_send_message_session_failure_is_surfaces_truthfully(
    monkeypatch: pytest.MonkeyPatch,
    exc_factory,
    expected_code: str,
    expected_message: str,
) -> None:
    app = IMApp("https://example.test", "alice")
    app._client = SimpleNamespace(send_message=None)  # type: ignore[assignment]
    app._user_id = "alice"

    async def _raise_session(*_args, **_kwargs):
        raise exc_factory()

    monkeypatch.setattr(app, "_ensure_session", _raise_session)

    result = await app.send_message_state(
        conversation_id="conv-1",
        peer_id="bob",
        plaintext="hello",
    )

    assert result.ok is False
    assert result.state.primary_banner is not None
    assert result.state.primary_banner.code == expected_code
    assert result.state.primary_banner.message == expected_message
    assert result.state.primary_banner.code != "message_send_failed"


@pytest.mark.asyncio
async def test_ensure_session_propagates_key_fetch_failure() -> None:
    app = IMApp("https://example.test", "alice")
    app._local_keys = SimpleNamespace(identity_kp=object(), dh_kp=object())  # type: ignore[assignment]
    app._peer_usernames["bob-id"] = "bob"

    async def _raise_get_keys(_username: str):
        raise _imclient_error(404, "Peer key unavailable")

    app._client = _as_any(SimpleNamespace(get_keys=_raise_get_keys))

    with pytest.raises(IMClientError, match="Peer key unavailable"):
        await app._ensure_session("conv-1", "bob-id")


@pytest.mark.asyncio
async def test_security_ui_paths_do_not_silently_swallow(monkeypatch: pytest.MonkeyPatch) -> None:
    app = IMApp("https://example.test", "alice")
    app._client = _as_any(SimpleNamespace(send_message=None))
    app._user_id = "alice"
    warnings: list[tuple[tuple, dict]] = []

    async def _raise_get_messages(*_args, **_kwargs):
        raise LocalStorageSecurityError("history unavailable")

    async def _send_message(_envelope) -> None:
        return None

    async def _ensure_session(*_args, **_kwargs):
        return SimpleNamespace(send_chain=object())

    async def _raise_save_message(**_kwargs) -> None:
        raise LocalStorageSecurityError("key unavailable")

    monkeypatch.setattr("client.state.store.get_messages", _raise_get_messages)
    monkeypatch.setattr(app._client, "send_message", _send_message)
    monkeypatch.setattr(app, "_ensure_session", _ensure_session)
    monkeypatch.setattr(
        app_module,
        "build_and_encrypt",
        lambda **_kwargs: SimpleNamespace(id="msg-1", eph_pub_b64=None, conv_dh_pub_b64=None),
    )
    monkeypatch.setattr(app_module, "save_message", _raise_save_message)
    monkeypatch.setattr(
        app_module.log,
        "warning",
        lambda *args, **kwargs: warnings.append((args, kwargs)),
    )

    history_result = await app.load_chat_history_state("conv-1")
    send_result = await app.send_message_state(
        conversation_id="conv-1",
        peer_id="bob",
        plaintext="hello",
    )

    assert warnings
    assert history_result.state.primary_banner is not None
    assert send_result.state.primary_banner is not None
    assert history_result.messages == ()
    assert send_result.state.kind == "blocked"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("exc_factory", "expected_error"),
    [
        (
            lambda: _imclient_error(409, "Still pending"),
            "Still pending",
        ),
        (
            lambda: httpx.ConnectError("offline"),
            "Cannot reach server. Pending requests unavailable.",
        ),
    ],
)
async def test_friends_pending_load_failure_is_shown(
    monkeypatch: pytest.MonkeyPatch,
    exc_factory,
    expected_error: str,
) -> None:
    app = IMApp("https://example.test", "alice")
    screen = _FakeFriendsScreen()
    app._screen_stack.append(screen)  # type: ignore[attr-defined]

    async def _raise_pending(*_args, **_kwargs):
        raise exc_factory()

    app._client = _as_any(
        SimpleNamespace(
            list_pending_requests=_raise_pending,
        )
    )

    await app.on_friends_screen__load_pending(SimpleNamespace())

    assert screen.pending_payloads == []
    assert screen.error == expected_error


@pytest.mark.asyncio
async def test_friends_pending_load_ignores_screen_change_after_await(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = IMApp("https://example.test", "alice")
    friends_screen = _FakeFriendsScreen()
    app._screen_stack.append(friends_screen)  # type: ignore[attr-defined]

    async def _load_pending(_context) -> app_module.PendingRequestsLoaded:
        app._screen_stack.append(_FakeLoginScreen())  # type: ignore[attr-defined]
        return app_module.PendingRequestsLoaded(requests=[{"id": "r1", "sender_name": "bob"}])

    monkeypatch.setattr(app_module, "execute_load_pending_requests", _load_pending)
    app._client = _as_any(SimpleNamespace())

    await app.on_friends_screen__load_pending(SimpleNamespace())

    assert friends_screen.pending_payloads == []
    assert friends_screen.error == ""
    assert friends_screen.status == ""


@pytest.mark.asyncio
async def test_friends_pending_load_propagates_unexpected_screen_contract_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = IMApp("https://example.test", "alice")

    class _BrokenFriendsScreen(_FakeFriendsScreen):
        def populate_pending(self, requests: list[dict]) -> None:
            raise RuntimeError("broken friends screen")

    screen = _BrokenFriendsScreen()
    app._screen_stack.append(screen)  # type: ignore[attr-defined]

    async def _load_pending(_context) -> app_module.PendingRequestsLoaded:
        return app_module.PendingRequestsLoaded(requests=[{"id": "r1", "sender_name": "bob"}])

    monkeypatch.setattr(app_module, "execute_load_pending_requests", _load_pending)
    app._client = _as_any(SimpleNamespace())

    with pytest.raises(RuntimeError, match="broken friends screen"):
        await app.on_friends_screen__load_pending(SimpleNamespace())


async def test_register_request_logs_and_returns_on_expected_error_widget_miss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = IMApp("https://example.test", "alice")
    warnings: list[tuple[tuple, dict]] = []
    screen = _FakeRegisterScreen(query_error=NoMatches("missing #error"))
    app._screen_stack.append(screen)  # type: ignore[attr-defined]

    class _FakeClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args) -> None:
            return None

        async def register(self, _body):
            raise _imclient_error(409, "Still pending")

    monkeypatch.setattr(app_module, "IMClient", _FakeClient)
    monkeypatch.setattr(app_module, "save_keystore", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        app_module.log,
        "warning",
        lambda *args, **kwargs: warnings.append((args, kwargs)),
    )

    await app.on_register_screen_register_request(
        SimpleNamespace(username="alice", password=TEST_ACCOUNT_PASSWORD)
    )

    assert screen.error.value == ""
    assert any(args and args[0] == "register_error_render_failed" for args, _ in warnings)


@pytest.mark.asyncio
async def test_register_request_propagates_unexpected_error_widget_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = IMApp("https://example.test", "alice")
    screen = _FakeRegisterScreen(query_error=RuntimeError("boom"))
    app._screen_stack.append(screen)  # type: ignore[attr-defined]

    class _FakeClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args) -> None:
            return None

        async def register(self, _body):
            raise _imclient_error(409, "Still pending")

    monkeypatch.setattr(app_module, "IMClient", _FakeClient)
    monkeypatch.setattr(app_module, "save_keystore", lambda *_args, **_kwargs: None)

    with pytest.raises(RuntimeError, match="boom"):
        await app.on_register_screen_register_request(
            SimpleNamespace(username="alice", password=TEST_ACCOUNT_PASSWORD)
        )


@pytest.mark.asyncio
async def test_register_request_propagates_unexpected_register_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = IMApp("https://example.test", "alice")
    screen = _FakeRegisterScreen()
    app._screen_stack.append(screen)  # type: ignore[attr-defined]

    class _FakeClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args) -> None:
            return None

        async def register(self, _body):
            raise RuntimeError("unexpected register bug")

    monkeypatch.setattr(app_module, "IMClient", _FakeClient)
    monkeypatch.setattr(app_module, "save_keystore", lambda *_args, **_kwargs: None)

    with pytest.raises(RuntimeError, match="unexpected register bug"):
        await app.on_register_screen_register_request(
            SimpleNamespace(username="alice", password=TEST_ACCOUNT_PASSWORD)
        )

    assert screen.error.value == ""


async def test_logout_propagates_unexpected_client_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = IMApp("https://example.test", "alice")
    exited: list[tuple[object, ...]] = []
    popped: list[bool] = []

    async def _raise_logout() -> None:
        raise ValueError("unexpected bug")

    async def _noop_aexit(*_args) -> None:
        exited.append(_args)
        return None

    app._client = _as_any(SimpleNamespace(logout=_raise_logout, __aexit__=_noop_aexit))
    monkeypatch.setattr(app, "pop_screen", lambda: popped.append(True))

    with pytest.raises(ValueError, match="unexpected bug"):
        await app.on_conversation_list_screen_logout(ConversationListScreen.Logout())

    assert exited == [(None, None, None)]
    assert app._client is None
    assert popped == []


def test_banner_priority_prefers_key_changed() -> None:
    app = IMApp("https://example.test", "alice")
    network = UIBanner(
        code="network_unavailable",
        severity="warning",
        title="Offline",
        message="Server is unreachable.",
    )
    key_changed = UIBanner(
        code="key_changed",
        severity="error",
        title="Identity key changed",
        message="Re-verify this contact before trusting new messages.",
        persistent=True,
        dismissible=False,
    )

    state = app.build_screen_state("blocked", network, key_changed, disabled_actions=("send",))

    assert state.primary_banner == key_changed
    assert state.banners == (key_changed, network)
    assert state.disabled_actions == ("send",)


def test_key_changed_blocks_send_and_keeps_draft(monkeypatch: pytest.MonkeyPatch) -> None:
    warning = _FakeStatic()
    send_button = _FakeButton("btn_send")

    class _FakeInput:
        def __init__(self, value: str) -> None:
            self.value = value

    fake_input = _FakeInput("hello")
    chat = ChatScreen("conv-1", "bob", "bob", "alice")
    widgets = {"#warning": warning, "#msg_input": fake_input, "#btn_send": send_button}
    monkeypatch.setattr(chat, "query_one", lambda selector, *_args, **_kwargs: widgets[selector])

    result = SendResult(
        ok=False,
        state=UIScreenState(
            kind="blocked",
            disabled_actions=("send",),
            primary_banner=UIBanner(
                code="key_changed",
                severity="error",
                title="Identity key changed",
                message="Verify the fingerprint before trusting new messages.",
                persistent=True,
                dismissible=False,
            ),
        ),
    )

    chat.apply_send_result(result)

    assert send_button.disabled is True
    assert fake_input.value == "hello"
    assert "Identity key changed" in warning.value


def test_key_changed_trust_state_builds_blocked_chat_state() -> None:
    app = IMApp("https://example.test", "alice")
    state = app.build_screen_state("blocked", app_module.KEY_CHANGED_BANNER)

    assert state.kind == "blocked"
    assert state.disabled_actions == ("send",)


def test_chat_send_does_not_clear_draft_before_result(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeInput:
        def __init__(self, value: str) -> None:
            self.value = value

    posted: list[object] = []
    fake_input = _FakeInput("hello")
    chat = ChatScreen("conv-1", "bob", "bob", "alice")

    monkeypatch.setattr(chat, "query_one", lambda selector, *_args, **_kwargs: fake_input)
    monkeypatch.setattr(chat, "post_message", lambda message: posted.append(message))

    chat._send()

    assert fake_input.value == "hello"
    assert len(posted) == 1


def test_chat_apply_send_result_clears_draft_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeInput:
        def __init__(self, value: str) -> None:
            self.value = value

    warning = _FakeStatic()
    send_button = _FakeButton("btn_send")
    fake_input = _FakeInput("hello")
    chat = ChatScreen("conv-1", "bob", "bob", "alice")
    widgets = {"#warning": warning, "#msg_input": fake_input, "#btn_send": send_button}
    monkeypatch.setattr(chat, "query_one", lambda selector, *_args, **_kwargs: widgets[selector])

    result = SendResult(ok=True, state=UIScreenState(kind="ok"))
    chat.apply_send_result(result)

    assert fake_input.value == ""
    assert send_button.disabled is False


def test_chat_apply_send_result_disables_send_when_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeInput:
        def __init__(self, value: str) -> None:
            self.value = value

    warning = _FakeStatic()
    send_button = _FakeButton("btn_send")
    fake_input = _FakeInput("hello")
    chat = ChatScreen("conv-1", "bob", "bob", "alice")
    widgets = {"#warning": warning, "#msg_input": fake_input, "#btn_send": send_button}
    monkeypatch.setattr(chat, "query_one", lambda selector, *_args, **_kwargs: widgets[selector])

    result = SendResult(
        ok=False,
        state=UIScreenState(
            kind="blocked",
            disabled_actions=("send",),
            primary_banner=UIBanner(
                code="local_storage_unavailable",
                severity="error",
                title="Secure storage unavailable",
                message="Local secure storage is unavailable. Message was not sent.",
                persistent=True,
                dismissible=False,
            ),
        ),
    )
    chat.apply_send_result(result)

    assert fake_input.value == "hello"
    assert send_button.disabled is True
    assert "Secure storage unavailable" in warning.value


def test_chat_render_empty_history_shows_placeholder(monkeypatch: pytest.MonkeyPatch) -> None:
    warning = _FakeStatic()
    messages = _FakeListView()
    send_button = _FakeButton("btn_send")
    chat = ChatScreen("conv-1", "bob", "bob", "alice")
    widgets = {"#warning": warning, "#messages": messages, "#btn_send": send_button}
    monkeypatch.setattr(chat, "query_one", lambda selector, *_args, **_kwargs: widgets[selector])

    chat.render_history_result(ChatHistoryResult(state=UIScreenState(kind="empty"), messages=()))

    assert len(messages.items) == 1
    assert warning.value == ""
    assert send_button.disabled is False


def test_chat_render_degraded_history_keeps_banner_and_placeholder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    warning = _FakeStatic()
    messages = _FakeListView()
    send_button = _FakeButton("btn_send")
    chat = ChatScreen("conv-1", "bob", "bob", "alice")
    widgets = {"#warning": warning, "#messages": messages, "#btn_send": send_button}
    monkeypatch.setattr(chat, "query_one", lambda selector, *_args, **_kwargs: widgets[selector])

    chat.render_history_result(
        ChatHistoryResult(
            state=UIScreenState(
                kind="degraded",
                primary_banner=UIBanner(
                    code="local_history_unavailable",
                    severity="warning",
                    title="Local history unavailable",
                    message="Local chat history is unavailable on this device.",
                ),
            ),
            messages=(),
        )
    )

    assert len(messages.items) == 1
    assert "Local history unavailable" in warning.value


def test_settings_shows_verified_state(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = SettingsScreen("conv-1", "bob")
    widgets = _patch_settings_widgets(monkeypatch, settings)

    settings.set_trust_view_model(
        TrustViewModel(
            fingerprint="fp",
            verified=True,
            key_changed=False,
            requires_action=False,
            last_verified_at=None,
        )
    )

    assert widgets["#fingerprint"].value == "fp"
    assert widgets["#trust_badge"].value == "Verified"
    assert widgets["#trust_state"].value == "Key stable"
    assert (
        widgets["#trust_hint"].value
        == "This contact is verified. Compare fingerprints again after any key change."
    )
    assert widgets["#action_hint"].value == "No action required."


def test_settings_shows_key_changed_state(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = SettingsScreen("conv-1", "bob")
    widgets = _patch_settings_widgets(monkeypatch, settings)

    settings.set_trust_view_model(
        TrustViewModel(
            fingerprint="fp",
            verified=True,
            key_changed=True,
            requires_action=True,
            last_verified_at=None,
            banner=UIBanner(
                code="key_changed",
                severity="error",
                title="Identity key changed",
                message="verify again",
                persistent=True,
                dismissible=False,
            ),
        )
    )

    assert widgets["#trust_badge"].value == "Action required"
    assert widgets["#trust_state"].value == "Verified · Key changed"
    assert widgets["#trust_hint"].value == "verify again"
    assert widgets["#action_hint"].value == "Re-verify this contact before trusting new messages."


def test_conversation_item_renders_security_indicator() -> None:
    item = ConversationItem(
        ConversationSummaryViewModel(
            conv_id="conv-1",
            peer_id="bob-id",
            peer_username="bob",
            unread_count=2,
            requires_action=True,
        )
    )

    assert item.render_primary_line() == "bob"
    assert item.render_status_line() == "Security review required  •  2 unread"


def test_conversation_item_keeps_unread_and_security_as_separate_cues() -> None:
    item = ConversationItem(
        ConversationSummaryViewModel(
            conv_id="conv-1",
            peer_id="bob-id",
            peer_username="bob",
            unread_count=3,
            requires_action=True,
            primary_banner=UIBanner(
                code="key_changed",
                severity="error",
                title="Identity key changed",
                message="Verify this contact again.",
                persistent=True,
                dismissible=False,
            ),
        )
    )

    assert item.render_status_line() == "Security review required  •  3 unread"
    assert item.render_secondary_line() == "Identity key changed"


def test_conversation_item_renders_unread_without_warning() -> None:
    item = ConversationItem(
        ConversationSummaryViewModel(
            conv_id="conv-2",
            peer_id="alice-id",
            peer_username="alice",
            unread_count=1,
            requires_action=False,
        )
    )

    assert item.render_status_line() == "1 unread"
    assert item.render_secondary_line() == "No security actions pending"


def test_conversation_summary_refresh_updates_banner_and_secondary_text_together() -> None:
    screen = ConversationListScreen("alice")
    item = ConversationItem(
        ConversationSummaryViewModel(
            conv_id="conv-2",
            peer_id="alice-id",
            peer_username="alice",
            unread_count=0,
            requires_action=False,
        )
    )
    name = _FakeStatic()
    status = _FakeStatic()
    detail = _FakeStatic()
    item.query_one = lambda selector, *_args, **_kwargs: {
        ".conversation_name": name,
        ".conversation_status": status,
        ".conversation_detail": detail,
    }[selector]  # type: ignore[method-assign]
    screen._items = {"conv-2": item}

    screen.refresh_conversation(
        "conv-2",
        0,
        requires_action=True,
        primary_banner=UIBanner(
            code="key_changed",
            severity="error",
            title="Identity key changed",
            message="Verify again.",
            persistent=True,
            dismissible=False,
        ),
    )

    assert status.value == "Security review required"
    assert detail.value == "Identity key changed"


def test_conversation_list_summary_reports_counts() -> None:
    screen = ConversationListScreen("alice")
    conversations = [
        ConversationSummaryViewModel(
            conv_id="conv-1",
            peer_id="bob-id",
            peer_username="bob",
            unread_count=3,
            requires_action=False,
        ),
        ConversationSummaryViewModel(
            conv_id="conv-2",
            peer_id="carol-id",
            peer_username="carol",
            unread_count=0,
            requires_action=True,
        ),
    ]

    assert (
        screen.render_summary_line(conversations)
        == "2 conversations  ·  3 unread  ·  1 needs review"
    )


def test_conversation_list_summary_keeps_unread_label_consistent() -> None:
    screen = ConversationListScreen("alice")
    conversations = [
        ConversationSummaryViewModel(
            conv_id="conv-1",
            peer_id="bob-id",
            peer_username="bob",
            unread_count=1,
            requires_action=False,
        )
    ]

    assert (
        screen.render_summary_line(conversations)
        == "1 conversation  ·  1 unread  ·  0 needs review"
    )


def test_conversation_list_empty_state_guides_next_step() -> None:
    screen = ConversationListScreen("alice")

    assert (
        screen.render_empty_state()
        == "No conversations yet.\nAccepted friends will appear here immediately."
    )


def test_friends_screen_pending_summary_reports_count() -> None:
    screen = FriendsScreen()
    requests = [
        {"id": "req-1", "sender_name": "bob"},
        {"id": "req-2", "sender_name": "carol"},
    ]

    assert (
        screen.render_pending_summary(requests)
        == "2 incoming requests  ·  respond to start chatting"
    )


def test_friends_screen_empty_state_guides_sending_request() -> None:
    screen = FriendsScreen()

    assert (
        screen.render_empty_state()
        == "No pending requests.\nSend an invite below to start a secure conversation."
    )


def test_friends_screen_populate_pending_updates_summary_and_empty_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    screen = FriendsScreen()
    pending_list = _FakeListView()
    pending_summary = _FakeStatic()
    empty_state = _FakeStatic()
    widgets = {
        "#pending_list": pending_list,
        "#pending_summary": pending_summary,
        "#pending_empty": empty_state,
    }
    monkeypatch.setattr(screen, "query_one", lambda selector, *_args, **_kwargs: widgets[selector])

    screen.populate_pending([])
    assert pending_summary.value == "No incoming requests right now"
    assert (
        empty_state.value
        == "No pending requests.\nSend an invite below to start a secure conversation."
    )
    assert pending_list.items == []

    screen.populate_pending([{"id": "req-1", "sender_name": "bob"}])
    assert pending_summary.value == "1 incoming request  ·  respond to start chatting"
    assert empty_state.value == ""
    assert len(pending_list.items) == 1


def test_verified_warning_persists_until_reverified() -> None:
    cache = IdentityKeyCache()
    pub1 = b"a" * 32
    pub2 = b"b" * 32

    cache.check_and_update("bob", pub1)
    cache.mark_verified("bob", pub1)
    with pytest.raises(KeyChangeWarning):
        cache.check_and_update("bob", pub2)

    restored = IdentityKeyCache.from_dict(_identity_cache_raw(cache.as_dict()))
    trust = restored.get_trust_state("bob")
    assert trust is not None
    assert trust.verified is True
    assert trust.key_changed is True

    restored.mark_verified("bob", pub2)
    reverified = IdentityKeyCache.from_dict(
        _identity_cache_raw(restored.as_dict())
    ).get_trust_state("bob")
    assert reverified is not None
    assert reverified.verified is True
    assert reverified.key_changed is False


def test_settings_verify_does_not_optimistically_update_trust(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = SettingsScreen("conv-1", "bob")
    widgets = _patch_settings_widgets(monkeypatch, settings)
    posted: list[object] = []

    monkeypatch.setattr(settings, "post_message", lambda message: posted.append(message))
    settings.set_trust_view_model(
        TrustViewModel(
            fingerprint="fp",
            verified=False,
            key_changed=True,
            requires_action=True,
            last_verified_at=None,
        )
    )

    settings.on_button_pressed(_as_any(SimpleNamespace(button=_FakeButton("btn_verify"))))

    assert widgets["#trust_badge"].value == "Action required"
    assert widgets["#trust_state"].value == "Not verified · Key changed"
    assert widgets["#action_hint"].value == "Re-verify this contact before trusting new messages."
    assert widgets["#status"].value == "Verification requested."
    assert len(posted) == 1


@pytest.mark.asyncio
async def test_friend_accept_server_error_is_shown(monkeypatch: pytest.MonkeyPatch) -> None:
    app = IMApp("https://example.test", "alice")
    screen = _FakeFriendsScreen()
    app._screen_stack.append(screen)  # type: ignore[attr-defined]

    async def _raise_handle(*_args, **_kwargs):
        raise _imclient_error(409, "Already handled")

    app._client = _as_any(
        SimpleNamespace(
            handle_friend_request=_raise_handle,
            list_pending_requests=None,
        )
    )

    await app.on_friends_screen_accept_request(SimpleNamespace(request_id="req-1"))

    assert screen.error == "Already handled"
    assert screen.pending_payloads == []


@pytest.mark.asyncio
async def test_friend_accept_refreshes_existing_conversation_lists_for_acceptor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = IMApp("https://example.test", "alice")
    friends = _FakeFriendsScreen()
    conversation_screen = ConversationListScreen("alice")
    app._screen_stack.append(conversation_screen)  # type: ignore[attr-defined]
    app._screen_stack.append(friends)  # type: ignore[attr-defined]
    app._client = _as_any(SimpleNamespace(handle_friend_request=None, list_pending_requests=None))

    async def _execute_handle_friend_request(*_args, **_kwargs):
        return FriendRequestHandled(
            requests=[],
            status_message="Friend request accepted.",
        )

    synced = 0
    populated: list[list[ConversationSummaryViewModel]] = []

    async def _sync_conversations() -> None:
        nonlocal synced
        synced += 1

    async def _get_conversations() -> list[dict[str, object]]:
        return [
            {
                "id": "conv-1",
                "peer_id": "bob-id",
                "peer_username": "bob",
                "last_message_at": 123,
                "unread_count": 0,
            }
        ]

    monkeypatch.setattr(app_module, "execute_handle_friend_request", _execute_handle_friend_request)
    monkeypatch.setattr(app, "_sync_conversations_from_server", _sync_conversations)
    monkeypatch.setattr(app_module, "get_conversations", _get_conversations)
    monkeypatch.setattr(
        ConversationListScreen,
        "populate",
        lambda self, conversations: populated.append(conversations),
    )

    await app.on_friends_screen_accept_request(SimpleNamespace(request_id="req-1"))

    assert friends.status == "Friend request accepted."
    assert synced == 1
    assert app._peer_usernames == {"bob-id": "bob"}
    assert len(populated) == 1
    assert populated[0][0].conv_id == "conv-1"


@pytest.mark.asyncio
async def test_friend_send_request_network_error_is_shown(monkeypatch: pytest.MonkeyPatch) -> None:
    app = IMApp("https://example.test", "alice")
    screen = _FakeFriendsScreen()
    app._screen_stack.append(screen)  # type: ignore[attr-defined]

    async def _raise_send(*_args, **_kwargs):
        raise httpx.ConnectError("offline")

    app._client = _as_any(SimpleNamespace(send_friend_request=_raise_send))

    await app.on_friends_screen_send_request(SimpleNamespace(username="bob"))

    assert screen.error == "Cannot reach server. Friend request not sent."
    assert screen.status == ""


@pytest.mark.asyncio
async def test_friend_decline_network_error_is_shown(monkeypatch: pytest.MonkeyPatch) -> None:
    app = IMApp("https://example.test", "alice")
    screen = _FakeFriendsScreen()
    app._screen_stack.append(screen)  # type: ignore[attr-defined]

    async def _raise_handle(*_args, **_kwargs):
        raise httpx.ConnectError("offline")

    app._client = _as_any(
        SimpleNamespace(
            handle_friend_request=_raise_handle,
            list_pending_requests=None,
        )
    )

    await app.on_friends_screen_decline_request(SimpleNamespace(request_id="req-1"))

    assert screen.error == "Cannot reach server. Pending request unchanged."
    assert screen.pending_payloads == []


@pytest.mark.asyncio
async def test_login_blocks_transition_on_initial_websocket_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = IMApp("https://example.test", "alice")
    login_screen = _FakeLoginScreen()
    app._screen_stack.append(login_screen)  # type: ignore[attr-defined]

    class _FakeClient:
        def __init__(self, *_args, **_kwargs) -> None:
            self.closed = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args) -> None:
            self.closed = True

        async def login(self, _body) -> None:
            return None

        async def get_keys(self, _username):
            return SimpleNamespace(user_id="alice-id")

        async def connect_ws(self, _on_message) -> None:
            raise IMClientConnectionError("WebSocket authentication failed.")

    monkeypatch.setattr(app_module, "IMClient", _FakeClient)
    monkeypatch.setattr(app_module, "keystore_exists", lambda _username: True)
    monkeypatch.setattr(app_module, "load_keystore", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(app_module, "init_store", lambda *_args, **_kwargs: _async_noop())
    monkeypatch.setattr(app_module, "sweep_expired", lambda *_args, **_kwargs: _async_noop())
    monkeypatch.setattr(app_module, "load_sessions", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(app, "_derive_and_set_storage_key", lambda *_args, **_kwargs: None)

    showed_conversations = False

    async def _show_conversations() -> None:
        nonlocal showed_conversations
        showed_conversations = True

    monkeypatch.setattr(app, "_show_conversations", _show_conversations)

    password = TEST_ACCOUNT_PASSWORD
    await app.on_login_screen_login_success(LoginScreen.LoginSuccess("alice", password, "123456"))

    assert login_screen.error.value == "Login failed: WebSocket authentication failed."
    assert showed_conversations is False
    assert app._client is None


@pytest.mark.asyncio
async def test_login_waits_for_initial_websocket_startup_before_showing_conversations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = IMApp("https://example.test", "alice")
    login_screen = _FakeLoginScreen()
    app._screen_stack.append(login_screen)  # type: ignore[attr-defined]
    ready = asyncio.Event()
    fake_client = _as_any(object())

    async def _execute_login_handshake(*_args, **_kwargs):
        await ready.wait()
        return LoginSucceeded(
            client=fake_client,
            username="alice",
            password=TEST_ACCOUNT_PASSWORD,
            user_id="alice-id",
            local_keys=object(),
            sessions={},
        )

    monkeypatch.setattr(app_module, "execute_login_handshake", _execute_login_handshake)

    showed_conversations = False

    async def _show_conversations() -> None:
        nonlocal showed_conversations
        showed_conversations = True

    monkeypatch.setattr(app, "_show_conversations", _show_conversations)

    task = asyncio.create_task(
        app.on_login_screen_login_success(
            LoginScreen.LoginSuccess(
                "alice",
                TEST_ACCOUNT_PASSWORD,
                "123456",
            )
        )
    )
    await asyncio.sleep(0)

    assert task.done() is False
    assert showed_conversations is False

    ready.set()
    await task

    assert showed_conversations is True
    assert app._client is fake_client


@pytest.mark.asyncio
async def test_show_conversations_syncs_server_rows_and_rehydrates_peer_map(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = IMApp("https://example.test", "alice")
    synced_rows: list[dict[str, object]] = []
    pushed_screens: list[object] = []
    populated: list[list[ConversationSummaryViewModel]] = []
    app._client = _as_any(
        SimpleNamespace(
            list_conversations=lambda: _async_value(
                SimpleNamespace(
                    conversations=[
                        SimpleNamespace(
                            id="conv-1",
                            peer_id="bob-id",
                            peer_username="bob",
                            last_message_at=123,
                            unread_count=2,
                        )
                    ]
                )
            )
        )
    )

    async def _upsert_conversation(**kwargs) -> None:
        synced_rows.append(kwargs)

    async def _get_conversations() -> list[dict[str, object]]:
        return [
            {
                "id": "conv-1",
                "peer_id": "bob-id",
                "peer_username": "bob",
                "last_message_at": 123,
                "unread_count": 2,
            }
        ]

    async def _push_screen(screen: object) -> None:
        pushed_screens.append(screen)

    monkeypatch.setattr(app_module, "upsert_conversation", _upsert_conversation)
    monkeypatch.setattr(app_module, "get_conversations", _get_conversations)
    monkeypatch.setattr(app, "push_screen", _push_screen)
    monkeypatch.setattr(
        ConversationListScreen,
        "populate",
        lambda self, conversations: populated.append(conversations),
    )

    await app._show_conversations()

    assert synced_rows == [
        {
            "id": "conv-1",
            "peer_id": "bob-id",
            "peer_username": "bob",
            "last_message_at": 123,
            "unread_count": 2,
        }
    ]
    assert app._peer_usernames == {"bob-id": "bob"}
    assert len(pushed_screens) == 1
    assert len(populated) == 1


@pytest.mark.asyncio
async def test_friend_request_accept_push_refreshes_existing_conversation_lists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = IMApp("https://example.test", "alice")
    screen = ConversationListScreen("alice")
    app._screen_stack.append(screen)  # type: ignore[attr-defined]

    synced = 0
    populated: list[list[ConversationSummaryViewModel]] = []

    async def _sync_conversations() -> None:
        nonlocal synced
        synced += 1

    async def _get_conversations() -> list[dict[str, object]]:
        return [
            {
                "id": "conv-1",
                "peer_id": "bob-id",
                "peer_username": "bob",
                "last_message_at": 123,
                "unread_count": 0,
            }
        ]

    monkeypatch.setattr(app, "_sync_conversations_from_server", _sync_conversations)
    monkeypatch.setattr(app_module, "get_conversations", _get_conversations)
    monkeypatch.setattr(
        ConversationListScreen,
        "populate",
        lambda self, conversations: populated.append(conversations),
    )

    await app._on_ws_message(
        {
            "type": "friend_request",
            "payload": {
                "event": FriendRequestStatus.ACCEPTED.value,
                "request_id": "req-1",
                "sender_id": "alice-id",
                "recipient_id": "bob-id",
                "conversation_id": "conv-1",
            },
        }
    )

    assert synced == 1
    assert app._peer_usernames == {"bob-id": "bob"}
    assert len(populated) == 1
    assert len(populated[0]) == 1
    assert populated[0][0].conv_id == "conv-1"


async def _async_value(value: object) -> object:
    return value


async def _async_noop(*_args, **_kwargs) -> None:
    return None
