import json
from pathlib import Path

import pytest

from scripts.eval_decks import (
    collect_case_metrics,
    display_path,
    safe_case_dir,
    structure_page_headings,
    validate_case_id,
    write_markdown_report,
)


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_metrics_fail_closed_when_required_reports_are_missing(tmp_path):
    (tmp_path / "notes.md").write_text("# Demo\n\n## Slide 1\n\ncontent\n", encoding="utf-8")

    metrics = collect_case_metrics(tmp_path, 0.1, 0, "")

    assert metrics["passed"] is False
    assert metrics["gates"]["required_artifacts_present"] is False
    assert metrics["gates"]["coverage_report_valid"] is False
    assert metrics["gates"]["quality_report_valid"] is False
    assert metrics["gates"]["export_report_valid"] is False


def test_metrics_record_local_page_listing_without_false_report(tmp_path):
    (tmp_path / "notes.md").write_text("# Demo\n\n### 第 1 页：Topic\n\ncontent\n", encoding="utf-8")
    _write_json(tmp_path / "run_summary.json", {"run": {"preset": "local"}, "stage_timings": {}})
    _write_json(
        tmp_path / "coverage.json",
        {"missing": 0, "coverage_ratio": 1.0, "required_visible_coverage": {"missing": 0, "total": 0}},
    )
    _write_json(
        tmp_path / "quality_report.json",
        {"structure_contract": {"enforced": False}, "mechanical_structure_pass": True},
    )
    _write_json(
        tmp_path / "export_report.json",
        {"summary": {"blocking_failures": 0}, "results": [{"format": "markdown-zip", "status": "ok"}]},
    )

    metrics = collect_case_metrics(tmp_path, 0.1, 0, "")
    metrics["case_id"] = "local-case"
    assert metrics["passed"] is True
    assert metrics["page_listing_hits"] == ["第 1 页：Topic"]

    report = {
        "meta": {"generated_at": "now", "manifest": "m.json", "run_dir": "run"},
        "cases": [metrics],
    }
    output = tmp_path / "eval_report.md"
    write_markdown_report(report, output)
    assert "有（未作为硬门槛）" in output.read_text(encoding="utf-8")


def test_case_id_validation_blocks_path_traversal(tmp_path):
    with pytest.raises(ValueError):
        validate_case_id("../../outside")
    with pytest.raises(ValueError):
        safe_case_dir(tmp_path, "case/subdir")
    assert safe_case_dir(tmp_path, "safe-case_1").parent == tmp_path.resolve()


def test_display_path_accepts_paths_outside_repository(tmp_path):
    assert display_path(tmp_path) == str(tmp_path.resolve())


@pytest.mark.parametrize("fence", ["```markdown", "~~~markdown"])
def test_evaluation_page_heading_gate_ignores_code_examples(fence):
    closing = fence[:3]
    notes = f"# Course\n\n{fence}\n## Slide 1 - Code example\n{closing}\n\n## Slide 2 - Actual listing\n"

    assert structure_page_headings(notes) == ["Slide 2 - Actual listing"]
