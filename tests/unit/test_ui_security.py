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
from client.ui.contracts import TrustDisplayState
from client.ui.screens.chat import ChatScreen
from client.ui.screens.settings import SettingsScreen


class _FakeStatic:
    def __init__(self) -> None:
        self.value = ""

    def update(self, value: str) -> None:
        self.value = value


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
) -> tuple[_FakeStatic, _FakeListView]:
    warning = _FakeStatic()
    messages = _FakeListView()
    widgets = {
        "#warning": warning,
        "#messages": messages,
    }
    monkeypatch.setattr(
        chat,
        "query_one",
        lambda selector, *_args, **_kwargs: widgets[selector],
    )
    return warning, messages


def _patch_settings_widgets(
    monkeypatch: pytest.MonkeyPatch,
    settings: SettingsScreen,
) -> dict[str, _FakeStatic]:
    widgets = {
        "#fingerprint": _FakeStatic(),
        "#trust_state": _FakeStatic(),
        "#status": _FakeStatic(),
    }
    monkeypatch.setattr(
        settings,
        "query_one",
        lambda selector, *_args, **_kwargs: widgets[selector],
    )
    return widgets


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

    result = await app.load_chat_history("conv-1")

    chat = ChatScreen("conv-1", "bob", "bob", "alice")
    warning_widget, messages = _patch_chat_widgets(monkeypatch, chat)
    chat.render_history_result(result)

    assert result.error is not None
    assert result.error.code == "local_history_unavailable"
    assert warning_events
    assert "Local chat history is unavailable" in warning_widget.value
    assert messages.items == []


@pytest.mark.asyncio
async def test_send_message_missing_storage_key_is_handled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
        lambda **_kwargs: SimpleNamespace(
            id="msg-1",
            eph_pub_b64=None,
            conv_dh_pub_b64=None,
        ),
    )
    monkeypatch.setattr(app_module, "save_message", _raise_save_message)
    monkeypatch.setattr(
        app_module.log,
        "warning",
        lambda *args, **kwargs: warning_events.append((args, kwargs)),
    )

    result = await app.send_message_action(
        conversation_id="conv-1",
        peer_id="bob",
        plaintext="hello",
    )

    chat = ChatScreen("conv-1", "bob", "bob", "alice")
    warning_widget, messages = _patch_chat_widgets(monkeypatch, chat)
    chat.apply_send_result(result)

    assert result.status == "blocked"
    assert result.error is not None
    assert result.error.code == "local_key_unavailable"
    assert warning_events
    assert "Local secure storage is unavailable" in warning_widget.value
    assert messages.items == []


@pytest.mark.asyncio
async def test_security_ui_paths_do_not_silently_swallow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
        lambda **_kwargs: SimpleNamespace(
            id="msg-1",
            eph_pub_b64=None,
            conv_dh_pub_b64=None,
        ),
    )
    monkeypatch.setattr(app_module, "save_message", _raise_save_message)
    monkeypatch.setattr(
        app_module.log,
        "warning",
        lambda *args, **kwargs: warnings.append((args, kwargs)),
    )

    history_result = await app.load_chat_history("conv-1")
    send_result = await app.send_message_action(
        conversation_id="conv-1",
        peer_id="bob",
        plaintext="hello",
    )

    assert warnings
    assert history_result.error is not None
    assert send_result.error is not None
    assert history_result.messages == []
    assert send_result.status == "blocked"


def test_settings_shows_verified_state(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = SettingsScreen("conv-1", "bob")
    widgets = _patch_settings_widgets(monkeypatch, settings)

    settings.set_trust_state(TrustDisplayState(verified=True, key_changed=False))

    assert widgets["#trust_state"].value == "Verified · Key stable"


def test_settings_shows_key_changed_state(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = SettingsScreen("conv-1", "bob")
    widgets = _patch_settings_widgets(monkeypatch, settings)

    settings.set_trust_state(TrustDisplayState(verified=True, key_changed=True))

    assert widgets["#trust_state"].value == "Verified · Key changed"


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
