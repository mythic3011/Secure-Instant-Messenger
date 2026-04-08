from __future__ import annotations

import os
import subprocess
from pathlib import Path


def _make_fake_uv(tmp_path: Path) -> tuple[dict[str, str], Path]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(exist_ok=True)
    fake_uv = fake_bin / "uv"
    log_file = tmp_path / "uv.log"
    fake_uv.write_text(
        '#!/usr/bin/env bash\nset -euo pipefail\nprintf \'%s\\n\' "$*" >> "$FAKE_UV_LOG"\nexit 0\n',
        encoding="utf-8",
    )
    fake_uv.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["FAKE_UV_LOG"] = str(log_file)
    return env, log_file


def _run_wrapper(tmp_path: Path, *args: str) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    repo_root = Path(__file__).resolve().parents[2]
    env, log_file = _make_fake_uv(tmp_path)
    result = subprocess.run(  # noqa: S603
        ["sh", "scripts/launch/run-server.sh", *args],  # noqa: S607
        cwd=repo_root,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    invocations = log_file.read_text(encoding="utf-8").splitlines() if log_file.exists() else []
    return result, invocations


def test_run_server_check_fails_without_env_file(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    env_local = repo_root / ".env.local"
    backup = env_local.read_text(encoding="utf-8") if env_local.exists() else None
    if env_local.exists():
        env_local.unlink()
    try:
        result, invocations = _run_wrapper(tmp_path, "--check")
    finally:
        if backup is not None:
            env_local.write_text(backup, encoding="utf-8")

    assert result.returncode == 1
    assert ".env.local not found" in result.stderr
    assert invocations == []


def test_run_server_check_fails_without_tls_files(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    env_local = repo_root / ".env.local"
    env_backup = env_local.read_text(encoding="utf-8") if env_local.exists() else None
    env_local.write_text("APP_ENV=development\n", encoding="utf-8")

    cert_dir = repo_root / "certs"
    cert_dir.mkdir(exist_ok=True)
    cert_file = cert_dir / "server.crt"
    key_file = cert_dir / "server.key"
    cert_backup = cert_file.read_text(encoding="utf-8") if cert_file.exists() else None
    key_backup = key_file.read_text(encoding="utf-8") if key_file.exists() else None
    if cert_file.exists():
        cert_file.unlink()
    if key_file.exists():
        key_file.unlink()

    try:
        result_missing_cert, invocations_missing_cert = _run_wrapper(tmp_path, "--check")
        cert_file.write_text("", encoding="utf-8")
        result_missing_key, invocations_missing_key = _run_wrapper(tmp_path, "--check")
    finally:
        if env_backup is None:
            env_local.unlink(missing_ok=True)
        else:
            env_local.write_text(env_backup, encoding="utf-8")
        if cert_backup is None:
            cert_file.unlink(missing_ok=True)
        else:
            cert_file.write_text(cert_backup, encoding="utf-8")
        if key_backup is None:
            key_file.unlink(missing_ok=True)
        else:
            key_file.write_text(key_backup, encoding="utf-8")

    assert result_missing_cert.returncode == 1
    assert "HTTPS readiness missing: certs/server.crt not found" in result_missing_cert.stderr
    assert invocations_missing_cert == []

    assert result_missing_key.returncode == 1
    assert "HTTPS readiness missing: certs/server.key not found" in result_missing_key.stderr
    assert invocations_missing_key == []


def test_run_server_check_passes_when_https_ready(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    env_local = repo_root / ".env.local"
    env_backup = env_local.read_text(encoding="utf-8") if env_local.exists() else None
    env_local.write_text("APP_ENV=development\n", encoding="utf-8")

    cert_dir = repo_root / "certs"
    cert_dir.mkdir(exist_ok=True)
    cert_file = cert_dir / "server.crt"
    key_file = cert_dir / "server.key"
    cert_backup = cert_file.read_text(encoding="utf-8") if cert_file.exists() else None
    key_backup = key_file.read_text(encoding="utf-8") if key_file.exists() else None
    cert_file.write_text("", encoding="utf-8")
    key_file.write_text("", encoding="utf-8")

    try:
        result, invocations = _run_wrapper(tmp_path, "--check")
    finally:
        if env_backup is None:
            env_local.unlink(missing_ok=True)
        else:
            env_local.write_text(env_backup, encoding="utf-8")
        if cert_backup is None:
            cert_file.unlink(missing_ok=True)
        else:
            cert_file.write_text(cert_backup, encoding="utf-8")
        if key_backup is None:
            key_file.unlink(missing_ok=True)
        else:
            key_file.write_text(key_backup, encoding="utf-8")

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[run-server] check passed"
    assert invocations[-1] == "run python -c from server.main import app; print(app.title)"
