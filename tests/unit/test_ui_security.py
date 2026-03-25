from __future__ import annotations

from types import SimpleNamespace

import pytest

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
from client.ui.screens.settings import SettingsScreen


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


def _patch_settings_widgets(monkeypatch: pytest.MonkeyPatch, settings: SettingsScreen) -> dict[str, _FakeStatic]:
    widgets = {
        "#fingerprint": _FakeStatic(),
        "#trust_state": _FakeStatic(),
        "#trust_badge": _FakeStatic(),
        "#trust_hint": _FakeStatic(),
        "#action_hint": _FakeStatic(),
        "#status": _FakeStatic(),
    }
    monkeypatch.setattr(settings, "query_one", lambda selector, *_args, **_kwargs: widgets[selector])
    return widgets


@pytest.mark.asyncio
async def test_chat_history_local_decrypt_failure_is_handled(monkeypatch: pytest.MonkeyPatch) -> None:
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
async def test_security_ui_paths_do_not_silently_swallow(monkeypatch: pytest.MonkeyPatch) -> None:
    app = IMApp("https://example.test", "alice")
    app._client = SimpleNamespace(send_message=None)  # type: ignore[assignment]
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


def test_chat_render_degraded_history_keeps_banner_and_placeholder(monkeypatch: pytest.MonkeyPatch) -> None:
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
    assert widgets["#trust_hint"].value == "This contact is verified. Compare fingerprints again after any key change."
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


def test_verified_warning_persists_until_reverified() -> None:
    cache = IdentityKeyCache()
    pub1 = b"a" * 32
    pub2 = b"b" * 32

    cache.check_and_update("bob", pub1)
    cache.mark_verified("bob", pub1)
    with pytest.raises(KeyChangeWarning):
        cache.check_and_update("bob", pub2)

    restored = IdentityKeyCache.from_dict(cache.as_dict())
    trust = restored.get_trust_state("bob")
    assert trust is not None
    assert trust.verified is True
    assert trust.key_changed is True

    restored.mark_verified("bob", pub2)
    reverified = IdentityKeyCache.from_dict(restored.as_dict()).get_trust_state("bob")
    assert reverified is not None
    assert reverified.verified is True
    assert reverified.key_changed is False


def test_settings_verify_does_not_optimistically_update_trust(monkeypatch: pytest.MonkeyPatch) -> None:
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

    settings.on_button_pressed(SimpleNamespace(button=_FakeButton("btn_verify")))

    assert widgets["#trust_badge"].value == "Action required"
    assert widgets["#trust_state"].value == "Not verified · Key changed"
    assert widgets["#action_hint"].value == "Re-verify this contact before trusting new messages."
    assert widgets["#status"].value == "Verification requested."
    assert len(posted) == 1
