from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from typing import Literal

MessageRecord = dict[str, Any]


@dataclass(frozen=True)
class UIErrorState:
    code: str
    message: str
    severity: Literal["warning", "error"]


@dataclass(frozen=True)
class ChatHistoryResult:
    messages: list[MessageRecord]
    error: UIErrorState | None = None


@dataclass(frozen=True)
class SendResult:
    status: Literal["sent", "blocked", "failed"]
    sent_at: int | None = None
    error: UIErrorState | None = None


@dataclass(frozen=True)
class TrustDisplayState:
    verified: bool
    key_changed: bool
