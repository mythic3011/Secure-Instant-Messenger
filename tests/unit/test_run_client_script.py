from __future__ import annotations

import os
import subprocess
from pathlib import Path


def _make_fake_uv(tmp_path: Path) -> tuple[dict[str, str], Path]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_uv = fake_bin / "uv"
    log_file = tmp_path / "uv.log"
    fake_uv.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'if [[ -n "${FAKE_UV_FAIL_ON:-}" && "$*" == *"${FAKE_UV_FAIL_ON}"* ]]; then\n'
        '  printf \'%s\\n\' "$*" >> "$FAKE_UV_LOG"\n'
        '  exit "${FAKE_UV_FAIL_CODE:-1}"\n'
        "fi\n"
        'printf \'%s\\n\' "$*" >> "$FAKE_UV_LOG"\n'
        "exit 0\n",
        encoding="utf-8",
    )
    fake_uv.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["FAKE_UV_LOG"] = str(log_file)
    return env, log_file


def _run_wrapper(
    tmp_path: Path,
    shell: str,
    script_path: str,
    *args: str,
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    repo_root = Path(__file__).resolve().parents[2]
    env, log_file = _make_fake_uv(tmp_path)
    result = subprocess.run(  # noqa: S603
        [shell, script_path, *args],
        cwd=repo_root,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    invocations = log_file.read_text(encoding="utf-8").splitlines() if log_file.exists() else []
    return result, invocations


def _has_health_probe(invocations: list[str]) -> bool:
    return any(
        "scripts/lib/check_server_health.py --server https://localhost:8443" in item
        for item in invocations
    )


def test_run_client_script_forwards_extra_cli_args_via_bash(tmp_path: Path) -> None:
    result, invocations = _run_wrapper(
        tmp_path,
        "bash",
        "scripts/launch/run-client.sh",
        "https://localhost:8443",
        "--ca-cert",
        "./certs/server.crt",
        "--verbose",
    )

    assert result.returncode == 0, result.stderr
    assert _has_health_probe(invocations)
    assert invocations[-1] == (
        "run python -m client.main --server https://localhost:8443 "
        "--ca-cert ./certs/server.crt --verbose"
    )


def test_run_client_script_forwards_extra_cli_args_via_sh(tmp_path: Path) -> None:
    result, invocations = _run_wrapper(
        tmp_path,
        "sh",
        "scripts/launch/run-client.sh",
        "https://localhost:8443",
        "--ca-cert",
        "./certs/server.crt",
        "--verbose",
    )

    assert result.returncode == 0, result.stderr
    assert _has_health_probe(invocations)
    assert invocations[-1] == (
        "run python -m client.main --server https://localhost:8443 "
        "--ca-cert ./certs/server.crt --verbose"
    )


def test_run_client_script_check_mode_ignores_extra_args(tmp_path: Path) -> None:
    result, invocations = _run_wrapper(
        tmp_path,
        "sh",
        "scripts/launch/run-client.sh",
        "--check",
        "https://localhost:8443",
        "--ca-cert",
        "./certs/server.crt",
        "--verbose",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[run-client] check passed"
    assert all("python -m client.main" not in invocation for invocation in invocations)
    assert all("scripts/lib/check_server_health.py" not in invocation for invocation in invocations)


def test_run_client_script_can_skip_health_check(tmp_path: Path) -> None:
    result, invocations = _run_wrapper(
        tmp_path,
        "sh",
        "scripts/launch/run-client.sh",
        "--no-health-check",
        "https://localhost:8443",
        "--ca-cert",
        "./certs/server.crt",
    )

    assert result.returncode == 0, result.stderr
    assert all("scripts/lib/check_server_health.py" not in invocation for invocation in invocations)
    assert invocations[-1] == (
        "run python -m client.main --server https://localhost:8443 --ca-cert ./certs/server.crt"
    )


def test_run_client_script_health_failure_stops_before_client_start(tmp_path: Path) -> None:
    env, log_file = _make_fake_uv(tmp_path)
    env["FAKE_UV_FAIL_ON"] = "scripts/lib/check_server_health.py"
    env["FAKE_UV_FAIL_CODE"] = "7"
    repo_root = Path(__file__).resolve().parents[2]

    result = subprocess.run(  # noqa: S603
        ["sh", "scripts/run-client.sh", "https://localhost:8443"],  # noqa: S607
        cwd=repo_root,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    invocations = log_file.read_text(encoding="utf-8").splitlines()
    assert result.returncode == 1
    assert "server health check failed" in result.stderr
    assert _has_health_probe(invocations)
    assert all("python -m client.main" not in invocation for invocation in invocations)


def test_run_client_batch_script_forwards_extra_args_contract() -> None:
    content = Path("scripts/launch/run-client.bat").read_text(encoding="utf-8")

    assert "--no-health-check" in content
    assert "uv run python scripts/lib/check_server_health.py" in content
    assert 'uv run python -m client.main --server "%SERVER_URL%"' in content
    assert "%1" in content
    assert "shift" in content
    assert "%~1:~0,7%" not in content
    assert "%~1:~0,8%" not in content
    assert 'set "CURRENT_ARG=%~1"' in content


def test_run_client_batch_script_check_mode_does_not_invoke_client_main() -> None:
    content = Path("scripts/launch/run-client.bat").read_text(encoding="utf-8")

    assert 'uv run python -c "import client.main" >nul' in content
    assert "uv run python -m client.main --help >nul" not in content
    assert "uv run python scripts/lib/check_server_health.py" in content


def test_run_client_batch_script_check_mode_is_wrapper_only() -> None:
    content = Path("scripts/launch/run-client.bat").read_text(encoding="utf-8")

    check_start = content.index('if "%~1"=="--check"')
    check_block = content[check_start:]
    check_exit = check_block.index("exit /b 0")
    assert "check-server-health" not in check_block[:check_exit]
    assert "scripts/lib/check_server_health.py" not in check_block[:check_exit]


def test_run_client_batch_script_uses_windows_specific_remediation_text() -> None:
    content = Path("scripts/launch/run-client.bat").read_text(encoding="utf-8")

    assert "canonical installer: install.sh" in content
    assert "run it from a POSIX shell if available" in content


def test_run_client_legacy_shim_preserves_invocation_contract(tmp_path: Path) -> None:
    result, invocations = _run_wrapper(
        tmp_path,
        "sh",
        "scripts/run-client.sh",
        "https://localhost:8443",
        "--ca-cert",
        "./certs/server.crt",
    )

    assert result.returncode == 0, result.stderr
    assert _has_health_probe(invocations)


def test_run_client_batch_legacy_shim_points_to_canonical_path() -> None:
    content = Path("scripts/run-client.bat").read_text(encoding="utf-8")

    assert "Legacy compatibility shim." in content
    assert 'call "%~dp0launch\\run-client.bat" %*' in content
