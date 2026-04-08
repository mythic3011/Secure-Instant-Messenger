from __future__ import annotations

import os
import pty
import shutil
import subprocess
from pathlib import Path


def _write_fake_command(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def _copy_install_files(tmp_path: Path) -> Path:
    repo_root = Path(__file__).resolve().parents[2]
    project_root = tmp_path / "project"
    (project_root / "scripts").mkdir(parents=True)
    install_path = project_root / "install.sh"
    (project_root / "scripts" / "install").mkdir(parents=True)
    check_path = project_root / "scripts/install/check-prereqs.sh"
    install_prereq_path = project_root / "scripts/install/install-prereqs.sh"
    bootstrap_path = project_root / "scripts/install/bootstrap-env.sh"
    shutil.copy2(repo_root / "install.sh", install_path)
    shutil.copy2(repo_root / "scripts/install/check-prereqs.sh", check_path)
    shutil.copy2(repo_root / "scripts/install/install-prereqs.sh", install_prereq_path)
    shutil.copy2(repo_root / "scripts/install/bootstrap-env.sh", bootstrap_path)
    install_path.chmod(0o755)
    check_path.chmod(0o755)
    install_prereq_path.chmod(0o755)
    bootstrap_path.chmod(0o755)
    (project_root / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")
    return project_root


def _run_install(
    project_root: Path,
    shell: str,
    path_override: str,
    *args: str,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PATH"] = path_override
    return subprocess.run(  # noqa: S603
        [shell, "./install.sh", *args],
        cwd=project_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _run_install_with_tty_input(
    project_root: Path,
    path_override: str,
    user_input: str,
    *args: str,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PATH"] = path_override
    master_fd, slave_fd = pty.openpty()
    try:
        process = subprocess.Popen(  # noqa: S603
            ["sh", "./install.sh", *args],  # noqa: S607
            cwd=project_root,
            env=env,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            text=True,
            close_fds=True,
        )
        os.close(slave_fd)
        os.write(master_fd, user_input.encode("utf-8"))
        chunks: list[bytes] = []
        while True:
            try:
                chunk = os.read(master_fd, 4096)
            except OSError:
                break
            if not chunk:
                break
            chunks.append(chunk)
        returncode = process.wait()
    finally:
        os.close(master_fd)

    output = b"".join(chunks).decode("utf-8", errors="replace")
    return subprocess.CompletedProcess(
        args=["sh", "./install.sh", *args],
        returncode=returncode,
        stdout=output,
        stderr=output,
    )


def test_install_check_passes_in_sh_and_bash(tmp_path: Path) -> None:
    project_root = _copy_install_files(tmp_path)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    for name in ("python3", "uv", "openssl"):
        _write_fake_command(fake_bin / name, "#!/bin/sh\nexit 0\n")

    path_override = f"{fake_bin}:/usr/bin:/bin"

    result_sh = _run_install(project_root, "sh", path_override, "--check")
    result_bash = _run_install(project_root, "bash", path_override, "--check")

    assert result_sh.returncode == 0, result_sh.stderr
    assert result_bash.returncode == 0, result_bash.stderr
    assert "[install] check passed" in result_sh.stdout
    assert "[install] check passed" in result_bash.stdout


def test_install_fix_runs_uv_sync_without_sudo(tmp_path: Path) -> None:
    project_root = _copy_install_files(tmp_path)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log_file = tmp_path / "commands.log"

    for name in ("python3",):
        _write_fake_command(fake_bin / name, "#!/bin/sh\nexit 0\n")

    _write_fake_command(
        fake_bin / "openssl",
        "#!/bin/sh\n"
        "keyout=\n"
        "out=\n"
        'while [ "$#" -gt 0 ]; do\n'
        '  case "$1" in\n'
        "    -keyout)\n"
        "      shift\n"
        "      keyout=$1\n"
        "      ;;\n"
        "    -out)\n"
        "      shift\n"
        "      out=$1\n"
        "      ;;\n"
        "  esac\n"
        "  shift\n"
        "done\n"
        'mkdir -p "$(dirname "$keyout")"\n'
        ': > "$keyout"\n'
        ': > "$out"\n'
        "exit 0\n",
    )

    _write_fake_command(
        fake_bin / "uv",
        '#!/bin/sh\nprintf \'%s\\n\' "$*" >> "$FAKE_CMD_LOG"\nexit 0\n',
    )
    _write_fake_command(
        fake_bin / "sudo",
        '#!/bin/sh\nprintf \'sudo %s\\n\' "$*" >> "$FAKE_CMD_LOG"\nexit 99\n',
    )

    env_path = f"{fake_bin}:/usr/bin:/bin"
    env = os.environ.copy()
    env["PATH"] = env_path
    env["FAKE_CMD_LOG"] = str(log_file)

    result = subprocess.run(  # noqa: S603
        ["sh", "./install.sh", "--fix"],  # noqa: S607
        cwd=project_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    log_lines = log_file.read_text(encoding="utf-8").splitlines()
    assert "sync" in log_lines[-1]
    assert all(not line.startswith("sudo ") for line in log_lines)


def test_install_fix_prompts_and_installs_missing_uv_via_brew(tmp_path: Path) -> None:
    project_root = _copy_install_files(tmp_path)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log_file = tmp_path / "commands.log"

    for name in ("python3",):
        _write_fake_command(fake_bin / name, "#!/bin/sh\nexit 0\n")

    _write_fake_command(
        fake_bin / "openssl",
        "#!/bin/sh\n"
        "keyout=\n"
        "out=\n"
        'while [ "$#" -gt 0 ]; do\n'
        '  case "$1" in\n'
        "    -keyout)\n"
        "      shift\n"
        "      keyout=$1\n"
        "      ;;\n"
        "    -out)\n"
        "      shift\n"
        "      out=$1\n"
        "      ;;\n"
        "  esac\n"
        "  shift\n"
        "done\n"
        'mkdir -p "$(dirname "$keyout")"\n'
        ': > "$keyout"\n'
        ': > "$out"\n'
        "exit 0\n",
    )

    _write_fake_command(
        fake_bin / "brew",
        "#!/bin/sh\n"
        'printf \'brew %s\\n\' "$*" >> "$FAKE_CMD_LOG"\n'
        'if [ "$1" = "install" ] && [ "$2" = "uv" ]; then\n'
        "  cat > \"$FAKE_BIN_DIR/uv\" <<'EOF'\n"
        "#!/bin/sh\n"
        'printf \'%s\\n\' "$*" >> "$FAKE_CMD_LOG"\n'
        "exit 0\n"
        "EOF\n"
        '  chmod +x "$FAKE_BIN_DIR/uv"\n'
        "fi\n"
        "exit 0\n",
    )

    path_override = f"{fake_bin}:/usr/bin:/bin"
    os.environ["FAKE_CMD_LOG"] = str(log_file)
    os.environ["FAKE_BIN_DIR"] = str(fake_bin)
    try:
        result = _run_install_with_tty_input(project_root, path_override, "y\n", "--fix")
    finally:
        os.environ.pop("FAKE_CMD_LOG", None)
        os.environ.pop("FAKE_BIN_DIR", None)

    assert result.returncode == 0, result.stderr
    log_lines = log_file.read_text(encoding="utf-8").splitlines()
    assert "brew install uv" in log_lines
    assert "sync" in log_lines[-1]


def test_install_fix_fails_when_user_declines_missing_uv_install(tmp_path: Path) -> None:
    project_root = _copy_install_files(tmp_path)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()

    for name in ("python3", "openssl"):
        _write_fake_command(fake_bin / name, "#!/bin/sh\nexit 0\n")
    _write_fake_command(fake_bin / "brew", "#!/bin/sh\nexit 0\n")

    result = _run_install_with_tty_input(
        project_root,
        f"{fake_bin}:/usr/bin:/bin",
        "n\n",
        "--fix",
    )

    assert result.returncode == 1
    assert "user declined installation for uv" in result.stderr


def test_install_check_fails_fast_when_uv_missing(tmp_path: Path) -> None:
    project_root = _copy_install_files(tmp_path)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    for name in ("python3", "openssl"):
        _write_fake_command(fake_bin / name, "#!/bin/sh\nexit 0\n")

    result = _run_install(project_root, "sh", f"{fake_bin}:/usr/bin:/bin", "--check")

    assert result.returncode == 1
    assert "missing prerequisite: uv" in result.stderr
    assert "install uv manually" in result.stderr


def test_install_rejects_invalid_flags(tmp_path: Path) -> None:
    project_root = _copy_install_files(tmp_path)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    for name in ("python3", "uv", "openssl"):
        _write_fake_command(fake_bin / name, "#!/bin/sh\nexit 0\n")

    result = _run_install(project_root, "sh", f"{fake_bin}:/usr/bin:/bin", "--dev")

    assert result.returncode == 2
    assert "Usage:" in result.stderr


def test_install_fix_fails_fast_without_tty_for_missing_uv(tmp_path: Path) -> None:
    project_root = _copy_install_files(tmp_path)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()

    for name in ("python3", "openssl"):
        _write_fake_command(fake_bin / name, "#!/bin/sh\nexit 0\n")
    _write_fake_command(fake_bin / "brew", "#!/bin/sh\nexit 0\n")

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:/usr/bin:/bin"

    result = subprocess.run(  # noqa: S603
        ["sh", "./install.sh", "--fix"],  # noqa: S607
        cwd=project_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "interactive confirmation required to install uv" in result.stderr


def test_install_batch_contract_uses_windows_python_and_uv_sync() -> None:
    content = Path("install.bat").read_text(encoding="utf-8")

    assert "--check" in content
    assert "--fix" in content
    assert "--verbose" in content
    assert "where python" in content
    assert 'set "PYTHON_EXE="' in content
    assert '"%PYTHON_EXE%" --version >nul 2>&1' in content
    assert "python command is not runnable on Windows PATH" in content
    assert '"%PYTHON_EXE%" "%PROJECT_ROOT%\\scripts\\lib\\bootstrap_env.py" dev' in content
    assert "where python3" not in content
    assert "where uv" in content
    assert 'set "UV_EXE="' in content
    assert 'set "SCRIPT_PATH=%~f0"' in content
    assert 'for %%I in ("%SCRIPT_PATH%") do set "PROJECT_ROOT=%%~dpI"' in content
    assert 'set "PROJECT_ROOT=%PROJECT_ROOT:~0,-1%"' in content
    assert '"%UV_EXE%" --project "%PROJECT_ROOT%" sync' in content
    assert "pyproject.toml" in content
    assert r"scripts\lib\bootstrap_env.py" in content


def test_install_batch_contract_avoids_launch_and_host_package_manager_logic() -> None:
    content = Path("install.bat").read_text(encoding="utf-8")

    assert "client.main" not in content
    assert "server.main" not in content
    assert "check_server_health" not in content
    assert "brew install" not in content
    assert "apt-get" not in content
    assert "choco" not in content
    assert "winget" not in content


def test_install_batch_contract_resolves_root_from_script_location() -> None:
    content = Path("install.bat").read_text(encoding="utf-8")

    assert 'pushd "%~dp0"' not in content
    assert 'set "PROJECT_ROOT=%CD%"' not in content
