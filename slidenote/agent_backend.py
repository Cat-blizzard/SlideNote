from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from dataclasses import fields
from pathlib import Path
from typing import Any

from slidenote.build.config import _apply_speed_mode_defaults, _friendly_build_error
from slidenote.build.stages import (
    _stage_composite_figures,
    _stage_content_guard,
    _stage_export_content,
    _stage_figure_crop,
    _stage_figure_grounding,
    _stage_image_importance,
    _stage_modality,
    _stage_ocr,
    _stage_parse,
    _stage_sections,
    _stage_semantic_layout,
    _stage_table_understanding,
    _stage_vision,
)
from slidenote.build.state import BuildState, create_build_state
from slidenote.content_guard import required_items_for_slides
from slidenote.coverage import analyze_coverage, render_coverage_markdown
from slidenote.ir import iter_expected_source_elements
from slidenote.llm_cache import utc_now_iso
from slidenote.models import Deck, ImageAsset, SlidePage, TableBlock, TextBlock
from slidenote.notes.assembly import _section_contexts
from slidenote.source_map import build_source_map
from slidenote.table_understanding import table_preview
from slidenote.utils import ensure_clean_dir, write_json, write_text


AGENT_PACK_SCHEMA_VERSION = 1
AGENT_RESULT_SCHEMA_VERSION = 1


class AgentBackendError(Exception):
    """User-facing error for experimental agent backend commands."""


AGENT_PACK_STAGES = (
    _stage_parse,
    _stage_modality,
    _stage_table_understanding,
    _stage_semantic_layout,
    _stage_composite_figures,
    _stage_figure_crop,
    _stage_image_importance,
    _stage_ocr,
    _stage_vision,
    _stage_figure_grounding,
    _stage_sections,
    _stage_content_guard,
    _stage_export_content,
)


def run_agent_pack(args: argparse.Namespace) -> int:
    state = _create_agent_pack_state(args)
    try:
        for stage in AGENT_PACK_STAGES:
            stage(state)
        report = build_agent_pack_from_state(state)
        state.progress.complete("Agent pack complete")
    except Exception as exc:
        state.progress.fail(str(exc))
        friendly = _friendly_build_error(exc, args)
        raise AgentBackendError(friendly or str(exc)) from exc

    if not args.quiet:
        print(f"SlideNote agent pack complete: {report['pack_dir']}")
        print(f"- manifest: {Path(report['pack_dir']) / 'manifest.json'}")
        print(f"- sections: {report['summary']['sections_total']}")
        print(f"- assets:   {report['summary']['assets_total']}")
    return 0


def run_agent_run(args: argparse.Namespace) -> int:
    if args.backend != "claude":
        raise AgentBackendError("Only the `claude` backend is supported in this experimental version.")
    pack_dir = args.agent_pack_dir.resolve()
    out = (args.out.resolve() if getattr(args, "out", None) else pack_dir.parent.resolve())
    try:
        report = run_claude_agent_pack(
            pack_dir=pack_dir,
            output_root=out,
            claude_command=args.claude_command,
            claude_model=args.claude_model,
            max_budget_usd=args.max_budget_usd,
            timeout_seconds=args.claude_timeout,
            repair_mode=args.repair,
            repair_rounds=args.repair_rounds,
            quiet=args.quiet,
        )
    except AgentBackendError as exc:
        _write_agent_diagnostics(out, {"status": "error", "message": str(exc)})
        print(f"Agent run failed: {exc}", file=sys.stderr)
        return 1

    if not args.quiet:
        print(f"SlideNote agent run complete: {out}")
        print(f"- notes:    {out / 'notes.md'}")
        print(f"- coverage: {out / 'coverage.md'}")
        print(f"- sources:  {out / 'source_map.json'}")
        print(f"- report:   {out / 'agent_run.json'}")
        if report["summary"].get("warnings"):
            print(f"- warnings: {len(report['summary']['warnings'])}")
    return 0


def run_agent_build(args: argparse.Namespace) -> int:
    pack_status = run_agent_pack(args)
    if pack_status != 0:
        return pack_status
    args.agent_pack_dir = args.out.resolve() / "agent_pack"
    return run_agent_run(args)


def _create_agent_pack_state(args: argparse.Namespace) -> BuildState:
    _apply_speed_mode_defaults(args)
    _fill_agent_pack_defaults(args)
    try:
        return create_build_state(args, export_formats=[])
    except Exception as exc:
        raise AgentBackendError(str(exc)) from exc


def _fill_agent_pack_defaults(args: argparse.Namespace) -> None:
    defaults = {
        "note_context": "section",
        "note_strategy": "lecture-weave",
        "deck_brief": "off",
        "export": None,
        "export_toc": "auto",
        "asset_mode": "bundle",
        "source_display": "hidden",
        "note_style": "article",
        "note_depth": "detailed",
        "note_language": "zh",
        "term_policy": "bilingual",
        "weave_dedup": "soft",
        "page_neighborhood": 1,
        "screenshot_policy": "fallback",
    }
    for name, value in defaults.items():
        if not hasattr(args, name):
            setattr(args, name, value)


def build_agent_pack_from_state(state: BuildState) -> dict[str, Any]:
    if state.deck is None:
        raise AgentBackendError("Cannot build an agent pack before parsing the source deck.")
    deck = state.deck
    output_root = state.output_root
    pack_dir = output_root / "agent_pack"
    sections_dir = pack_dir / "sections"
    assets_dir = pack_dir / "assets"
    ensure_clean_dir(sections_dir)
    ensure_clean_dir(assets_dir)

    asset_map, assets, asset_warnings = _copy_agent_assets(deck, output_root, pack_dir)
    contexts = _section_contexts(deck, section_plan=state.section_report)
    expected_by_slide = _expected_source_ids_by_slide(deck)
    content_guard = state.content_guard_report
    section_records: list[dict[str, Any]] = []
    for index, context in enumerate(contexts, start=1):
        file_name = f"section_{index:03d}.md"
        relative_file = f"sections/{file_name}"
        markdown = _render_section_pack_markdown(
            deck=deck,
            index=index,
            context=context,
            asset_map=asset_map,
            expected_by_slide=expected_by_slide,
            content_guard=content_guard,
            figure_placement=state.args.figure_placement,
        )
        write_text(sections_dir / file_name, markdown)
        source_ids = _source_ids_for_pages(context.pages, expected_by_slide)
        section_records.append(
            {
                "section_id": context.id,
                "title": context.title,
                "slide_ids": [page.slide_id for page in context.pages],
                "file": relative_file,
                "source_ids": source_ids,
                "required_source_ids": [
                    str(item.get("element_id"))
                    for item in required_items_for_slides(content_guard, {page.slide_id for page in context.pages})
                    if item.get("element_id")
                ],
            }
        )

    write_text(pack_dir / "style.md", _agent_style_markdown())
    write_text(pack_dir / "skill.md", _agent_skill_markdown())
    manifest = {
        "schema_version": AGENT_PACK_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "source": {
            "path": deck.source_path,
            "type": deck.source_type,
            "title": _deck_title(deck),
        },
        "output_rules": {
            "result_schema_version": AGENT_RESULT_SCHEMA_VERSION,
            "markdown_must_reference_existing_assets": True,
            "source_marker_format": "<!-- slidenote-source: p{slide_id}:{comma_separated_element_ids} -->",
            "image_path_root": "assets/",
        },
        "sections": section_records,
        "assets": assets,
        "artifacts": {
            "style": "style.md",
            "skill": "skill.md",
            "content": "../content.json",
            "element_ir": "../element_ir.json",
            "sections": "../sections.json",
        },
        "deck": deck.to_dict(),
        "section_plan": state.section_report,
        "content_guard": content_guard,
        "warnings": asset_warnings,
    }
    write_json(pack_dir / "manifest.json", manifest)
    report = {
        "schema_version": 1,
        "generated_at": utc_now_iso(),
        "pack_dir": str(pack_dir),
        "summary": {
            "sections_total": len(section_records),
            "assets_total": len(assets),
            "warnings": asset_warnings,
        },
        "manifest": "agent_pack/manifest.json",
    }
    write_json(output_root / "agent_pack_report.json", report)
    state.artifacts.register("agent_pack", pack_dir / "manifest.json")
    state.artifacts.register("agent_pack_report", output_root / "agent_pack_report.json")
    return report


def run_claude_agent_pack(
    *,
    pack_dir: Path,
    output_root: Path,
    claude_command: str,
    claude_model: str | None,
    max_budget_usd: float | None,
    timeout_seconds: int,
    quiet: bool,
    repair_mode: str = "auto",
    repair_rounds: int = 1,
) -> dict[str, Any]:
    manifest_path = pack_dir / "manifest.json"
    if not manifest_path.exists():
        raise AgentBackendError(f"Agent pack manifest not found: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if int(manifest.get("schema_version") or 0) != AGENT_PACK_SCHEMA_VERSION:
        raise AgentBackendError("Unsupported agent pack schema version.")
    if repair_mode not in {"auto", "off"}:
        raise AgentBackendError("Repair mode must be `auto` or `off`.")
    if repair_rounds not in {0, 1}:
        raise AgentBackendError("First version supports only --repair-rounds 0 or 1.")

    ensure_clean_dir(output_root)
    _copy_pack_assets_to_output(pack_dir, output_root)
    known_assets = {str(asset.get("path")).replace("\\", "/") for asset in manifest.get("assets", []) if asset.get("path")}
    asset_path_by_id = _agent_asset_path_by_id(manifest)
    deck = _deck_from_manifest(manifest)
    source_id_to_slide = _source_id_to_slide(deck)
    section_results: list[dict[str, Any]] = []
    warnings: list[str] = []
    agent_sections_dir = output_root / "agent_sections"
    ensure_clean_dir(agent_sections_dir)

    for index, section in enumerate(manifest.get("sections") or [], start=1):
        section_file = pack_dir / str(section.get("file") or "")
        if not section_file.exists():
            raise AgentBackendError(f"Agent section file not found: {section_file}")
        if not quiet:
            print(f"Running Claude Code for section {index}: {section.get('title') or section.get('section_id')}")
        prompt = _build_claude_prompt(manifest, section, section_file)
        parsed, metadata = _run_claude_command(
            prompt=prompt,
            pack_dir=pack_dir,
            claude_command=claude_command,
            claude_model=claude_model,
            max_budget_usd=max_budget_usd,
            timeout_seconds=timeout_seconds,
        )
        markdown = str(parsed["markdown"]).strip()
        _validate_asset_references(markdown, parsed["used_asset_paths"], known_assets)
        markdown = _ensure_covered_markers(markdown, parsed["covered_source_ids"], source_id_to_slide)
        section_output = agent_sections_dir / f"section_{index:03d}.md"
        write_text(section_output, markdown.rstrip() + "\n")
        warnings.extend(str(item) for item in parsed.get("warnings") or [])
        section_results.append(
            {
                "section_id": section.get("section_id"),
                "title": section.get("title"),
                "slide_ids": section.get("slide_ids") or [],
                "file": f"agent_sections/section_{index:03d}.md",
                "used_asset_paths": parsed["used_asset_paths"],
                "covered_source_ids": parsed["covered_source_ids"],
                "warnings": parsed.get("warnings") or [],
                "claude": metadata,
            }
        )

    notes_markdown = _compose_agent_notes(manifest, section_results, output_root)
    coverage_report = analyze_coverage(deck, notes_markdown, content_guard=manifest.get("content_guard"))
    repair_report = _empty_repair_report(repair_mode=repair_mode, repair_rounds=repair_rounds, coverage_before=coverage_report)
    if repair_mode == "auto" and repair_rounds > 0:
        repair_report = _repair_agent_sections_once(
            manifest=manifest,
            pack_dir=pack_dir,
            output_root=output_root,
            deck=deck,
            section_results=section_results,
            coverage_report=coverage_report,
            known_assets=known_assets,
            asset_path_by_id=asset_path_by_id,
            source_id_to_slide=source_id_to_slide,
            claude_command=claude_command,
            claude_model=claude_model,
            max_budget_usd=max_budget_usd,
            timeout_seconds=timeout_seconds,
            quiet=quiet,
        )
        notes_markdown = _compose_agent_notes(manifest, section_results, output_root)
        coverage_report = analyze_coverage(deck, notes_markdown, content_guard=manifest.get("content_guard"))
        repair_report["after"] = _coverage_repair_summary(coverage_report)
    write_text(output_root / "notes.md", notes_markdown)
    write_json(output_root / "coverage.json", coverage_report)
    write_text(output_root / "coverage.md", render_coverage_markdown(coverage_report))
    source_map = build_source_map(deck, notes_markdown, output_root)
    write_json(output_root / "source_map.json", source_map)
    run_report = {
        "schema_version": 1,
        "generated_at": utc_now_iso(),
        "backend": "claude",
        "agent_pack": str(pack_dir),
        "summary": {
            "sections_total": len(section_results),
            "warnings": _agent_result_warnings(warnings, section_results, repair_report),
            "coverage_missing": coverage_report.get("missing"),
            "coverage_ratio": coverage_report.get("coverage_ratio"),
        },
        "repair": repair_report,
        "sections": section_results,
    }
    write_json(output_root / "agent_run.json", run_report)
    return run_report


def _empty_repair_report(repair_mode: str, repair_rounds: int, coverage_before: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "mode": repair_mode,
        "rounds_requested": repair_rounds,
        "rounds_completed": 0,
        "attempted_sections": 0,
        "failed_repairs": 0,
        "unmapped_issues": [],
        "before": _coverage_repair_summary(coverage_before),
        "after": _coverage_repair_summary(coverage_before),
        "sections": [],
    }


def _repair_agent_sections_once(
    *,
    manifest: dict[str, Any],
    pack_dir: Path,
    output_root: Path,
    deck: Deck,
    section_results: list[dict[str, Any]],
    coverage_report: dict[str, Any],
    known_assets: set[str],
    asset_path_by_id: dict[str, str],
    source_id_to_slide: dict[str, int],
    claude_command: str,
    claude_model: str | None,
    max_budget_usd: float | None,
    timeout_seconds: int,
    quiet: bool,
) -> dict[str, Any]:
    report = _empty_repair_report("auto", 1, coverage_report)
    issues_by_section, unmapped = _repair_issues_by_section(
        coverage_report,
        section_results,
        source_id_to_slide,
        asset_path_by_id=asset_path_by_id,
    )
    report["unmapped_issues"] = unmapped
    if not issues_by_section:
        return report

    section_records = manifest.get("sections") or []
    sections_by_id = {str(section.get("section_id")): section for section in section_records if section.get("section_id")}
    for result in section_results:
        section_id = str(result.get("section_id") or "")
        issues = issues_by_section.get(section_id)
        if not issues:
            result.setdefault("repair_attempted", False)
            result.setdefault("repair_status", "not_needed")
            continue
        section = sections_by_id.get(section_id)
        if not section:
            warning = f"repair_section_manifest_missing:{section_id}"
            result.setdefault("warnings", []).append(warning)
            report.setdefault("unmapped_issues", []).extend(issues)
            continue
        section_file = pack_dir / str(section.get("file") or "")
        current_markdown_path = output_root / str(result.get("file") or "")
        current_markdown = current_markdown_path.read_text(encoding="utf-8")
        result["repair_attempted"] = True
        result["repair_status"] = "attempted"
        result["missing_before"] = issues
        if not quiet:
            print(f"Repairing Claude Code section: {result.get('title') or section_id}")
        try:
            prompt = _build_claude_repair_prompt(manifest, section, section_file, current_markdown, issues)
            parsed, metadata = _run_claude_command(
                prompt=prompt,
                pack_dir=pack_dir,
                claude_command=claude_command,
                claude_model=claude_model,
                max_budget_usd=max_budget_usd,
                timeout_seconds=timeout_seconds,
            )
            markdown = str(parsed["markdown"]).strip()
            _validate_asset_references(markdown, parsed["used_asset_paths"], known_assets)
            markdown = _ensure_covered_markers(markdown, parsed["covered_source_ids"], source_id_to_slide)
            write_text(current_markdown_path, markdown.rstrip() + "\n")
            result["repair_status"] = "ok"
            result["repair"] = {
                "used_asset_paths": parsed["used_asset_paths"],
                "covered_source_ids": parsed["covered_source_ids"],
                "warnings": parsed.get("warnings") or [],
                "claude": metadata,
            }
            result.setdefault("warnings", []).extend(str(item) for item in parsed.get("warnings") or [])
        except AgentBackendError as exc:
            result["repair_status"] = "failed"
            result["repair_error"] = str(exc)
            result.setdefault("warnings", []).append(f"repair_failed:{exc}")
        report["sections"].append(
            {
                "section_id": section_id,
                "title": result.get("title"),
                "status": result.get("repair_status"),
                "missing_before": issues,
                "error": result.get("repair_error"),
            }
        )

    final_notes = _compose_agent_notes(manifest, section_results, output_root)
    final_coverage = analyze_coverage(deck, final_notes, content_guard=manifest.get("content_guard"))
    final_issues_by_section, final_unmapped = _repair_issues_by_section(
        final_coverage,
        section_results,
        source_id_to_slide,
        asset_path_by_id=asset_path_by_id,
    )
    for result in section_results:
        if result.get("repair_attempted"):
            result["missing_after"] = final_issues_by_section.get(str(result.get("section_id") or ""), [])
    for section_record in report["sections"]:
        section_id = str(section_record.get("section_id") or "")
        section_record["missing_after"] = final_issues_by_section.get(section_id, [])

    report["rounds_completed"] = 1
    report["attempted_sections"] = sum(1 for result in section_results if result.get("repair_attempted"))
    report["failed_repairs"] = sum(1 for result in section_results if result.get("repair_status") == "failed")
    report["unmapped_issues"].extend(final_unmapped)
    report["after"] = _coverage_repair_summary(final_coverage)
    return report


def _repair_issues_by_section(
    coverage_report: dict[str, Any],
    section_results: list[dict[str, Any]],
    source_id_to_slide: dict[str, int],
    *,
    asset_path_by_id: dict[str, str] | None = None,
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    issues = _repair_issues_from_coverage(coverage_report, asset_path_by_id=asset_path_by_id)
    section_by_slide: dict[int, str] = {}
    for section in section_results:
        section_id = str(section.get("section_id") or "")
        for slide_id in section.get("slide_ids") or []:
            section_by_slide[int(slide_id)] = section_id
    grouped: dict[str, list[dict[str, Any]]] = {}
    unmapped: list[dict[str, Any]] = []
    for issue in issues:
        source_id = str(issue.get("id") or "")
        slide_id = int(issue.get("slide_id") or source_id_to_slide.get(source_id) or 0)
        section_id = section_by_slide.get(slide_id)
        if not section_id:
            unmapped.append(issue)
            continue
        grouped.setdefault(section_id, []).append(issue)
    return grouped, unmapped


def _repair_issues_from_coverage(
    coverage_report: dict[str, Any],
    *,
    asset_path_by_id: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    asset_path_by_id = asset_path_by_id or {}
    by_id: dict[str, dict[str, Any]] = {}
    for item in coverage_report.get("items") or []:
        if not isinstance(item, dict):
            continue
        if item.get("trace_covered"):
            continue
        issue = _repair_issue_record(item)
        issue["trace_missing"] = True
        by_id[issue["id"]] = issue
    required = coverage_report.get("required_visible_coverage")
    missing_required = required.get("missing_items") if isinstance(required, dict) else []
    for item in missing_required or []:
        if not isinstance(item, dict):
            continue
        issue_id = str(item.get("id") or item.get("element_id") or "")
        if not issue_id:
            continue
        issue = by_id.get(issue_id) or _repair_issue_record(item)
        issue["required_visible_missing"] = True
        by_id[issue_id] = issue
    figure_coverage = coverage_report.get("figure_coverage")
    figures = figure_coverage.get("figures") if isinstance(figure_coverage, dict) else []
    for figure in figures or []:
        if not isinstance(figure, dict):
            continue
        issue_id = str(figure.get("id") or "")
        if not issue_id:
            continue
        figure_missing = not bool(figure.get("covered"))
        figure_unexplained = bool(figure.get("covered")) and not bool(figure.get("note_explained"))
        figure_needs_review = figure.get("figure_audit_status") == "needs_review" and (figure_missing or figure_unexplained)
        if not (figure_missing or figure_unexplained or figure_needs_review):
            continue
        issue = by_id.get(issue_id) or _repair_issue_record(figure)
        issue.update(
            {
                "kind": "image",
                "figure_missing": bool(issue.get("figure_missing")) or figure_missing,
                "figure_unexplained": bool(issue.get("figure_unexplained")) or figure_unexplained,
                "figure_needs_review": bool(issue.get("figure_needs_review")) or figure_needs_review,
                "asset_path": asset_path_by_id.get(issue_id) or figure.get("path"),
                "raw_image_path": figure.get("path"),
                "role": figure.get("role"),
                "source_element_ids": figure.get("source_element_ids") or [],
                "anchor_element_ids": figure.get("anchor_element_ids") or [],
                "figure_explanation_status": figure.get("figure_explanation_status"),
                "figure_audit_status": figure.get("figure_audit_status"),
                "note_explained": bool(figure.get("note_explained")),
                "matched_markdown_targets": figure.get("matched_markdown_targets") or [],
            }
        )
        by_id[issue_id] = issue
    return sorted(by_id.values(), key=lambda item: (int(item.get("slide_id") or 0), str(item.get("id") or "")))


def _repair_issue_record(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(item.get("id") or item.get("element_id") or ""),
        "slide_id": int(item.get("slide_id") or 0),
        "kind": item.get("kind"),
        "preview": item.get("preview"),
        "trace_missing": False,
        "required_visible_missing": False,
        "figure_missing": False,
        "figure_unexplained": False,
        "figure_needs_review": False,
    }


def _coverage_repair_summary(coverage_report: dict[str, Any]) -> dict[str, Any]:
    required = coverage_report.get("required_visible_coverage")
    required_missing = required.get("missing") if isinstance(required, dict) else 0
    figure_coverage = coverage_report.get("figure_coverage")
    figure_missing = figure_coverage.get("missing_figures") if isinstance(figure_coverage, dict) else 0
    figure_unexplained = figure_coverage.get("unexplained_note_figures") if isinstance(figure_coverage, dict) else 0
    figure_note_explained = figure_coverage.get("note_explained_figures") if isinstance(figure_coverage, dict) else 0
    figure_needs_review = figure_coverage.get("needs_review") if isinstance(figure_coverage, dict) else 0
    return {
        "trace_missing": int(coverage_report.get("missing") or 0),
        "required_visible_missing": int(required_missing or 0),
        "figure_missing": int(figure_missing or 0),
        "figure_unexplained": int(figure_unexplained or 0),
        "figure_note_explained": int(figure_note_explained or 0),
        "figure_needs_review": int(figure_needs_review or 0),
        "coverage_ratio": coverage_report.get("coverage_ratio"),
    }


def _agent_result_warnings(initial_warnings: list[str], section_results: list[dict[str, Any]], repair_report: dict[str, Any]) -> list[str]:
    warnings: list[str] = list(initial_warnings)
    for section in section_results:
        warnings.extend(str(item) for item in section.get("warnings") or [])
    if repair_report.get("unmapped_issues"):
        warnings.append(f"repair_unmapped_issues:{len(repair_report['unmapped_issues'])}")
    if repair_report.get("failed_repairs"):
        warnings.append(f"repair_failed_sections:{repair_report['failed_repairs']}")
    return warnings


def _agent_asset_path_by_id(manifest: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for asset in manifest.get("assets") or []:
        if not isinstance(asset, dict):
            continue
        asset_id = str(asset.get("id") or "")
        path = str(asset.get("path") or "").replace("\\", "/")
        if asset_id and path:
            result[asset_id] = path
    return result


def _copy_agent_assets(deck: Deck, output_root: Path, pack_dir: Path) -> tuple[dict[str, str], list[dict[str, Any]], list[str]]:
    asset_map: dict[str, str] = {}
    assets: list[dict[str, Any]] = []
    warnings: list[str] = []
    seen_destinations: set[Path] = set()

    def add_asset(raw_path: str | None, *, asset_id: str, slide_id: int, kind: str, image: ImageAsset | None = None) -> None:
        if not raw_path or raw_path in asset_map:
            return
        source = _resolve_asset_source(output_root, raw_path)
        if not source.exists():
            warnings.append(f"Missing asset for agent pack: {raw_path}")
            return
        destination = _asset_destination(pack_dir, raw_path, kind, seen_destinations)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        seen_destinations.add(destination)
        display_path = destination.relative_to(pack_dir).as_posix()
        asset_map[raw_path] = display_path
        record = {
            "id": asset_id,
            "slide_id": slide_id,
            "kind": kind,
            "path": display_path,
            "original_path": raw_path,
        }
        if image is not None:
            record.update(
                {
                    "caption": image.caption,
                    "role": image.role,
                    "source_element_ids": list(image.source_element_ids),
                    "anchor_element_ids": list(image.anchor_element_ids),
                    "importance_score": image.importance_score,
                    "importance_rank": image.importance_rank,
                    "visual_summary": image.visual_summary,
                    "ocr_text": image.ocr_text,
                }
            )
        assets.append(record)

    for page in deck.pages:
        add_asset(page.page_screenshot, asset_id=f"p{page.slide_id}_screenshot", slide_id=page.slide_id, kind="screenshots")
        for image in page.images:
            if image.ignored:
                continue
            kind = "figures" if image.role in {"figure_crop", "composite_figure"} else "images"
            add_asset(image.path, asset_id=image.id, slide_id=page.slide_id, kind=kind, image=image)
    return asset_map, assets, warnings


def _render_section_pack_markdown(
    *,
    deck: Deck,
    index: int,
    context: Any,
    asset_map: dict[str, str],
    expected_by_slide: dict[int, list[str]],
    content_guard: dict[str, Any] | None,
    figure_placement: str,
) -> str:
    slide_ids = [page.slide_id for page in context.pages]
    required_items = required_items_for_slides(content_guard, set(slide_ids))
    lines = [
        f"# Section {index}: {context.title}",
        "",
        f"- section_id: `{context.id}`",
        f"- slide_ids: {', '.join(str(slide_id) for slide_id in slide_ids)}",
        f"- preferred_figure_placement: `{figure_placement}`",
        "",
        "## Required Output",
        "",
        "Return one JSON object to SlideNote. Do not write files. The final `markdown` value should be polished study notes, keep source markers, and reference only existing `assets/...` paths.",
        "",
        "## Section Source IDs",
        "",
        ", ".join(f"`{source_id}`" for source_id in _source_ids_for_pages(context.pages, expected_by_slide)) or "None",
        "",
    ]
    if required_items:
        lines.extend(["## Required Visible Coverage", ""])
        for item in required_items:
            lines.append(f"- `{item.get('element_id')}`: {item.get('preview') or item.get('reason') or ''}")
        lines.append("")
    lines.extend(["## Pages", ""])
    for page in context.pages:
        lines.extend(_render_page_pack_markdown(deck, page, asset_map, expected_by_slide))
    return "\n".join(lines).rstrip() + "\n"


def _render_page_pack_markdown(deck: Deck, page: SlidePage, asset_map: dict[str, str], expected_by_slide: dict[int, list[str]]) -> list[str]:
    del deck
    lines = [
        f"### Page {page.slide_id}: {page.title or 'Untitled'}",
        "",
        f"- source_ids: {', '.join(f'`{source_id}`' for source_id in expected_by_slide.get(page.slide_id, [])) or 'None'}",
        f"- page_modality: {page.page_modality or 'unknown'}",
    ]
    if page.page_screenshot and page.page_screenshot in asset_map:
        lines.append(f"- page_screenshot: `{asset_map[page.page_screenshot]}`")
    if page.page_ocr_text:
        lines.append(f"- page_ocr_text: {page.page_ocr_text}")
    if page.page_visual_summary:
        lines.append(f"- page_visual_summary: {page.page_visual_summary}")
    lines.append("")
    if page.text_blocks:
        lines.extend(["#### Text Blocks", ""])
        for block in page.text_blocks:
            content = re.sub(r"\s+", " ", block.content).strip()
            lines.append(f"- `{block.id}` ({block.type}): {content}")
        lines.append("")
    if page.tables:
        lines.extend(["#### Tables", ""])
        for table in page.tables:
            summary = table.table_summary or table.table_conclusion or table_preview(table)
            lines.append(f"- `{table.id}`: {summary}")
            if table.rows:
                for row in table.rows[:4]:
                    lines.append(f"  - {' | '.join(cell.strip() for cell in row)}")
        lines.append("")
    visible_images = [image for image in page.images if not image.ignored and image.path in asset_map]
    if visible_images:
        lines.extend(["#### Images And Figures", ""])
        for image in visible_images:
            display_path = asset_map[image.path]
            source_ids = ", ".join(f"`{source_id}`" for source_id in [image.id, *image.source_element_ids] if source_id)
            anchors = ", ".join(f"`{anchor_id}`" for anchor_id in image.anchor_element_ids)
            caption = image.caption or image.id
            lines.append(f"- `{image.id}`: `{display_path}`")
            lines.append(f"  - caption: {caption}")
            if source_ids:
                lines.append(f"  - source_ids: {source_ids}")
            if anchors:
                lines.append(f"  - anchor_element_ids: {anchors}")
            if image.visual_summary:
                lines.append(f"  - visual_summary: {image.visual_summary}")
            if image.ocr_text:
                lines.append(f"  - ocr_text: {image.ocr_text}")
            if image.figure_explanation:
                lines.append(f"  - existing_figure_explanation: {image.figure_explanation}")
            if image.figure_explanation_status:
                lines.append(f"  - figure_explanation_status: {image.figure_explanation_status}")
            if image.figure_audit_status:
                lines.append(f"  - figure_audit_status: {image.figure_audit_status}")
            if image.importance_score is not None:
                lines.append(f"  - importance_score: {image.importance_score}")
            if image.importance_rank is not None:
                lines.append(f"  - importance_rank: {image.importance_rank}")
            lines.append(f"  - markdown_reference: ![{caption}]({display_path})")
        lines.append("")
    return lines


def _run_claude_command(
    *,
    prompt: str,
    pack_dir: Path,
    claude_command: str,
    claude_model: str | None,
    max_budget_usd: float | None,
    timeout_seconds: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    cmd = [claude_command, "-p", "--bare", "--output-format", "json", "--add-dir", str(pack_dir)]
    if claude_model:
        cmd.extend(["--model", claude_model])
    if max_budget_usd is not None:
        cmd.extend(["--max-budget-usd", str(max_budget_usd)])
    cmd.append(prompt)
    try:
        completed = subprocess.run(
            cmd,
            cwd=str(pack_dir),
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise AgentBackendError(f"Claude Code timed out after {timeout_seconds} seconds.") from exc
    except OSError as exc:
        raise AgentBackendError(f"Could not run Claude Code command `{claude_command}`: {exc}") from exc
    if completed.returncode != 0:
        stderr = completed.stderr.strip()
        raise AgentBackendError(f"Claude Code exited with code {completed.returncode}: {stderr or completed.stdout.strip()}")
    parsed, metadata = parse_claude_stdout(completed.stdout)
    return parsed, metadata


def parse_claude_stdout(stdout: str) -> tuple[dict[str, Any], dict[str, Any]]:
    if not stdout.strip():
        raise AgentBackendError("Claude Code returned empty stdout.")
    try:
        outer = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise AgentBackendError(f"Claude Code stdout was not JSON: {exc}") from exc
    metadata: dict[str, Any] = {}
    if isinstance(outer, dict) and "markdown" in outer:
        payload = outer
    elif isinstance(outer, dict) and isinstance(outer.get("result"), str):
        metadata = {key: value for key, value in outer.items() if key != "result"}
        payload = _parse_json_object_from_text(outer["result"])
    else:
        raise AgentBackendError("Claude Code JSON did not contain a SlideNote result object.")
    _validate_agent_result_payload(payload)
    return payload, metadata


def _parse_json_object_from_text(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise AgentBackendError("Claude Code result did not contain a JSON object.")
        value = json.loads(cleaned[start : end + 1])
    if not isinstance(value, dict):
        raise AgentBackendError("Claude Code result JSON must be an object.")
    return value


def _validate_agent_result_payload(payload: dict[str, Any]) -> None:
    required = {
        "markdown": str,
        "used_asset_paths": list,
        "covered_source_ids": list,
        "warnings": list,
    }
    missing = [name for name in required if name not in payload]
    if missing:
        raise AgentBackendError(f"Claude Code result is missing required field(s): {', '.join(missing)}")
    for name, expected_type in required.items():
        if not isinstance(payload[name], expected_type):
            raise AgentBackendError(f"Claude Code result field `{name}` must be {expected_type.__name__}.")
    if not all(isinstance(value, str) for value in payload["used_asset_paths"]):
        raise AgentBackendError("Claude Code result field `used_asset_paths` must contain only strings.")
    if not all(isinstance(value, str) for value in payload["covered_source_ids"]):
        raise AgentBackendError("Claude Code result field `covered_source_ids` must contain only strings.")


def _build_claude_prompt(manifest: dict[str, Any], section: dict[str, Any], section_file: Path) -> str:
    style = (section_file.parent.parent / "style.md").read_text(encoding="utf-8")
    skill = (section_file.parent.parent / "skill.md").read_text(encoding="utf-8")
    section_markdown = section_file.read_text(encoding="utf-8")
    return f"""Use the SlideNote skill instructions and write one section of study notes.

You must return only one JSON object with these fields:
- "markdown": string
- "used_asset_paths": string[]
- "covered_source_ids": string[]
- "warnings": string[]

Do not write files. Do not invent image paths. Use only assets listed in the section pack.
When a useful figure/image is listed, insert its `markdown_reference` near the relevant concept and add visible explanatory text in the same paragraph, list item, or caption block.

SOURCE TITLE: {manifest.get("source", {}).get("title")}
SECTION ID: {section.get("section_id")}
SECTION TITLE: {section.get("title")}

=== skill.md ===
{skill}

=== style.md ===
{style}

=== section pack ===
{section_markdown}
"""


def _build_claude_repair_prompt(
    manifest: dict[str, Any],
    section: dict[str, Any],
    section_file: Path,
    current_markdown: str,
    missing_issues: list[dict[str, Any]],
) -> str:
    style = (section_file.parent.parent / "style.md").read_text(encoding="utf-8")
    skill = (section_file.parent.parent / "skill.md").read_text(encoding="utf-8")
    section_markdown = section_file.read_text(encoding="utf-8")
    missing_json = json.dumps(missing_issues, ensure_ascii=False, indent=2)
    return f"""Repair one SlideNote section after deterministic coverage checks.

You must return only one JSON object with these fields:
- "markdown": string
- "used_asset_paths": string[]
- "covered_source_ids": string[]
- "warnings": string[]

Return the full revised section markdown, not a diff. Do not write files.
Fix the listed missing coverage issues while preserving the good parts of the current section.
For required_visible_missing items, include a visible explanation in the same paragraph/list item/table/image block as the source marker.
For trace_missing items, cover the source in prose or add an appropriate source marker.
For figure_missing items, insert the listed `asset_path` with Markdown image syntax near the relevant concept.
For figure_unexplained items, keep or insert the image and add visible explanatory text in the same paragraph, list item, or caption block.
For figure_needs_review items, explain cautiously using the visual summary, OCR, anchors, and source ids; add a warning if ambiguity remains.
Use only assets listed in the section pack.

SOURCE TITLE: {manifest.get("source", {}).get("title")}
SECTION ID: {section.get("section_id")}
SECTION TITLE: {section.get("title")}

=== missing coverage issues ===
{missing_json}

=== current section markdown ===
{current_markdown}

=== skill.md ===
{skill}

=== style.md ===
{style}

=== section pack ===
{section_markdown}
"""


def _validate_asset_references(markdown: str, used_asset_paths: list[str], known_assets: set[str]) -> None:
    referenced = set(used_asset_paths)
    referenced.update(target.strip().strip("<>").replace("\\", "/") for target in re.findall(r"!\[[^\]]*]\(([^)]+)\)", markdown))
    unknown = sorted(path for path in referenced if path and path.startswith("assets/") and path not in known_assets)
    if unknown:
        raise AgentBackendError(f"Claude Code referenced unknown asset path(s): {', '.join(unknown)}")


def _ensure_covered_markers(markdown: str, covered_source_ids: list[str], source_id_to_slide: dict[str, int]) -> str:
    existing = set(re.findall(r"\bs\d+_(?:t|tbl|img|fig)\d+\b", markdown))
    missing = [source_id for source_id in covered_source_ids if source_id not in existing and source_id in source_id_to_slide]
    if not missing:
        return markdown.rstrip() + "\n"
    by_slide: dict[int, list[str]] = {}
    for source_id in missing:
        by_slide.setdefault(source_id_to_slide[source_id], []).append(source_id)
    markers = [f"<!-- slidenote-source: p{slide_id}:{','.join(sorted(ids))} -->" for slide_id, ids in sorted(by_slide.items())]
    return markdown.rstrip() + "\n\n" + "\n".join(markers) + "\n"


def _compose_agent_notes(manifest: dict[str, Any], section_results: list[dict[str, Any]], output_root: Path) -> str:
    lines = [f"# {manifest.get('source', {}).get('title') or 'SlideNote Agent Notes'}", ""]
    for section in section_results:
        section_file = output_root / str(section["file"])
        body = section_file.read_text(encoding="utf-8").strip()
        if body:
            lines.append(body)
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _copy_pack_assets_to_output(pack_dir: Path, output_root: Path) -> None:
    source_assets = pack_dir / "assets"
    if not source_assets.exists():
        return
    destination = output_root / "assets"
    if source_assets.resolve() == destination.resolve():
        return
    shutil.copytree(source_assets, destination, dirs_exist_ok=True)


def _write_agent_diagnostics(output_root: Path, data: dict[str, Any]) -> None:
    ensure_clean_dir(output_root)
    data = {"schema_version": 1, "generated_at": utc_now_iso(), **data}
    write_json(output_root / "agent_diagnostics.json", data)


def _agent_style_markdown() -> str:
    return """# SlideNote Study Note Style

Write detailed lecture notes in Chinese unless the source material is clearly English-only.
Keep important English technical terms in parentheses or inline when useful.
Do not merely translate slide bullets. Explain the concepts as study material.
Every important text/table/image element should be covered either in visible prose or with a hidden SlideNote source marker.
When a useful figure is listed, place its Markdown image near the concept it explains and add a short explanation before or after it.
"""


def _agent_skill_markdown() -> str:
    return """# SlideNote Claude Code Skill

You are writing one section of a larger SlideNote handout.
Use the provided section pack as the only source of truth.
Return only JSON. The `markdown` value may contain Markdown headings, paragraphs, tables, images, and hidden HTML comments.
Use this source marker format exactly: `<!-- slidenote-source: p{slide_id}:{source_ids} -->`.
Use only image paths that start with `assets/` and are explicitly listed in the section pack.
Populate `used_asset_paths` with every image path you used.
Populate `covered_source_ids` with every source id you intentionally covered.
Use `warnings` for possible omissions, unclear OCR, or figures that need human review.
"""


def _expected_source_ids_by_slide(deck: Deck) -> dict[int, list[str]]:
    result: dict[int, list[str]] = {}
    for element in iter_expected_source_elements(deck):
        slide_id = int(element.get("slide_id") or 0)
        element_id = str(element.get("element_id") or "")
        if slide_id and element_id:
            result.setdefault(slide_id, []).append(element_id)
    return {slide_id: sorted(ids) for slide_id, ids in result.items()}


def _source_ids_for_pages(pages: list[SlidePage], expected_by_slide: dict[int, list[str]]) -> list[str]:
    ids: list[str] = []
    seen: set[str] = set()
    for page in pages:
        for source_id in expected_by_slide.get(page.slide_id, []):
            if source_id not in seen:
                ids.append(source_id)
                seen.add(source_id)
    return ids


def _source_id_to_slide(deck: Deck) -> dict[str, int]:
    result: dict[str, int] = {}
    for element in iter_expected_source_elements(deck):
        element_id = str(element.get("element_id") or "")
        slide_id = int(element.get("slide_id") or 0)
        if element_id and slide_id:
            result[element_id] = slide_id
    return result


def _resolve_asset_source(output_root: Path, raw_path: str) -> Path:
    path = Path(raw_path)
    if path.is_absolute():
        return path
    return (output_root / path).resolve()


def _asset_destination(pack_dir: Path, raw_path: str, kind: str, seen_destinations: set[Path]) -> Path:
    path = Path(raw_path)
    if path.is_absolute():
        relative = Path("external") / path.name
    else:
        relative = path
    destination = pack_dir / "assets" / relative
    if destination in seen_destinations:
        stem = destination.stem or "asset"
        suffix = destination.suffix or ".png"
        parent = destination.parent
        counter = 2
        while destination in seen_destinations:
            destination = parent / f"{stem}-{counter}{suffix}"
            counter += 1
    if len(destination.parts) <= len((pack_dir / "assets").parts):
        destination = pack_dir / "assets" / kind / (path.name or "asset")
    return destination


def _deck_title(deck: Deck) -> str:
    for page in deck.pages[:3]:
        if page.title:
            return page.title
    return Path(deck.source_path).stem


def _deck_from_manifest(manifest: dict[str, Any]) -> Deck:
    deck_data = manifest.get("deck")
    if not isinstance(deck_data, dict):
        raise AgentBackendError("Agent pack manifest does not contain deck data.")
    return Deck(
        source_path=str(deck_data.get("source_path") or ""),
        source_type=str(deck_data.get("source_type") or ""),
        warnings=list(deck_data.get("warnings") or []),
        pages=[_page_from_dict(page) for page in deck_data.get("pages") or []],
    )


def _page_from_dict(data: dict[str, Any]) -> SlidePage:
    kwargs = _filtered_kwargs(SlidePage, data)
    kwargs["text_blocks"] = [_dataclass_from_dict(TextBlock, item) for item in data.get("text_blocks") or []]
    kwargs["tables"] = [_dataclass_from_dict(TableBlock, item) for item in data.get("tables") or []]
    kwargs["images"] = [_dataclass_from_dict(ImageAsset, item) for item in data.get("images") or []]
    return SlidePage(**kwargs)


def _dataclass_from_dict(cls: type[Any], data: dict[str, Any]) -> Any:
    return cls(**_filtered_kwargs(cls, data))


def _filtered_kwargs(cls: type[Any], data: dict[str, Any]) -> dict[str, Any]:
    allowed = {field.name for field in fields(cls)}
    return {key: value for key, value in data.items() if key in allowed}
