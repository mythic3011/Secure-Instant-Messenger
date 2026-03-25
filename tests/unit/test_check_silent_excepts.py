from __future__ import annotations

from pathlib import Path

from scripts import check_silent_excepts


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_scan_file_flags_bare_except_pass(tmp_path: Path) -> None:
    target = _write(
        tmp_path / "bad.py",
        "try:\n    risky()\nexcept:\n    pass\n",
    )

    issues = check_silent_excepts.scan_file(target)

    assert issues == [f"{target}:3: broad except with silent body"]


def test_scan_file_flags_exception_with_logging_and_return(tmp_path: Path) -> None:
    target = _write(
        tmp_path / "bad_return.py",
        (
            "try:\n"
            "    risky()\n"
            "except Exception as exc:\n"
            "    log.warning('ignored', exc_info=exc)\n"
            "    return\n"
        ),
    )

    issues = check_silent_excepts.scan_file(target)

    assert issues == [f"{target}:3: broad except with silent body"]


def test_scan_file_ignores_specific_exception(tmp_path: Path) -> None:
    target = _write(
        tmp_path / "good.py",
        (
            "try:\n"
            "    risky()\n"
            "except ValueError as exc:\n"
            "    log.warning('bad input', exc_info=exc)\n"
            "    raise\n"
        ),
    )

    issues = check_silent_excepts.scan_file(target)

    assert issues == []


def test_scan_file_honors_inline_whitelist(tmp_path: Path) -> None:
    target = _write(
        tmp_path / "allowed.py",
        (
            "try:\n"
            "    risky()\n"
            "except Exception:  # allow-silent-except\n"
            "    log.debug('best effort cleanup failed')\n"
            "    return\n"
        ),
    )

    issues = check_silent_excepts.scan_file(target)

    assert issues == []


def test_iter_py_files_skips_pycache_and_venv(monkeypatch, tmp_path: Path) -> None:
    (tmp_path / "client").mkdir()
    (tmp_path / "client" / "ok.py").write_text("print('ok')\n", encoding="utf-8")
    (tmp_path / "client" / "__pycache__").mkdir()
    (tmp_path / "client" / "__pycache__" / "skip.py").write_text("", encoding="utf-8")
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "skip.py").write_text("", encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(check_silent_excepts, "ROOTS", ("client", ".venv"))

    files = check_silent_excepts.iter_py_files()

    assert files == [Path("client/ok.py")]
