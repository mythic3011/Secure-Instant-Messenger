from __future__ import annotations

import json

from client.crypto.session import (
    DHKeypair,
    IdentityKeyCache,
    IdentityKeypair,
    RatchetChain,
    ReplayProtector,
    SessionKey,
    make_key_signature,
)
from client.crypto.storage import (
    LocalKeys,
    SessionState,
    load_sessions,
    save_keystore,
    save_sessions,
)
from tests.secrets import TEST_ACCOUNT_PASSWORD


def _session_state(*, conversation_id: str, next_outbound_counter: int) -> SessionState:
    return SessionState(
        session_key=SessionKey(
            raw=b"a" * 32,
            conversation_id=conversation_id,
            peer_id="bob-id",
        ),
        send_chain=RatchetChain(chain_key=b"b" * 32),
        recv_chain=RatchetChain(chain_key=b"c" * 32),
        replay_protector=ReplayProtector(),
        identity_key_cache=IdentityKeyCache(),
        next_outbound_counter=next_outbound_counter,
    )


def _save_local_keystore(username: str, password: str) -> None:
    identity_kp = IdentityKeypair.generate()
    dh_kp = DHKeypair.generate()
    save_keystore(
        username,
        password,
        LocalKeys(
            identity_kp=identity_kp,
            dh_kp=dh_kp,
            key_sig=make_key_signature(identity_kp, dh_kp),
        ),
    )


def test_save_and_load_sessions_round_trip_next_outbound_counter(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    username = "alice"
    _save_local_keystore(username, TEST_ACCOUNT_PASSWORD)

    save_sessions(
        username,
        TEST_ACCOUNT_PASSWORD,
        {"conv-1": _session_state(conversation_id="conv-1", next_outbound_counter=4)},
    )

    restored = load_sessions(username, TEST_ACCOUNT_PASSWORD)

    assert restored["conv-1"].next_outbound_counter == 4


def test_load_sessions_defaults_missing_next_outbound_counter_backward_compatibly(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    username = "alice"
    _save_local_keystore(username, TEST_ACCOUNT_PASSWORD)
    save_sessions(
        username,
        TEST_ACCOUNT_PASSWORD,
        {"conv-1": _session_state(conversation_id="conv-1", next_outbound_counter=2)},
    )

    sessions_path = tmp_path / ".comp3334im" / username / "sessions.json"
    raw = json.loads(sessions_path.read_text())
    raw["conv-1"].pop("next_outbound_counter")
    sessions_path.write_text(json.dumps(raw, indent=2))

    restored = load_sessions(username, TEST_ACCOUNT_PASSWORD)

    assert restored["conv-1"].next_outbound_counter == 0


def test_load_sessions_is_idempotent_for_next_outbound_counter(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    username = "alice"
    _save_local_keystore(username, TEST_ACCOUNT_PASSWORD)
    save_sessions(
        username,
        TEST_ACCOUNT_PASSWORD,
        {"conv-1": _session_state(conversation_id="conv-1", next_outbound_counter=5)},
    )

    first = load_sessions(username, TEST_ACCOUNT_PASSWORD)
    second = load_sessions(username, TEST_ACCOUNT_PASSWORD)

    assert first["conv-1"].next_outbound_counter == 5
    assert second["conv-1"].next_outbound_counter == 5
