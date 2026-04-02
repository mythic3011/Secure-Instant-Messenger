#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RewriteResult:
    source: str
    rules: tuple[str, ...]


@dataclass(frozen=True)
class Replacement:
    start: int
    end: int
    text: str


STR_ENUM_RULE = "strenum"
RAISE_FROM_RULE = "raise-from"


def _line_offsets(source: str) -> list[int]:
    offsets = [0]
    running = 0
    for line in source.splitlines(keepends=True):
        running += len(line)
        offsets.append(running)
    return offsets


def _index(offsets: list[int], lineno: int, col: int) -> int:
    return offsets[lineno - 1] + col


def _apply_replacements(source: str, replacements: list[Replacement]) -> str:
    updated = source
    for replacement in sorted(replacements, key=lambda item: item.start, reverse=True):
        updated = updated[: replacement.start] + replacement.text + updated[replacement.end :]
    return updated


def _is_target_str_enum_base(node: ast.expr) -> bool:
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        return node.value.id == "enum" and node.attr == "Enum"
    if isinstance(node, ast.Name):
        return node.id in {"Enum", "PyEnum"}
    return False


def _rewrite_strenum_classes(source: str) -> RewriteResult:
    tree = ast.parse(source)
    offsets = _line_offsets(source)
    replacements: list[Replacement] = []
    changed = False

    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        if len(node.bases) != 2:
            continue
        first, second = node.bases
        if not (
            isinstance(first, ast.Name) and first.id == "str" and _is_target_str_enum_base(second)
        ):
            continue

        start = _index(offsets, first.lineno, first.col_offset)
        end = _index(offsets, second.end_lineno, second.end_col_offset)
        replacements.append(Replacement(start=start, end=end, text="StrEnum"))
        changed = True

    updated = _apply_replacements(source, replacements)
    if not changed:
        return RewriteResult(source=source, rules=())
    updated = _rewrite_enum_imports(updated)
    return RewriteResult(source=updated, rules=(STR_ENUM_RULE,))


def _collect_used_names(tree: ast.AST) -> set[str]:
    used: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            used.add(node.id)
    return used


def _rewrite_enum_imports(source: str) -> str:
    tree = ast.parse(source)
    if "StrEnum" not in _collect_used_names(tree):
        return source

    enum_imports = [
        node
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module == "enum" and node.level == 0
    ]
    for node in enum_imports:
        if any((alias.asname or alias.name) == "StrEnum" for alias in node.names):
            return source

    insert_at = _import_insertion_offset(source)
    newline = _newline_style(source)
    insertion = f"from enum import StrEnum{newline}"
    return source[:insert_at] + insertion + source[insert_at:]


def _newline_style(source: str) -> str:
    if "\r\n" in source:
        return "\r\n"
    return "\n"


def _import_insertion_offset(source: str) -> int:
    tree = ast.parse(source)
    offsets = _line_offsets(source)
    body = tree.body
    index = 0
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        if isinstance(body[0].value.value, str):
            index = offsets[body[0].end_lineno]
    future_imports = [
        node for node in body if isinstance(node, ast.ImportFrom) and node.module == "__future__"
    ]
    if future_imports:
        return offsets[future_imports[-1].end_lineno]

    imports = [node for node in body if isinstance(node, ast.Import | ast.ImportFrom)]
    if imports:
        return offsets[imports[-1].end_lineno]

    return index


def _rewrite_raise_from(source: str) -> RewriteResult:
    tree = ast.parse(source)
    offsets = _line_offsets(source)
    replacements: list[Replacement] = []
    changed = False

    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        if len(node.body) != 1:
            continue
        only_stmt = node.body[0]
        if not isinstance(only_stmt, ast.Raise):
            continue
        if only_stmt.exc is None or only_stmt.cause is not None:
            continue
        if not isinstance(only_stmt.exc, ast.Call):
            continue
        header_start = _index(offsets, node.lineno, node.col_offset)
        header_end = offsets[node.lineno]
        header = source[header_start:header_end]
        if " as exc:" not in header:
            if re.search(r"\bas\s+\w+\s*:", header):
                new_header = re.sub(r"\bas\s+\w+\s*:", " as exc:", header, count=1)
            else:
                new_header = header.replace(":", " as exc:", 1)
            replacements.append(Replacement(start=header_start, end=header_end, text=new_header))

        raise_start = _index(offsets, only_stmt.lineno, only_stmt.col_offset)
        raise_end = _index(offsets, only_stmt.end_lineno, only_stmt.end_col_offset)
        raise_text = source[raise_start:raise_end]
        if " from " in raise_text:
            continue
        replacements.append(
            Replacement(
                start=raise_start,
                end=raise_end,
                text=f"{raise_text} from exc",
            )
        )
        changed = True

    updated = _apply_replacements(source, replacements)
    if not changed:
        return RewriteResult(source=source, rules=())
    return RewriteResult(source=updated, rules=(RAISE_FROM_RULE,))


def rewrite_source(source: str) -> RewriteResult:
    current = source
    applied_rules: list[str] = []
    for rewriter in (_rewrite_strenum_classes, _rewrite_raise_from):
        result = rewriter(current)
        current = result.source
        applied_rules.extend(result.rules)
    return RewriteResult(source=current, rules=tuple(dict.fromkeys(applied_rules)))


def process_file(path: Path, *, apply: bool) -> tuple[bool, tuple[str, ...]]:
    source_bytes = path.read_bytes()
    source = source_bytes.decode("utf-8")
    result = rewrite_source(source)
    changed = result.source != source
    if apply and changed:
        path.write_bytes(result.source.encode("utf-8"))
    return changed, result.rules


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Apply narrow mechanical lint codemods to explicit Python files."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="Report files that would change.")
    mode.add_argument("--apply", action="store_true", help="Rewrite files in place.")
    parser.add_argument("paths", nargs="+", help="Explicit Python files to process.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    paths = [Path(raw_path) for raw_path in args.paths]
    if any(path.suffix != ".py" for path in paths):
        print("Only explicit .py files are supported.")
        return 2

    pending_changes = False
    for path in paths:
        try:
            changed, rules = process_file(path, apply=args.apply)
        except (OSError, SyntaxError, UnicodeDecodeError) as exc:
            print(f"{path}: error: {exc}")
            return 2

        status = "changed" if changed else "unchanged"
        rendered_rules = ", ".join(rules) if rules else "none"
        print(f"{path}: {status} [{rendered_rules}]")
        pending_changes = pending_changes or changed

    if args.check and pending_changes:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
