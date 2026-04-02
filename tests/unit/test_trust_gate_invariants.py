from __future__ import annotations

from pathlib import Path


def test_fetched_bundle_paths_do_not_decode_or_verify_outside_trust_gate() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    target_paths = [
        repo_root / "client" / "use_cases" / "send_message.py",
        repo_root / "client" / "ui" / "app.py",
    ]

    for path in target_paths:
        source = path.read_text()
        assert "verify_key_bundle(" not in source
        assert "validate_and_decode_peer_bundle(" in source
        assert "peer_bundle.identity_pub_b64" not in source
        assert "peer_bundle.dh_pub_b64" not in source
        assert "peer_bundle.key_sig_b64" not in source
