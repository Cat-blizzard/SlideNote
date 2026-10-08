"""Run the SlideNote build pipeline over a fixed sample manifest and record
reproducible evidence for P0 review: wall time, stage timings, token usage,
cost estimate, coverage, heuristic quality and structure-contract metrics.

Typical use:

    python scripts/make_sample_deck.py --out benchmarks/samples/synthetic_course.pdf
    python scripts/eval_decks.py benchmarks/samples.manifest.json --out benchmarks/runs/local-baseline
    # after a change:
    python scripts/eval_decks.py benchmarks/samples.manifest.json --out benchmarks/runs/after-change \
        --baseline benchmarks/runs/local-baseline

Hard gates (a case fails when any is violated):
- required visible coverage missing items == 0
- no page-listing headings (lecture structure contract mechanical check)
- notes.md generated and non-empty
- build and requested exports finish without blocking errors

Heuristic scores are recorded for comparison only; they never replace the
manual spot-check described in benchmarks/README.md.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
API_ENV_KEYS = (
    "DEEPSEEK_API_KEY",
    "DASHSCOPE_API_KEY",
    "OPENAI_API_KEY",
    "OPENAI_API_BASE",
    "BAIDU_OCR_API_KEY",
    "BAIDU_OCR_SECRET_KEY",
)
CASE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
REQUIRED_ARTIFACTS = (
    "notes.md",
    "run_summary.json",
    "coverage.json",
    "quality_report.json",
    "export_report.json",
)


def display_path(path: Path, base: Path = REPO_ROOT) -> str:
    """Return a readable path without requiring it to live under the repo."""
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(base.resolve()))
    except ValueError:
        return str(resolved)


def validate_case_id(value: object) -> str:
    case_id = str(value or "")
    if not CASE_ID_RE.fullmatch(case_id):
        raise ValueError(
            "case_id must match ^[A-Za-z0-9][A-Za-z0-9._-]*$ "
            f"(got {case_id!r})"
        )
    return case_id


def safe_case_dir(root: Path, case_id: str) -> Path:
    """Resolve a case directory and prove it stays below its expected root."""
    root = root.resolve()
    candidate = (root / validate_case_id(case_id)).resolve()
    if candidate.parent != root:
        raise ValueError(f"unsafe case output path: {candidate}")
    return candidate


def env_without_api_keys() -> dict[str, str]:
    env = dict(os.environ)
    for key in API_ENV_KEYS:
        env.pop(key, None)
    return env


def read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def structure_page_headings(notes: str) -> list[str]:
    from slidenote.notes.structure import _heading_sections, _is_page_heading

    return [
        heading["title"]
        for heading in _heading_sections(notes)
        if 2 <= heading["level"] <= 4 and _is_page_heading(heading["title"])
    ]


def collect_case_metrics(output_dir: Path, wall_seconds: float, returncode: int, stderr_tail: str) -> dict:
    run_summary = read_json(output_dir / "run_summary.json") or {}
    coverage = read_json(output_dir / "coverage.json") or {}
    quality = read_json(output_dir / "quality_report.json") or {}
    usage = read_json(output_dir / "llm_usage.json") or {}
    export_report = read_json(output_dir / "export_report.json") or {}
    notes_path = output_dir / "notes.md"
    notes = notes_path.read_text(encoding="utf-8", errors="replace") if notes_path.exists() else ""
    required = coverage.get("required_visible_coverage") or {}
    artifact_presence = {name: (output_dir / name).is_file() for name in REQUIRED_ARTIFACTS}

    cost_summary = None
    try:
        from slidenote.costing import write_cost_report

        pricing = REPO_ROOT / "pricing.template.json"
        report = write_cost_report(output_dir, pricing if pricing.exists() else None, "USD")
        cost_summary = report.get("summary")
    except Exception as exc:  # cost report is optional evidence
        cost_summary = {"error": type(exc).__name__}

    timings = run_summary.get("stage_timings") or {}
    slowest = timings.get("slowest_stages") if isinstance(timings, dict) else None

    usage_summary = usage.get("summary") or {}
    export_summary = export_report.get("summary") or {}
    export_results = export_report.get("results") or []
    export_failures = [
        item
        for item in export_results
        if isinstance(item, dict) and item.get("status") == "failed"
    ]
    blocking_export_failures = export_summary.get("blocking_failures")
    # The structure contract only governs lecture note profiles. The local
    # preset is a mechanical parse preview where per-page headings are by
    # design, so its page-listing hits are recorded but not gated.
    structure_enforced = bool(quality.get("structure_contract", {}).get("enforced"))
    page_listing_hits = structure_page_headings(notes)
    required_missing = required.get("missing")
    coverage_valid = isinstance(required_missing, int) and not isinstance(required_missing, bool)
    quality_valid = isinstance(quality.get("structure_contract"), dict) and "enforced" in quality["structure_contract"]
    export_valid = (
        isinstance(blocking_export_failures, int)
        and not isinstance(blocking_export_failures, bool)
        and isinstance(export_results, list)
    )
    run_summary_valid = bool(run_summary) and isinstance(run_summary.get("run"), dict)
    gates = {
        "build_exit_code_zero": returncode == 0,
        "required_artifacts_present": all(artifact_presence.values()),
        "run_summary_valid": run_summary_valid,
        "coverage_report_valid": coverage_valid,
        "quality_report_valid": quality_valid,
        "export_report_valid": export_valid,
        "notes_nonempty": bool(notes.strip()),
        "required_visible_missing_zero": coverage_valid and required_missing == 0,
        "no_page_listing_headings": (not page_listing_hits) if structure_enforced else True,
        "no_export_blocking_failures": export_valid and blocking_export_failures == 0 and not export_failures,
    }
    return {
        "case_id_placeholder": True,
        "wall_seconds": round(wall_seconds, 3),
        "build_exit_code": returncode,
        "stderr_tail": stderr_tail[-800:],
        "artifacts": artifact_presence,
        "preset": run_summary.get("run", {}).get("preset"),
        "counts": run_summary.get("counts"),
        "slowest_stages": slowest[:5] if isinstance(slowest, list) else None,
        "coverage": {
            "missing": coverage.get("missing"),
            "coverage_ratio": coverage.get("coverage_ratio"),
            "required_visible_missing": required.get("missing"),
            "required_visible_total": required.get("total"),
        },
        "quality_heuristics": {
            key: quality.get(key)
            for key in (
                "coherence_score",
                "explanation_depth_score",
                "example_score",
                "figure_integration_score",
                "mechanical_page_listing_score",
                "self_test_score",
                "pitfall_score",
                "human_note_structure_score",
                "structure_contract_pass",
                "mechanical_structure_pass",
                "hallucination_risk",
            )
        },
        "structure_contract": quality.get("structure_contract"),
        "page_listing_hits": page_listing_hits,
        "usage": {
            key: usage_summary.get(key)
            for key in (
                "llm_calls",
                "input_tokens",
                "output_tokens",
                "total_tokens",
                "api_retries",
                "failed_contexts",
                "page_note_calls",
                "weave_calls",
                "teaching_enrichment_calls",
                "repair_calls",
            )
        },
        "cost_summary": cost_summary,
        "export_failures": export_failures,
        "gates": gates,
        "passed": all(gates.values()),
        "notes_chars": len(notes),
    }


def run_case(case: dict, run_dir: Path, preset: str | None, timeout: int) -> dict:
    try:
        case_id = validate_case_id(case.get("case_id"))
    except (AttributeError, ValueError) as exc:
        return {"case_id": str(case.get("case_id", "")) if isinstance(case, dict) else "", "error": str(exc), "passed": False}
    source_value = case.get("source")
    if not isinstance(source_value, str) or not source_value.strip():
        return {"case_id": case_id, "error": "source must be a non-empty path string", "passed": False}
    source = (REPO_ROOT / source_value).resolve()
    if not source.exists():
        return {"case_id": case_id, "error": f"source not found: {source}", "passed": False}
    case_out = safe_case_dir(run_dir / "outputs", case_id)
    if case_out.exists():
        shutil.rmtree(case_out)
    case_out.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        "-m",
        "slidenote",
        "build",
        str(source),
        "--out",
        str(case_out),
        "--preset",
        preset or case.get("preset") or "local",
    ]
    export = case.get("export") or "markdown-zip"
    if export:
        command += ["--export", export]
    env = env_without_api_keys() if (preset or case.get("preset") or "local") == "local" else dict(os.environ)
    started = time.perf_counter()
    try:
        proc = subprocess.run(
            command,
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        wall = time.perf_counter() - started
        metrics = collect_case_metrics(case_out, wall, proc.returncode, proc.stderr or "")
    except subprocess.TimeoutExpired:
        wall = time.perf_counter() - started
        metrics = {
            "wall_seconds": round(wall, 3),
            "error": f"timeout after {timeout}s",
            "passed": False,
        }
    except OSError as exc:
        wall = time.perf_counter() - started
        metrics = {
            "wall_seconds": round(wall, 3),
            "error": f"failed to launch build: {type(exc).__name__}: {exc}",
            "passed": False,
        }
    metrics["case_id"] = case_id
    metrics.pop("case_id_placeholder", None)
    metrics["command"] = command[2:]
    metrics["output_dir"] = display_path(case_out)
    return metrics


def archive_failure(case: dict, metrics: dict, run_dir: Path) -> Path | None:
    try:
        case_id = validate_case_id(case.get("case_id"))
    except (AttributeError, ValueError):
        return None
    out_dir = safe_case_dir(run_dir / "outputs", case_id)
    failure_dir = safe_case_dir(run_dir / "failures", case_id)
    failure_dir.mkdir(parents=True, exist_ok=True)
    for name in (
        "notes.md",
        "coverage.md",
        "coverage.json",
        "quality_report.json",
        "export_report.json",
        "llm_usage.json",
        "run_summary.json",
    ):
        source = out_dir / name
        if source.exists():
            shutil.copy2(source, failure_dir / name)
    (failure_dir / "eval_metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return failure_dir


def compare_with_baseline(report: dict, baseline_dir: Path) -> dict:
    baseline = read_json(baseline_dir / "eval_report.json")
    if not baseline:
        return {"error": f"no eval_report.json under {baseline_dir}"}
    baseline_by_case = {item["case_id"]: item for item in baseline.get("cases", []) if item.get("case_id")}
    comparisons = []
    for current in report["cases"]:
        case_id = current.get("case_id")
        previous = baseline_by_case.get(case_id)
        if not previous:
            comparisons.append({"case_id": case_id, "status": "new_case"})
            continue
        deltas = {}
        for key in ("wall_seconds", "notes_chars"):
            if isinstance(current.get(key), (int, float)) and isinstance(previous.get(key), (int, float)):
                deltas[key] = round(current[key] - previous[key], 3)
        for key in ("missing", "required_visible_missing"):
            cur = (current.get("coverage") or {}).get(key)
            prev = (previous.get("coverage") or {}).get(key)
            if isinstance(cur, (int, float)) and isinstance(prev, (int, float)):
                deltas[f"coverage.{key}"] = cur - prev
        cur_usage, prev_usage = current.get("usage") or {}, previous.get("usage") or {}
        for key in ("input_tokens", "output_tokens", "total_tokens", "llm_calls"):
            if isinstance(cur_usage.get(key), (int, float)) and isinstance(prev_usage.get(key), (int, float)):
                deltas[f"usage.{key}"] = cur_usage[key] - prev_usage[key]
        cur_h, prev_h = current.get("quality_heuristics") or {}, previous.get("quality_heuristics") or {}
        for key, value in cur_h.items():
            if isinstance(value, (int, float)) and isinstance(prev_h.get(key), (int, float)):
                deltas[f"quality.{key}"] = round(value - prev_h[key], 4)
        comparisons.append(
            {
                "case_id": case_id,
                "status": "regressed" if (current.get("passed") is False and previous.get("passed") is not False) else
                ("improved" if (current.get("passed") is True and previous.get("passed") is False) else "same_or_mixed"),
                "gates_now": current.get("gates"),
                "gates_baseline": previous.get("gates"),
                "deltas": deltas,
            }
        )
    return {"baseline": str(baseline_dir), "cases": comparisons}


def write_markdown_report(report: dict, path: Path) -> None:
    lines = [
        "# SlideNote 评测报告（eval_decks）",
        "",
        f"- 运行时间：{report['meta']['generated_at']}",
        f"- manifest：`{report['meta']['manifest']}`",
        f"- 通过用例：{sum(1 for c in report['cases'] if c.get('passed'))}/{len(report['cases'])}",
        "",
        "> 以下启发式分数只用于跨运行比较，不能代表内容正确性；内容结论请配合人工抽查模板使用。",
        "",
        "| 用例 | 结果 | 耗时(s) | 必讲漏项 | 缺失元素 | 逐页标题 | LLM调用 | tokens(入/出) |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for case in report["cases"]:
        coverage = case.get("coverage") or {}
        usage = case.get("usage") or {}
        heuristics = case.get("quality_heuristics") or {}
        lines.append(
            "| {id} | {status} | {wall} | {req} | {miss} | {page_listing} | {calls} | {inp}/{out} |".format(
                id=case.get("case_id", "?"),
                status="通过" if case.get("passed") else "失败",
                wall=case.get("wall_seconds", "—"),
                req=coverage.get("required_visible_missing") if coverage.get("required_visible_missing") is not None else "—",
                miss=coverage.get("missing") if coverage.get("missing") is not None else "—",
                page_listing=(
                    "有" if case.get("page_listing_hits") and not (case.get("gates") or {}).get("no_page_listing_headings")
                    else "有（未作为硬门槛）" if case.get("page_listing_hits")
                    else "无"
                ),
                calls=usage.get("llm_calls") if usage.get("llm_calls") is not None else "—",
                inp=usage.get("input_tokens") if usage.get("input_tokens") is not None else "—",
                out=usage.get("output_tokens") if usage.get("output_tokens") is not None else "—",
            )
        )
    failed = [case for case in report["cases"] if not case.get("passed")]
    if failed:
        lines += ["", "## 失败用例与证据", ""]
        for case in failed:
            lines.append(f"### {case.get('case_id')}")
            lines.append("")
            lines.append(f"- 失败门槛：{[k for k, ok in (case.get('gates') or {}).items() if not ok]}")
            if case.get("error"):
                lines.append(f"- 错误：{case['error']}")
            if case.get("stderr_tail"):
                lines.append(f"- stderr 末尾：`{case['stderr_tail'][-300:]}`")
            lines.append(f"- 证据目录：`{report['meta']['run_dir']}/failures/{case.get('case_id')}`")
            lines.append("")
    if report.get("comparison", {}).get("cases"):
        lines += ["", "## 与基线对比", "", "| 用例 | 状态 | 主要差异 |", "| --- | --- | --- |"]
        for item in report["comparison"]["cases"]:
            deltas = item.get("deltas") or {}
            summary = ", ".join(f"{k}: {v:+.3f}" if isinstance(v, float) else f"{k}: {v:+d}" for k, v in deltas.items())
            lines.append(f"| {item.get('case_id')} | {item.get('status')} | {summary or '—'} |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path, help="Path to a samples manifest JSON")
    parser.add_argument("--out", type=Path, required=True, help="Run directory (e.g. benchmarks/runs/2026-xx-baseline)")
    parser.add_argument("--preset", choices=("local", "lecture"), default=None, help="Override each case preset")
    parser.add_argument("--baseline", type=Path, default=None, help="Previous run directory to compare against")
    parser.add_argument("--timeout", type=int, default=1800, help="Per-case build timeout in seconds")
    parser.add_argument("--no-failure-archive", action="store_true", help="Do not copy failing artifacts")
    args = parser.parse_args()

    manifest_path = args.manifest.resolve()
    manifest = read_json(manifest_path)
    if not manifest or not isinstance(manifest.get("cases"), list):
        print(f"invalid manifest: {manifest_path}", file=sys.stderr)
        return 2
    case_ids: list[str] = []
    try:
        for case in manifest["cases"]:
            if not isinstance(case, dict):
                raise ValueError("every manifest case must be an object")
            case_ids.append(validate_case_id(case.get("case_id")))
    except ValueError as exc:
        print(f"invalid manifest: {exc}", file=sys.stderr)
        return 2
    if len(case_ids) != len(set(case_ids)):
        print("invalid manifest: duplicate case_id values", file=sys.stderr)
        return 2

    run_dir = args.out.resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    cases = [run_case(case, run_dir, args.preset, args.timeout) for case in manifest["cases"]]
    if not args.no_failure_archive:
        for case, metric in zip(manifest["cases"], cases):
            if not metric.get("passed"):
                archive_failure(case, metric, run_dir)

    report = {
        "meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "manifest": display_path(manifest_path),
            "run_dir": display_path(run_dir),
            "preset_override": args.preset,
            "python": sys.version.split()[0],
            "platform": platform.platform(),
        },
        "cases": cases,
    }
    if args.baseline:
        report["comparison"] = compare_with_baseline(report, args.baseline.resolve())
    (run_dir / "eval_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    write_markdown_report(report, run_dir / "eval_report.md")

    failed = [case for case in cases if not case.get("passed")]
    print(f"cases: {len(cases)}, passed: {len(cases) - len(failed)}, failed: {len(failed)}")
    print(f"report: {run_dir / 'eval_report.md'}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
