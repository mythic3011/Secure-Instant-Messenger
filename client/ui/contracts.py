from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

Severity = Literal["info", "warning", "error"]
UiStateKind = Literal["ok", "empty", "loading", "degraded", "blocked", "error"]
UiErrorCode = Literal[
    "local_storage_unavailable",
    "local_history_unavailable",
    "message_send_blocked",
    "message_send_failed",
    "key_changed",
    "trust_unverified",
    "ciphertext_tampered",
    "replay_rejected",
    "network_unavailable",
    "server_error",
]

MessageRecord = dict[str, Any]


@dataclass(frozen=True)
class UIBanner:
    code: UiErrorCode
    severity: Severity
    title: str
    message: str
    persistent: bool = False
    dismissible: bool = True


@dataclass(frozen=True)
class UIScreenState:
    kind: UiStateKind
    banners: tuple[UIBanner, ...] = ()
    primary_banner: UIBanner | None = None
    disabled_actions: tuple[str, ...] = ()


@dataclass(frozen=True)
class ChatHistoryResult:
    state: UIScreenState
    messages: tuple[MessageRecord, ...] = ()


@dataclass(frozen=True)
class SendResult:
    ok: bool
    state: UIScreenState
    sent_at: int | None = None


@dataclass(frozen=True)
class TrustViewModel:
    fingerprint: str
    verified: bool
    key_changed: bool
    requires_action: bool
    last_verified_at: int | None
    banner: UIBanner | None = None


@dataclass(frozen=True)
class ConversationSummaryViewModel:
    conv_id: str
    peer_id: str
    peer_username: str
    unread_count: int
    requires_action: bool = False
    primary_banner: UIBanner | None = None
