from __future__ import annotations

from pathlib import Path

from scripts import check_stale_security_claims


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_scan_file_flags_hashed_conversation_id_claim(tmp_path: Path) -> None:
    target = _write(
        tmp_path / "bad.md",
        '"conversation_id": "sha256(sorted(alice_id, bob_id))[:16]"\n',
    )

    issues = check_stale_security_claims.scan_file(target)

    assert issues == [
        f"{target}:1: stale claim: conversation_id must not be documented as sha256(sorted(...)); it is server-issued"
    ]


def test_scan_file_flags_delivered_read_claim(tmp_path: Path) -> None:
    target = _write(
        tmp_path / "bad.py",
        "# delivery_status: whether a message was delivered/read\n",
    )

    issues = check_stale_security_claims.scan_file(target)

    assert issues == [
        f"{target}:1: stale claim: delivery status should match current sent/delivered semantics; read is reserved only"
    ]


def test_scan_file_allows_server_main_query_param_rationale(tmp_path: Path) -> None:
    (tmp_path / "server").mkdir()
    target = _write(
        tmp_path / "server" / "main.py",
        "Why first-frame auth instead of query-parameter token:\nTokens in ?token=<token> appear in server access logs.\n",
    )

    issues = check_stale_security_claims.scan_file(target)

    assert issues == []


def test_scan_file_honors_inline_whitelist(tmp_path: Path) -> None:
    target = _write(
        tmp_path / "allowed.md",
        "Legacy note: No Per-Message Forward Secrecy. allow-stale-security-claim\n",
    )

    issues = check_stale_security_claims.scan_file(target)

    assert issues == []


def test_iter_candidate_files_excludes_plan_docs(monkeypatch, tmp_path: Path) -> None:
    (tmp_path / "docs" / "plans").mkdir(parents=True)
    (tmp_path / "docs" / "plans" / "skip.md").write_text("skip\n", encoding="utf-8")
    (tmp_path / "docs" / "keep.md").write_text("keep\n", encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(check_stale_security_claims, "ROOTS", ("docs",))

    files = check_stale_security_claims.iter_candidate_files()

    assert files == [Path("docs/keep.md")]
