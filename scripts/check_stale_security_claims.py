#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path

ROOTS = ("docs", "shared", "client", "server")
INCLUDED_SUFFIXES = {".md", ".py"}
EXCLUDED_DIRS = {"docs/plans"}
ALLOW_COMMENT = "allow-stale-security-claim"


@dataclass(frozen=True)
class Rule:
    name: str
    pattern: re.Pattern[str]
    message: str


RULES = (
    Rule(
        name="conversation_id_hash_derivation",
        pattern=re.compile(r"conversation_id.*sha256\s*\(\s*sorted\s*\(", re.IGNORECASE),
        message=(
            "stale claim: conversation_id must not be documented as "
            "sha256(sorted(...)); it is server-issued"
        ),
    ),
    Rule(
        name="query_param_token_auth",
        pattern=re.compile(r"\?token=<token>|query-parameter token", re.IGNORECASE),
        message=(
            "stale claim: auth tokens must not be described as URL query "
            "parameters without an explicit 'not used' rationale"
        ),
    ),
    Rule(
        name="no_per_message_forward_secrecy",
        pattern=re.compile(r"\bNo Per-Message Forward Secrecy\b", re.IGNORECASE),
        message="stale claim: review this limitation wording before keeping it in submission docs",
    ),
    Rule(
        name="delivery_status_read",
        pattern=re.compile(r"delivery_status:.*delivered/read|delivered/read", re.IGNORECASE),
        message=(
            "stale claim: delivery status should match current sent/delivered "
            "semantics; read is reserved only"
        ),
    ),
)


def _is_excluded(path: Path) -> bool:
    as_posix = path.as_posix()
    return any(
        as_posix.startswith(excluded + "/") or as_posix == excluded
        for excluded in EXCLUDED_DIRS
    )


def _in_scope(path: Path) -> bool:
    return bool(path.parts) and path.parts[0] in ROOTS


def iter_candidate_files(selected_paths: list[str] | None = None) -> list[Path]:
    if selected_paths is not None:
        files: list[Path] = []
        for raw_path in selected_paths:
            path = Path(raw_path)
            if (
                path.exists()
                and path.is_file()
                and path.suffix in INCLUDED_SUFFIXES
                and _in_scope(path)
                and ".venv" not in path.parts
                and "__pycache__" not in path.parts
                and not _is_excluded(path)
            ):
                files.append(path)
        return sorted(set(files))

    files: list[Path] = []
    for root in ROOTS:
        base = Path(root)
        if not base.exists():
            continue
        files.extend(
            path for path in base.rglob("*")
            if path.is_file()
            and path.suffix in INCLUDED_SUFFIXES
            and ".venv" not in path.parts
            and "__pycache__" not in path.parts
            and not _is_excluded(path)
        )
    return sorted(files)


def is_whitelisted(path: Path, line: str, rule: Rule) -> bool:
    if ALLOW_COMMENT in line:
        return True
    if rule.name == "query_param_token_auth" and path.as_posix().endswith("server/main.py"):
        lowered = line.lower()
        return (
            "instead of" in lowered
            or "appear in server access logs" in lowered
            or "?token=<token>" in lowered
        )
    return False


def scan_file(path: Path) -> list[str]:
    issues: list[str] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError:
        return [f"{path}: unable to decode as utf-8"]

    for lineno, line in enumerate(lines, start=1):
        for rule in RULES:
            if not rule.pattern.search(line):
                continue
            if is_whitelisted(path, line, rule):
                continue
            issues.append(f"{path}:{lineno}: {rule.message}")
    return issues


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="*")
    args = parser.parse_args()

    issues: list[str] = []
    selected_paths = args.paths or None
    for path in iter_candidate_files(selected_paths):
        issues.extend(scan_file(path))

    if issues:
        print("Found stale security claims:\n")
        for issue in issues:
            print(f"- {issue}")
        print(
            "\nIf a match is intentional, add an inline comment "
            f"`{ALLOW_COMMENT}` and explain why the wording is still correct."
        )
        return 1

    print("No stale security claims found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
