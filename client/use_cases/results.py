from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

FailureCode = Literal[
    "network_failure",
    "server_failure",
    "local_security_failure",
    "trust_blocked",
    "unexpected_failure",
]


@dataclass(frozen=True)
class Success:
    """
    Successful use-case outcome.

    `value` stays generic so each use case can return its own payload shape
    without forcing a premature shared abstraction.
    """

    value: object | None = None


@dataclass(frozen=True)
class NetworkFailure:
    code: Literal["network_failure"] = "network_failure"
    message: str = "The server is unreachable. Retry when the connection recovers."


@dataclass(frozen=True)
class ServerFailure:
    code: Literal["server_failure"] = "server_failure"
    message: str = "The server rejected the request."


@dataclass(frozen=True)
class LocalSecurityFailure:
    code: Literal["local_security_failure"] = "local_security_failure"
    message: str = "Local secure storage is unavailable."


@dataclass(frozen=True)
class TrustBlocked:
    code: Literal["trust_blocked"] = "trust_blocked"
    message: str = "A trust policy blocked this action."


@dataclass(frozen=True)
class UnexpectedFailure:
    code: Literal["unexpected_failure"] = "unexpected_failure"
    message: str = "An unexpected error occurred."


type UseCaseFailure = (
    NetworkFailure | ServerFailure | LocalSecurityFailure | TrustBlocked | UnexpectedFailure
)
type UseCaseResult = Success | UseCaseFailure
