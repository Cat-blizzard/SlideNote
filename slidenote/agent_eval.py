from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path
from typing import Any

from slidenote.agent_backend import run_agent_build
from slidenote.build.runner import run_build
from slidenote.llm_cache import utc_now_iso
from slidenote.utils import write_json, write_text


BUILD_DEFAULTS: dict[str, Any] = {
    "note_context": "section",
    "note_strategy": "lecture-weave",
    "deck_brief": "auto",
    "export": None,
    "export_toc": "auto",
    "asset_mode": "bundle",
    "source_display": "hidden",
    "note_style": "article",
    "note_profile": "auto",
    "note_depth": "detailed",
    "note_language": "zh",
    "term_policy": "bilingual",
    "teaching_enrichment": "auto",
    "weave_dedup": "soft",
    "page_neighborhood": 1,
    "screenshot_policy": "fallback",
    "review_mode": "off",
    "exam_mode": "off",
    "exam_question_count": 12,
}


def run_agent_eval(args: argparse.Namespace) -> int:
    output_root = args.out.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    baseline_dir = output_root / "baseline_build"
    agent_dir = output_root / "agent_build"

    baseline = _run_pipeline(
        name="baseline",
        output_dir=baseline_dir,
        runner=run_build,
        args=_baseline_args(args, baseline_dir),
    )
    agent = _run_pipeline(
        name="agent",
        output_dir=agent_dir,
        runner=run_agent_build,
        args=_agent_build_args(args, agent_dir),
    )
    report = _build_eval_report(args=args, output_root=output_root, baseline=baseline, agent=agent)
    write_json(output_root / "eval_report.json", report)
    write_text(output_root / "eval_report.md", render_eval_markdown(report))
    if not args.quiet:
        print(f"SlideNote agent eval complete: {output_root}")
        print(f"- report: {output_root / 'eval_report.md'}")
        print(f"- baseline: {baseline_dir}")
        print(f"- agent: {agent_dir}")
    return 0 if baseline["status"] == "ok" and agent["status"] == "ok" else 1


def _run_pipeline(*, name: str, output_dir: Path, runner, args: argparse.Namespace) -> dict[str, Any]:
    started = time.perf_counter()
    exit_code = 1
    error: str | None = None
    try:
        exit_code = int(runner(args) or 0)
    except Exception as exc:  # pragma: no cover - exercised through user-facing CLI behavior.
        error = str(exc)
        exit_code = 1
    duration = time.perf_counter() - started
    notes_path = output_dir / "notes.md"
    coverage_path = output_dir / "coverage.json"
    run_report_path = output_dir / "agent_run.json"
    diagnostics_path = output_dir / "agent_diagnostics.json"
    coverage = _read_json(coverage_path)
    run_report = _read_json(run_report_path)
    diagnostics = _read_json(diagnostics_path)
    status = "ok" if exit_code == 0 and error is None else "error"
    if diagnostics and diagnostics.get("message"):
        error = str(diagnostics.get("message"))
    return {
        "name": name,
        "status": status,
        "exit_code": exit_code,
        "duration_seconds": round(duration, 3),
        "output_dir": str(output_dir),
        "notes_path": str(notes_path) if notes_path.exists() else None,
        "coverage_path": str(coverage_path) if coverage_path.exists() else None,
        "error": error,
        "coverage": _coverage_summary(coverage),
        "notes": _markdown_summary(notes_path),
        "agent_run": _agent_run_summary(run_report),
        "diagnostics": diagnostics or None,
    }


def _baseline_args(args: argparse.Namespace, output_dir: Path) -> argparse.Namespace:
    values = dict(vars(args))
    values.update(BUILD_DEFAULTS)
    values.update(
        {
            "command": "build",
            "input": args.input,
            "out": output_dir,
            "progress_json": output_dir / "progress.json",
            "quiet": args.quiet,
        }
    )
    return argparse.Namespace(**values)


def _agent_build_args(args: argparse.Namespace, output_dir: Path) -> argparse.Namespace:
    values = dict(vars(args))
    values.update(
        {
            "command": "agent-build",
            "input": args.input,
            "out": output_dir,
            "progress_json": output_dir / "progress.json",
            "quiet": args.quiet,
        }
    )
    return argparse.Namespace(**values)


def _build_eval_report(
    *,
    args: argparse.Namespace,
    output_root: Path,
    baseline: dict[str, Any],
    agent: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "generated_at": utc_now_iso(),
        "source": str(args.input.resolve()),
        "output_root": str(output_root),
        "status": "ok" if baseline["status"] == "ok" and agent["status"] == "ok" else "error",
        "baseline": baseline,
        "agent": agent,
        "comparison": _comparison(baseline, agent),
        "review_checklist": _review_checklist(baseline, agent),
    }


def _coverage_summary(report: dict[str, Any] | None) -> dict[str, Any]:
    report = report or {}
    visible = report.get("visible_coverage") if isinstance(report.get("visible_coverage"), dict) else {}
    required = report.get("required_visible_coverage") if isinstance(report.get("required_visible_coverage"), dict) else {}
    figure = report.get("figure_coverage") if isinstance(report.get("figure_coverage"), dict) else {}
    page_coverage = report.get("page_coverage") if isinstance(report.get("page_coverage"), dict) else {}
    return {
        "total": int(report.get("total") or 0),
        "covered": int(report.get("covered") or 0),
        "missing": int(report.get("missing") or 0),
        "coverage_ratio": float(report.get("coverage_ratio") or 0.0),
        "visible_missing": int(visible.get("missing") or 0),
        "required_visible_missing": int(required.get("missing") or 0),
        "marker_only": int(report.get("marker_only") or 0),
        "figure_total": int(figure.get("total_figures") or 0),
        "figure_missing": int(figure.get("missing_figures") or 0),
        "figure_unexplained": int(figure.get("unexplained_note_figures") or 0),
        "figure_note_explained": int(figure.get("note_explained_figures") or 0),
        "figure_needs_review": int(figure.get("needs_review") or 0),
        "missing_slide_ids": list(page_coverage.get("missing_slide_ids") or []),
        "required_missing_items": list(required.get("missing_items") or []),
        "figures": list(figure.get("figures") or []),
    }


def _markdown_summary(notes_path: Path) -> dict[str, Any]:
    if not notes_path.exists():
        return {"exists": False}
    markdown = notes_path.read_text(encoding="utf-8")
    return {
        "exists": True,
        "chars": len(markdown),
        "words": len(re.findall(r"\S+", markdown)),
        "images": len(re.findall(r"!\[[^\]]*]\([^)]+\)", markdown)),
        "source_markers": len(re.findall(r"<!--\s*slidenote-source:", markdown)),
        "h2_sections": len(re.findall(r"^##\s+", markdown, flags=re.MULTILINE)),
    }


def _agent_run_summary(report: dict[str, Any] | None) -> dict[str, Any]:
    if not report:
        return {}
    sections = report.get("sections") if isinstance(report.get("sections"), list) else []
    repair = report.get("repair") if isinstance(report.get("repair"), dict) else {}
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    attempted_repairs = int(repair.get("attempted_sections") or 0)
    return {
        "sections_total": int(summary.get("sections_total") or len(sections)),
        "warnings": list(summary.get("warnings") or []),
        "repair": repair,
        "estimated_claude_calls": len(sections) + attempted_repairs,
    }


def _comparison(baseline: dict[str, Any], agent: dict[str, Any]) -> dict[str, Any]:
    baseline_cov = baseline.get("coverage") or {}
    agent_cov = agent.get("coverage") or {}
    baseline_notes = baseline.get("notes") or {}
    agent_notes = agent.get("notes") or {}
    metrics = [
        ("coverage_ratio", baseline_cov, agent_cov),
        ("missing", baseline_cov, agent_cov),
        ("visible_missing", baseline_cov, agent_cov),
        ("required_visible_missing", baseline_cov, agent_cov),
        ("figure_missing", baseline_cov, agent_cov),
        ("figure_unexplained", baseline_cov, agent_cov),
        ("figure_note_explained", baseline_cov, agent_cov),
        ("chars", baseline_notes, agent_notes),
        ("images", baseline_notes, agent_notes),
        ("source_markers", baseline_notes, agent_notes),
    ]
    return {
        key: {
            "baseline": left.get(key),
            "agent": right.get(key),
            "delta": _numeric_delta(left.get(key), right.get(key)),
        }
        for key, left, right in metrics
    }


def _numeric_delta(left: Any, right: Any) -> float | int | None:
    if not isinstance(left, (int, float)) or not isinstance(right, (int, float)):
        return None
    delta = right - left
    if isinstance(left, int) and isinstance(right, int):
        return int(delta)
    return round(float(delta), 4)


def _review_checklist(baseline: dict[str, Any], agent: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for label, result in (("baseline", baseline), ("agent", agent)):
        if result.get("status") != "ok":
            items.append({"pipeline": label, "kind": "pipeline_error", "message": result.get("error") or "pipeline failed"})
        coverage = result.get("coverage") or {}
        for slide_id in coverage.get("missing_slide_ids") or []:
            items.append({"pipeline": label, "kind": "missing_slide", "slide_id": slide_id})
        for missing in coverage.get("required_missing_items") or []:
            if isinstance(missing, dict):
                items.append(
                    {
                        "pipeline": label,
                        "kind": "required_visible_missing",
                        "slide_id": missing.get("slide_id"),
                        "id": missing.get("id") or missing.get("element_id"),
                        "preview": missing.get("preview"),
                    }
                )
        for figure in coverage.get("figures") or []:
            if not isinstance(figure, dict):
                continue
            if not figure.get("covered"):
                items.append({"pipeline": label, "kind": "figure_missing", "slide_id": figure.get("slide_id"), "id": figure.get("id")})
            elif not figure.get("note_explained"):
                items.append({"pipeline": label, "kind": "figure_unexplained", "slide_id": figure.get("slide_id"), "id": figure.get("id")})
    return items


def render_eval_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# SlideNote Agent Eval",
        "",
        f"- Source: `{report.get('source')}`",
        f"- Status: `{report.get('status')}`",
        f"- Baseline: `{(report.get('baseline') or {}).get('output_dir')}`",
        f"- Agent: `{(report.get('agent') or {}).get('output_dir')}`",
        "",
        "## Metrics",
        "",
        "| Metric | Baseline | Agent | Delta |",
        "| --- | ---: | ---: | ---: |",
    ]
    for key, values in (report.get("comparison") or {}).items():
        if not isinstance(values, dict):
            continue
        lines.append(
            f"| `{key}` | {_format_value(values.get('baseline'))} | {_format_value(values.get('agent'))} | {_format_value(values.get('delta'))} |"
        )
    agent_run = (report.get("agent") or {}).get("agent_run") or {}
    if agent_run:
        lines.extend(
            [
                "",
                "## Agent Run",
                "",
                f"- Sections: {agent_run.get('sections_total', 0)}",
                f"- Estimated Claude calls: {agent_run.get('estimated_claude_calls', 0)}",
                f"- Warnings: {len(agent_run.get('warnings') or [])}",
            ]
        )
        repair = agent_run.get("repair") if isinstance(agent_run.get("repair"), dict) else {}
        if repair:
            lines.extend(
                [
                    f"- Repair mode: `{repair.get('mode')}`",
                    f"- Repair attempted sections: {repair.get('attempted_sections', 0)}",
                    f"- Failed repairs: {repair.get('failed_repairs', 0)}",
                ]
            )
    checklist = report.get("review_checklist") or []
    lines.extend(["", "## Review Checklist", ""])
    if not checklist:
        lines.append("- No deterministic review items.")
    else:
        for item in checklist:
            if not isinstance(item, dict):
                continue
            detail = ", ".join(
                f"{key}={value}"
                for key, value in item.items()
                if key != "pipeline" and value is not None and value != ""
            )
            lines.append(f"- `{item.get('pipeline')}` {item.get('kind')}: {detail}")
    return "\n".join(lines).rstrip() + "\n"


def _format_value(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        value = json.loads(data)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None
