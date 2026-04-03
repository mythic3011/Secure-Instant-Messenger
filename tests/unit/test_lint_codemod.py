from __future__ import annotations

from pathlib import Path

import pytest

from scripts import lint_codemod


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_rewrite_source_reports_strenum_change() -> None:
    source = "from enum import Enum as PyEnum\n\nclass Example(str, PyEnum):\n    A = 'a'\n"

    result = lint_codemod.rewrite_source(source)

    assert "class Example(StrEnum):" in result.source
    assert "from enum import StrEnum" in result.source
    assert result.rules == (lint_codemod.STR_ENUM_RULE,)


def test_apply_rewrites_strenum_and_only_adds_import(tmp_path: Path) -> None:
    target = _write(
        tmp_path / "enum_case.py",
        ("from enum import Enum as PyEnum\n\nclass Example(str, PyEnum):\n    A = 'a'\n"),
    )

    changed, rules = lint_codemod.process_file(target, apply=True)

    assert changed is True
    assert rules == (lint_codemod.STR_ENUM_RULE,)
    assert target.read_text(encoding="utf-8") == (
        "from enum import Enum as PyEnum\n"
        "from enum import StrEnum\n\n"
        "class Example(StrEnum):\n"
        "    A = 'a'\n"
    )


def test_apply_rewrites_raise_from_exc(tmp_path: Path) -> None:
    target = _write(
        tmp_path / "except_case.py",
        (
            "def run() -> None:\n"
            "    try:\n"
            "        risky()\n"
            "    except ValueError:\n"
            "        raise RuntimeError('bad input')\n"
        ),
    )

    changed, rules = lint_codemod.process_file(target, apply=True)

    assert changed is True
    assert rules == (lint_codemod.RAISE_FROM_RULE,)
    assert "except ValueError as exc:\n" in target.read_text(encoding="utf-8")
    assert "raise RuntimeError('bad input') from exc\n" in target.read_text(encoding="utf-8")


def test_apply_preserves_crlf_newlines(tmp_path: Path) -> None:
    target = tmp_path / "windows_case.py"
    target.write_bytes(
        b"from enum import Enum as PyEnum\r\n\r\nclass Example(str, PyEnum):\r\n    A = 'a'\r\n"
    )

    changed, rules = lint_codemod.process_file(target, apply=True)

    assert changed is True
    assert rules == (lint_codemod.STR_ENUM_RULE,)
    assert b"\r\n" in target.read_bytes()


def test_skip_complex_except_block(tmp_path: Path) -> None:
    target = _write(
        tmp_path / "complex.py",
        (
            "def run() -> None:\n"
            "    try:\n"
            "        risky()\n"
            "    except ValueError:\n"
            "        log.warning('bad input')\n"
            "        raise RuntimeError('bad input')\n"
        ),
    )

    changed, rules = lint_codemod.process_file(target, apply=False)

    assert changed is False
    assert rules == ()


def test_noop_fastapi_depends_code() -> None:
    source = "from fastapi import Depends\n\ndef route(db = Depends(get_db)) -> None:\n    pass\n"

    result = lint_codemod.rewrite_source(source)

    assert result.source == source
    assert result.rules == ()


def test_noop_verify_false_code() -> None:
    source = "client = httpx.AsyncClient(verify=False)\n"

    result = lint_codemod.rewrite_source(source)

    assert result.source == source
    assert result.rules == ()


def test_noop_non_string_enum() -> None:
    source = "from enum import Enum\n\nclass Example(Enum):\n    A = 'a'\n"

    result = lint_codemod.rewrite_source(source)

    assert result.source == source
    assert result.rules == ()


def test_noop_existing_raise_from() -> None:
    source = (
        "def run() -> None:\n"
        "    try:\n"
        "        risky()\n"
        "    except ValueError as err:\n"
        "        raise RuntimeError('bad input') from err\n"
    )

    result = lint_codemod.rewrite_source(source)

    assert result.source == source
    assert result.rules == ()


def test_noop_ambiguous_mixed_class_bases() -> None:
    source = "from enum import Enum\n\nclass Example(Tracked, str, Enum):\n    A = 'a'\n"

    result = lint_codemod.rewrite_source(source)

    assert result.source == source
    assert result.rules == ()


def test_main_requires_explicit_python_paths(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = lint_codemod.main(["--check", "README.md"])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "Only explicit .py files are supported." in captured.out


def test_main_check_exit_code_is_one_when_changes_pending(tmp_path: Path) -> None:
    target = _write(
        tmp_path / "pending.py",
        ("from enum import Enum\n\nclass Example(str, Enum):\n    A = 'a'\n"),
    )

    exit_code = lint_codemod.main(["--check", str(target)])

    assert exit_code == 1


def test_unsupported_case_reports_unchanged_not_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = _write(
        tmp_path / "unsupported.py",
        (
            "def run() -> None:\n"
            "    try:\n"
            "        risky()\n"
            "    except ValueError:\n"
            "        if flag:\n"
            "            raise RuntimeError('bad input')\n"
        ),
    )

    exit_code = lint_codemod.main(["--check", str(target)])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert f"{target}: unchanged [none]" in captured.out
