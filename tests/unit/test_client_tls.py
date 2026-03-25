from __future__ import annotations

import ssl

from client.api import client as client_module


def test_tls_default_verifies_cert() -> None:
    verify, ws_ssl = client_module.apply_tls_policy()

    assert verify is True
    assert ws_ssl is None


def test_tls_insecure_flag_disables_verify_with_warning(monkeypatch) -> None:
    warnings: list[tuple[tuple, dict]] = []
    monkeypatch.setattr(
        client_module.log,
        "warning",
        lambda *args, **kwargs: warnings.append((args, kwargs)),
    )

    verify, ws_ssl = client_module.apply_tls_policy(verify_tls=False)

    assert verify is False
    assert isinstance(ws_ssl, ssl.SSLContext)
    assert warnings
