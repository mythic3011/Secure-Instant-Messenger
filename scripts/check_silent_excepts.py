#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
from pathlib import Path

ROOTS = ("client", "server", "scripts", "shared", "tests")
ALLOW_COMMENT = "allow-silent-except"
LOG_METHODS = {"debug", "info", "warning", "error", "exception", "critical"}


class SilentExceptVisitor(ast.NodeVisitor):
    def __init__(self, source_lines: list[str], path: Path) -> None:
        self.source_lines = source_lines
        self.path = path
        self.issues: list[str] = []

    def visit_Try(self, node: ast.Try) -> None:
        for handler in node.handlers:
            self._check_handler(handler)
        self.generic_visit(node)

    def _check_handler(self, handler: ast.ExceptHandler) -> None:
        if self._is_whitelisted(handler):
            return
        if not self._is_broad_except(handler):
            return
        if self._is_silent_body(handler.body):
            self.issues.append(f"{self.path}:{handler.lineno}: broad except with silent body")

    def _is_whitelisted(self, handler: ast.ExceptHandler) -> bool:
        if handler.lineno - 1 >= len(self.source_lines):
            return False
        return ALLOW_COMMENT in self.source_lines[handler.lineno - 1]

    @staticmethod
    def _is_broad_except(handler: ast.ExceptHandler) -> bool:
        if handler.type is None:
            return True
        if isinstance(handler.type, ast.Name):
            return handler.type.id == "Exception"
        if isinstance(handler.type, ast.Tuple):
            return any(
                isinstance(element, ast.Name) and element.id == "Exception"
                for element in handler.type.elts
            )
        return False

    def _is_silent_body(self, body: list[ast.stmt]) -> bool:
        if not body:
            return True

        logging_calls = 0
        control_only = False
        for stmt in body:
            if isinstance(stmt, ast.Pass | ast.Continue | ast.Return):
                control_only = True
                continue
            if self._is_log_call(stmt):
                logging_calls += 1
                continue
            return False

        if logging_calls == 0 and control_only:
            return True
        return logging_calls > 0

    @staticmethod
    def _is_log_call(stmt: ast.stmt) -> bool:
        if not isinstance(stmt, ast.Expr):
            return False
        call = stmt.value
        if not isinstance(call, ast.Call):
            return False
        func = call.func
        return isinstance(func, ast.Attribute) and func.attr in LOG_METHODS


def _in_scope(path: Path) -> bool:
    return bool(path.parts) and path.parts[0] in ROOTS


def iter_py_files(selected_paths: list[str] | None = None) -> list[Path]:
    if selected_paths is not None:
        files: list[Path] = []
        for raw_path in selected_paths:
            path = Path(raw_path)
            if (
                path.exists()
                and path.suffix == ".py"
                and _in_scope(path)
                and ".venv" not in path.parts
                and "__pycache__" not in path.parts
            ):
                files.append(path)
        return sorted(set(files))

    files: list[Path] = []
    for root in ROOTS:
        base = Path(root)
        if not base.exists():
            continue
        files.extend(
            path
            for path in base.rglob("*.py")
            if ".venv" not in path.parts and "__pycache__" not in path.parts
        )
    return sorted(files)


def scan_file(path: Path) -> list[str]:
    try:
        source = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return [f"{path}: unable to decode as utf-8"]

    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        lineno = exc.lineno or 1
        return [f"{path}:{lineno}: syntax error during scan: {exc.msg}"]

    visitor = SilentExceptVisitor(source.splitlines(), path)
    visitor.visit(tree)
    return visitor.issues


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="*")
    args = parser.parse_args()

    issues: list[str] = []
    selected_paths = args.paths or None
    for path in iter_py_files(selected_paths):
        issues.extend(scan_file(path))

    if issues:
        print("Found forbidden silent broad-except patterns:\n")
        for issue in issues:
            print(f"- {issue}")
        print(
            "\nIf a case is truly intentional, add an inline comment "
            f"`# {ALLOW_COMMENT}` on the except line and explain it."
        )
        return 1

    print("No forbidden silent broad-except patterns found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
