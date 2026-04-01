from __future__ import annotations

from client.use_cases import (
    LocalSecurityFailure,
    NetworkFailure,
    ServerFailure,
    Success,
    TrustBlocked,
    UnexpectedFailure,
)


def test_use_case_success_can_carry_value() -> None:
    result = Success(value={"message_id": "msg-1"})

    assert result.value == {"message_id": "msg-1"}


def test_use_case_failures_have_stable_codes() -> None:
    assert NetworkFailure().code == "network_failure"
    assert ServerFailure(message="Already handled").code == "server_failure"
    assert LocalSecurityFailure().code == "local_security_failure"
    assert TrustBlocked().code == "trust_blocked"
    assert UnexpectedFailure().code == "unexpected_failure"


def test_use_case_failures_allow_stable_message_overrides() -> None:
    result = ServerFailure(message="Peer key unavailable")

    assert result.message == "Peer key unavailable"
