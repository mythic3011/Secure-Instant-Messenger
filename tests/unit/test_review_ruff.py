from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

import pytest

from scripts import review_ruff


def test_parse_ruff_check_groups_by_file_and_rule() -> None:
    payload = json.dumps(
        [
            {
                "filename": "client/app.py",
                "code": "F401",
                "message": "`os` imported but unused",
            },
            {
                "filename": "client/app.py",
                "code": "F401",
                "message": "`sys` imported but unused",
            },
            {
                "filename": "server/main.py",
                "code": "I001",
                "message": "Import block is un-sorted",
            },
        ]
    )

    report = review_ruff.parse_ruff_check_json(payload, exit_code=1)

    assert report.exit_code == 1
    assert report.passed is False
    assert report.files == [
        review_ruff.LintFileSummary(
            path="client/app.py",
            rule_ids=["F401"],
            count_by_rule={"F401": 2},
            total_count=2,
        ),
        review_ruff.LintFileSummary(
            path="server/main.py",
            rule_ids=["I001"],
            count_by_rule={"I001": 1},
            total_count=1,
        ),
    ]


def test_parse_ruff_check_normalizes_absolute_paths() -> None:
    payload = json.dumps(
        [
            {
                "filename": f"{review_ruff.ROOT}/client/app.py",
                "code": "F401",
                "message": "`os` imported but unused",
            }
        ]
    )

    report = review_ruff.parse_ruff_check_json(payload, exit_code=1)

    assert report.files == [
        review_ruff.LintFileSummary(
            path="client/app.py",
            rule_ids=["F401"],
            count_by_rule={"F401": 1},
            total_count=1,
        )
    ]


def test_parse_ruff_format_check_extracts_files() -> None:
    stdout = (
        "Would reformat: client/app.py\n"
        "Would reformat: server/main.py\n"
        "2 files would be reformatted, 8 files already formatted\n"
    )

    report = review_ruff.parse_ruff_format_check_output(stdout, exit_code=1)

    assert report.exit_code == 1
    assert report.passed is False
    assert report.files == ["client/app.py", "server/main.py"]


def test_consolidate_reports_prioritizes_format_first_and_counts_rules() -> None:
    lint_report = review_ruff.RuffCheckReport(
        exit_code=1,
        passed=False,
        files=[
            review_ruff.LintFileSummary(
                path="client/app.py",
                rule_ids=["F401", "I001"],
                count_by_rule={"F401": 2, "I001": 1},
                total_count=3,
            ),
            review_ruff.LintFileSummary(
                path="server/main.py",
                rule_ids=["B904"],
                count_by_rule={"B904": 1},
                total_count=1,
            ),
        ],
        raw_summary="Found 4 errors.",
    )
    format_report = review_ruff.RuffFormatReport(
        exit_code=1,
        passed=False,
        files=["client/app.py"],
        raw_summary="Would reformat: client/app.py",
    )

    consolidated = review_ruff.consolidate_reports(lint_report, format_report)

    assert consolidated.format_first_files == ["client/app.py"]
    assert consolidated.lint_only_files == ["server/main.py"]
    assert consolidated.top_recurring_rule_ids == [
        ("F401", 2),
        ("B904", 1),
        ("I001", 1),
    ]
    assert consolidated.suspicious_patterns == [
        (
            "Files failing both formatter and lint likely need mechanical "
            "cleanup before rule-level review."
        ),
        "Rule F401 repeats 2 times across the repo; check for broad unused-import churn.",
    ]
    assert consolidated.recommended_fix_batches == [
        ("Run formatter on 1 file(s), then re-run lint to shrink noise on overlapping files."),
        "Address lint-only files starting with highest-frequency rules: B904.",
    ]


def test_consolidate_reports_when_both_fail_never_claims_passed() -> None:
    lint_report = review_ruff.RuffCheckReport(
        exit_code=1,
        passed=False,
        files=[
            review_ruff.LintFileSummary(
                path="server/main.py",
                rule_ids=["B904"],
                count_by_rule={"B904": 1},
                total_count=1,
            )
        ],
        raw_summary="Found 1 error.",
    )
    format_report = review_ruff.RuffFormatReport(
        exit_code=1,
        passed=False,
        files=["client/app.py"],
        raw_summary="Would reformat: client/app.py",
    )

    consolidated = review_ruff.consolidate_reports(lint_report, format_report)

    assert consolidated.ruff_check_status == "fail"
    assert consolidated.ruff_format_status == "fail"
    assert all("passed" not in batch.lower() for batch in consolidated.recommended_fix_batches)


def test_consolidate_reports_when_only_lint_fails_stays_fail() -> None:
    lint_report = review_ruff.RuffCheckReport(
        exit_code=1,
        passed=False,
        files=[
            review_ruff.LintFileSummary(
                path="server/main.py",
                rule_ids=["B904"],
                count_by_rule={"B904": 1},
                total_count=1,
            )
        ],
        raw_summary="Found 1 error.",
    )
    format_report = review_ruff.RuffFormatReport(
        exit_code=0,
        passed=True,
        files=[],
        raw_summary="1 file already formatted",
    )

    consolidated = review_ruff.consolidate_reports(lint_report, format_report)

    assert consolidated.ruff_check_status == "fail"
    assert consolidated.ruff_format_status == "pass"
    assert consolidated.recommended_fix_batches == [
        "Address lint-only files starting with highest-frequency rules: B904."
    ]


def test_consolidate_reports_when_only_format_fails_stays_fail() -> None:
    lint_report = review_ruff.RuffCheckReport(
        exit_code=0,
        passed=True,
        files=[],
        raw_summary="0 diagnostic(s) across 0 file(s).",
    )
    format_report = review_ruff.RuffFormatReport(
        exit_code=1,
        passed=False,
        files=["client/app.py"],
        raw_summary="Would reformat: client/app.py",
    )

    consolidated = review_ruff.consolidate_reports(lint_report, format_report)

    assert consolidated.ruff_check_status == "pass"
    assert consolidated.ruff_format_status == "fail"
    assert consolidated.recommended_fix_batches == [
        "Run formatter on 1 file(s), then re-run lint to shrink noise on overlapping files."
    ]


def test_consolidate_reports_only_reports_passed_when_both_pass() -> None:
    lint_report = review_ruff.RuffCheckReport(
        exit_code=0,
        passed=True,
        files=[],
        raw_summary="0 diagnostic(s) across 0 file(s).",
    )
    format_report = review_ruff.RuffFormatReport(
        exit_code=0,
        passed=True,
        files=[],
        raw_summary="1 file already formatted",
    )

    consolidated = review_ruff.consolidate_reports(lint_report, format_report)

    assert consolidated.ruff_check_status == "pass"
    assert consolidated.ruff_format_status == "pass"
    assert consolidated.recommended_fix_batches == [
        "No fix batches needed; both Ruff entrypoints passed."
    ]


def test_render_report_json_emits_machine_readable_schema() -> None:
    report = review_ruff.ConsolidatedReport(
        ruff_check_status="fail",
        ruff_format_status="pass",
        format_first_files=["client/app.py"],
        lint_only_files=["server/main.py"],
        top_recurring_rule_ids=[("F401", 2), ("B904", 1)],
        suspicious_patterns=["Repeated unused imports."],
        recommended_fix_batches=["Run formatter first."],
    )

    rendered = review_ruff.render_report_json(report)

    assert json.loads(rendered) == {
        "ruff_check": "fail",
        "ruff_format_check": "pass",
        "format_first_files": ["client/app.py"],
        "lint_only_files": ["server/main.py"],
        "top_recurring_rule_ids": [
            {"rule_id": "F401", "count": 2},
            {"rule_id": "B904", "count": 1},
        ],
        "suspicious_patterns_worth_manual_inspection": ["Repeated unused imports."],
        "recommended_fix_batches": ["Run formatter first."],
    }


def test_render_report_dispatches_json_mode() -> None:
    report = review_ruff.ConsolidatedReport(
        ruff_check_status="pass",
        ruff_format_status="pass",
        format_first_files=[],
        lint_only_files=[],
        top_recurring_rule_ids=[],
        suspicious_patterns=[],
        recommended_fix_batches=["No fix batches needed; both Ruff entrypoints passed."],
    )

    rendered = review_ruff.render_report_for_output(report, Namespace(json=True))

    assert json.loads(rendered)["ruff_check"] == "pass"


def test_write_output_writes_json_and_creates_parent_dirs(tmp_path: Path) -> None:
    report = review_ruff.ConsolidatedReport(
        ruff_check_status="fail",
        ruff_format_status="fail",
        format_first_files=["client/app.py"],
        lint_only_files=["server/main.py"],
        top_recurring_rule_ids=[("F401", 2)],
        suspicious_patterns=["Repeated unused imports."],
        recommended_fix_batches=["Run formatter first."],
    )
    output_path = tmp_path / "reports" / "ruff-review.json"

    review_ruff.write_output(output_path, review_ruff.render_report_json(report))

    assert output_path.exists() is True
    assert json.loads(output_path.read_text(encoding="utf-8")) == {
        "ruff_check": "fail",
        "ruff_format_check": "fail",
        "format_first_files": ["client/app.py"],
        "lint_only_files": ["server/main.py"],
        "top_recurring_rule_ids": [{"rule_id": "F401", "count": 2}],
        "suspicious_patterns_worth_manual_inspection": ["Repeated unused imports."],
        "recommended_fix_batches": ["Run formatter first."],
    }
    assert output_path.read_text(encoding="utf-8").endswith("\n") is True


def test_validate_args_rejects_output_without_json() -> None:
    args = Namespace(json=False, output="reports/ruff-review.json")

    with pytest.raises(SystemExit) as excinfo:
        review_ruff.validate_args(args)

    assert excinfo.value.code == 2
