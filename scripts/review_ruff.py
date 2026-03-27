#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UV_CACHE_DIR = ROOT / ".uv-cache"
RUFF_CHECK_CMD = ["uv", "run", "ruff", "check", ".", "--output-format", "json"]
RUFF_FORMAT_CMD = ["uv", "run", "ruff", "format", "--check", "."]


@dataclass(frozen=True)
class LintFileSummary:
    path: str
    rule_ids: list[str]
    count_by_rule: dict[str, int]
    total_count: int


@dataclass(frozen=True)
class RuffCheckReport:
    exit_code: int
    passed: bool
    files: list[LintFileSummary]
    raw_summary: str


@dataclass(frozen=True)
class RuffFormatReport:
    exit_code: int
    passed: bool
    files: list[str]
    raw_summary: str


@dataclass(frozen=True)
class ConsolidatedReport:
    ruff_check_status: str
    ruff_format_status: str
    format_first_files: list[str]
    lint_only_files: list[str]
    top_recurring_rule_ids: list[tuple[str, int]]
    suspicious_patterns: list[str]
    recommended_fix_batches: list[str]


def run_command(command: list[str]) -> subprocess.CompletedProcess[str]:
    # Commands are fixed module constants, not user-controlled input.
    env = os.environ.copy()
    env.setdefault("UV_CACHE_DIR", str(UV_CACHE_DIR))
    return subprocess.run(  # noqa: S603
        command,
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def normalize_path(path: str) -> str:
    candidate = Path(path)
    if candidate.is_absolute():
        try:
            return str(candidate.relative_to(ROOT))
        except ValueError:
            return str(candidate)
    return str(candidate)


def parse_ruff_check_json(stdout: str, *, exit_code: int) -> RuffCheckReport:
    diagnostics = json.loads(stdout or "[]")
    grouped: dict[str, Counter[str]] = {}
    for item in diagnostics:
        path = normalize_path(item["filename"])
        rule_id = item["code"]
        grouped.setdefault(path, Counter())[rule_id] += 1

    files = [
        LintFileSummary(
            path=path,
            rule_ids=sorted(counts),
            count_by_rule=dict(sorted(counts.items())),
            total_count=sum(counts.values()),
        )
        for path, counts in sorted(grouped.items())
    ]

    raw_summary = f"{len(diagnostics)} diagnostic(s) across {len(files)} file(s)."
    return RuffCheckReport(
        exit_code=exit_code,
        passed=exit_code == 0,
        files=files,
        raw_summary=raw_summary,
    )


def parse_ruff_format_check_output(stdout: str, *, exit_code: int) -> RuffFormatReport:
    files: list[str] = []
    for line in stdout.splitlines():
        if line.startswith("Would reformat: "):
            files.append(normalize_path(line.removeprefix("Would reformat: ").strip()))

    raw_summary = stdout.strip() or "No formatter output."
    return RuffFormatReport(
        exit_code=exit_code,
        passed=exit_code == 0,
        files=files,
        raw_summary=raw_summary,
    )


def consolidate_reports(
    lint_report: RuffCheckReport, format_report: RuffFormatReport
) -> ConsolidatedReport:
    format_first = sorted(set(format_report.files))
    format_first_set = set(format_first)
    lint_only = sorted(
        summary.path for summary in lint_report.files if summary.path not in format_first_set
    )

    rule_counts: Counter[str] = Counter()
    for summary in lint_report.files:
        rule_counts.update(summary.count_by_rule)

    top_rules = sorted(rule_counts.items(), key=lambda item: (-item[1], item[0]))

    suspicious_patterns: list[str] = []
    if format_first:
        suspicious_patterns.append(
            "Files failing both formatter and lint likely need mechanical "
            "cleanup before rule-level review."
        )
    for rule_id, count in top_rules:
        if count > 1:
            if rule_id == "F401":
                suspicious_patterns.append(
                    f"Rule {rule_id} repeats {count} times across the repo; "
                    "check for broad unused-import churn."
                )
            else:
                suspicious_patterns.append(
                    f"Rule {rule_id} repeats {count} times across the repo; "
                    "inspect for copy-pasted or systematic style drift."
                )

    lint_only_rule_counts: Counter[str] = Counter()
    for summary in lint_report.files:
        if summary.path in format_first_set:
            continue
        lint_only_rule_counts.update(summary.count_by_rule)

    fix_batches: list[str] = []
    if format_first:
        fix_batches.append(
            f"Run formatter on {len(format_first)} file(s), then re-run lint "
            "to shrink noise on overlapping files."
        )
    if lint_only:
        lint_only_top_rules = sorted(
            lint_only_rule_counts.items(), key=lambda item: (-item[1], item[0])
        )
        ordered_rules = (
            ", ".join(rule_id for rule_id, _count in lint_only_top_rules[:3]) or "remaining rules"
        )
        fix_batches.append(
            f"Address lint-only files starting with highest-frequency rules: {ordered_rules}."
        )
    if lint_report.passed and format_report.passed:
        fix_batches.append("No fix batches needed; both Ruff entrypoints passed.")
    elif not fix_batches:
        fix_batches.append(
            "Review the failing Ruff entrypoint output directly; "
            "no scoped fix order could be derived."
        )

    return ConsolidatedReport(
        ruff_check_status="pass" if lint_report.passed else "fail",
        ruff_format_status="pass" if format_report.passed else "fail",
        format_first_files=format_first,
        lint_only_files=lint_only,
        top_recurring_rule_ids=top_rules,
        suspicious_patterns=suspicious_patterns,
        recommended_fix_batches=fix_batches,
    )


def render_report(report: ConsolidatedReport) -> str:
    lines = [
        f"ruff check: {report.ruff_check_status}",
        f"ruff format --check: {report.ruff_format_status}",
        "format-first files:",
    ]
    lines.extend(_render_list(report.format_first_files))
    lines.append("lint-only files:")
    lines.extend(_render_list(report.lint_only_files))
    lines.append("top recurring rule IDs:")
    lines.extend(_render_rule_counts(report.top_recurring_rule_ids))
    lines.append("suspicious patterns worth manual inspection:")
    lines.extend(_render_list(report.suspicious_patterns))
    lines.append("recommended fix batches:")
    lines.extend(_render_numbered_list(report.recommended_fix_batches))
    return "\n".join(lines)


def render_report_json(report: ConsolidatedReport) -> str:
    payload = {
        "ruff_check": report.ruff_check_status,
        "ruff_format_check": report.ruff_format_status,
        "format_first_files": report.format_first_files,
        "lint_only_files": report.lint_only_files,
        "top_recurring_rule_ids": [
            {"rule_id": rule_id, "count": count} for rule_id, count in report.top_recurring_rule_ids
        ],
        "suspicious_patterns_worth_manual_inspection": report.suspicious_patterns,
        "recommended_fix_batches": report.recommended_fix_batches,
    }
    return json.dumps(payload, indent=2, sort_keys=False, ensure_ascii=False)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run Ruff lint and format verification, then consolidate the results."
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the consolidated report as JSON instead of plain text.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Write the JSON report to the given path. Requires --json.",
    )
    args = parser.parse_args()
    validate_args(args, parser)
    return args


def validate_args(args: argparse.Namespace, parser: argparse.ArgumentParser | None = None) -> None:
    if args.output is not None and not args.json:
        if parser is not None:
            parser.error("--output requires --json")
        raise SystemExit(2)


def render_report_for_output(report: ConsolidatedReport, args: argparse.Namespace) -> str:
    if args.json:
        return render_report_json(report)
    return render_report(report)


def _render_list(items: list[str]) -> list[str]:
    if not items:
        return ["  - none"]
    return [f"  - {item}" for item in items]


def _render_rule_counts(items: list[tuple[str, int]]) -> list[str]:
    if not items:
        return ["  - none"]
    return [f"  - {rule_id}: {count}" for rule_id, count in items]


def _render_numbered_list(items: list[str]) -> list[str]:
    return [f"  {index}. {item}" for index, item in enumerate(items, start=1)]


def write_output(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{content}\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    lint_run = run_command(RUFF_CHECK_CMD)
    format_run = run_command(RUFF_FORMAT_CMD)

    lint_report = parse_ruff_check_json(lint_run.stdout, exit_code=lint_run.returncode)
    format_report = parse_ruff_format_check_output(
        format_run.stdout,
        exit_code=format_run.returncode,
    )

    consolidated = consolidate_reports(lint_report, format_report)
    rendered = render_report_for_output(consolidated, args)
    if args.output is not None:
        write_output(args.output, rendered)
        print(f"wrote report to {args.output}")
    else:
        print(rendered)
    return 0 if lint_report.passed and format_report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
