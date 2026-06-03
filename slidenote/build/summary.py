from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from slidenote.build.config import _resolve_api_concurrency
from slidenote.build.progress import _stage_metrics
from slidenote.content_guard import content_guard_warnings
from slidenote.exporting import export_warnings, parse_export_formats
from slidenote.pipeline import ArtifactRegistry
from slidenote.progress import ProgressReporter


def _build_run_summary(
    args: argparse.Namespace,
    input_path: Path,
    output_root: Path,
    deck,
    modality_report: dict[str, Any],
    table_understanding_report: dict[str, Any],
    semantic_layout_report: dict[str, Any],
    image_importance_report: dict[str, Any] | None,
    section_report: dict[str, Any],
    deck_brief_report: dict[str, Any] | None,
    deck_understanding_report: dict[str, Any] | None,
    page_understanding_report: dict[str, Any] | None,
    composite_figure_report: dict[str, Any] | None,
    figure_report: dict[str, Any] | None,
    figure_grounding_report: dict[str, Any] | None,
    ocr_report: dict[str, Any] | None,
    vision_report: dict[str, Any] | None,
    content_guard_report: dict[str, Any] | None,
    llm_usage: dict[str, Any] | None,
    coverage_report: dict[str, Any],
    quality_report: dict[str, Any] | None,
    source_map: dict[str, Any],
    cache_dirs: dict[str, Path | None],
    refresh_slide_ids: set[int],
    progress: ProgressReporter,
    note_asset_warnings: list[str],
    export_report: dict[str, Any] | None = None,
    study_pack_report: dict[str, Any] | None = None,
    artifact_registry: ArtifactRegistry | None = None,
    api_concurrency: dict[str, int] | None = None,
) -> dict[str, Any]:
    pages = deck.pages
    images_count = sum(len(page.images) for page in pages)
    return {
        "schema_version": 1,
        "source_path": str(input_path),
        "source_type": deck.source_type,
        "output_root": str(output_root),
        "run": {
            "preset": getattr(args, "preset", "auto"),
            "speed_mode": args.speed_mode,
            "concurrency": max(1, args.concurrency),
            "api_concurrency": api_concurrency or _resolve_api_concurrency(args),
            "refresh_slide_ids": sorted(refresh_slide_ids),
            "cache_dirs": {name: str(path) if path else None for name, path in cache_dirs.items()},
            "parser": getattr(args, "parser", "auto"),
            "asset_mode": args.asset_mode,
            "source_display": args.source_display,
            "note_context": args.note_context,
            "note_style": args.note_style,
            "note_profile": args.note_profile,
            "note_language": args.note_language,
            "term_policy": args.term_policy,
            "note_strategy": args.note_strategy,
            "note_depth": args.note_depth,
            "teaching_enrichment": args.teaching_enrichment,
            "deck_brief": args.deck_brief,
            "content_guard": args.content_guard,
            "review_mode": args.review_mode,
            "exam_mode": args.exam_mode,
            "exam_question_count": args.exam_question_count,
            "export": parse_export_formats(args.export),
            "export_toc": args.export_toc,
            "weave_dedup": args.weave_dedup,
            "page_neighborhood": args.page_neighborhood,
            "section_detection": args.section_detection,
            "semantic_layout": args.semantic_layout,
            "image_ranking": args.image_ranking,
            "composite_figures": args.composite_figures,
            "figure_crop": args.figure_crop,
            "figure_grounding": args.figure_grounding,
            "figure_placement": args.figure_placement,
            "figure_audit": args.figure_audit,
            "screenshot_policy": args.screenshot_policy,
        },
        "counts": {
            "pages": len(pages),
            "text_blocks": sum(len(page.text_blocks) for page in pages),
            "tables": sum(len(page.tables) for page in pages),
            "images": images_count,
            "figure_crops": sum(1 for page in pages for image in page.images if image.role == "figure_crop"),
            "composite_figures": sum(1 for page in pages for image in page.images if image.role == "composite_figure"),
            "page_screenshots": sum(1 for page in pages if page.page_screenshot),
        },
        "composite_figures": composite_figure_report.get("summary") if composite_figure_report else None,
        "figure_crop": figure_report.get("summary") if figure_report else None,
        "figure_grounding": figure_grounding_report.get("summary") if figure_grounding_report else None,
        "page_modalities": modality_report.get("summary") if modality_report else None,
        "table_understanding": table_understanding_report.get("summary") if table_understanding_report else None,
        "semantic_layout": semantic_layout_report.get("summary") if semantic_layout_report else None,
        "image_importance": image_importance_report.get("summary") if image_importance_report else None,
        "sections": section_report.get("summary") if section_report else None,
        "deck_brief": deck_brief_report.get("summary") if deck_brief_report else None,
        "deck_understanding": deck_understanding_report.get("summary") if deck_understanding_report else None,
        "page_understanding": page_understanding_report.get("summary") if page_understanding_report else None,
        "ocr": ocr_report.get("summary") if ocr_report else None,
        "vision": vision_report.get("summary") if vision_report else None,
        "content_guard": content_guard_report.get("summary") if content_guard_report else None,
        "study_pack": study_pack_report.get("summary") if study_pack_report else None,
        "llm": llm_usage.get("summary") if llm_usage else None,
        "quality": quality_report.get("summary") if quality_report else None,
        "coverage": {
            "total": coverage_report.get("total"),
            "covered": coverage_report.get("covered"),
            "missing": coverage_report.get("missing"),
            "coverage_ratio": coverage_report.get("coverage_ratio"),
            "page_coverage": coverage_report.get("page_coverage"),
            "trace_coverage": coverage_report.get("trace_coverage"),
            "visible_coverage": coverage_report.get("visible_coverage"),
            "required_visible_coverage": coverage_report.get("required_visible_coverage"),
            "marker_only": coverage_report.get("marker_only"),
            "structural_marker_only": coverage_report.get("structural_marker_only"),
        },
        "source_map": {
            "note_blocks": len(source_map.get("note_blocks", [])),
            "default_display_mode": source_map.get("default_display_mode"),
        },
        "stage_timings": _stage_metrics(progress),
        "warnings": {
            "note_assets": note_asset_warnings,
            "content_guard": content_guard_warnings(content_guard_report),
            "study_pack": study_pack_report.get("warnings") if study_pack_report else [],
            "export": export_warnings(export_report),
        },
        "artifacts": {
            "content": "content.json",
            "element_ir": "element_ir.json",
            "notes": "notes.md",
            "note_assets": "notes.assets" if args.asset_mode == "bundle" else None,
            "coverage": "coverage.md",
            "quality_report": "quality_report.json" if quality_report else None,
            "source_map": "source_map.json",
            "progress": _display_path(progress.path, output_root),
            "run_summary": "run_summary.json",
            "page_modalities": "page_modalities.json",
            "table_understanding": "table_understanding.json",
            "semantic_layout": "semantic_layout.json",
            "image_importance": "image_importance.json" if image_importance_report else None,
            "composite_figures": "composite_figures.json" if composite_figure_report else None,
            "sections": "sections.json",
            "deck_brief": "deck_brief.json" if deck_brief_report else None,
            "deck_brief_markdown": "deck_brief.md" if deck_brief_report else None,
            "deck_understanding": "deck_understanding.json" if deck_understanding_report else None,
            "page_understanding": "page_understanding.json" if page_understanding_report else None,
            "content_guard": "content_guard.json" if content_guard_report else None,
            "study_pack": "study_pack.json" if study_pack_report else None,
            "review_markdown": "review.md" if study_pack_report and study_pack_report.get("review") else None,
            "exam_markdown": "exam.md" if study_pack_report and study_pack_report.get("exam") else None,
            "exam_json": "exam.json" if study_pack_report and study_pack_report.get("exam") else None,
            "exam_html": "exam.html" if study_pack_report and study_pack_report.get("exam") else None,
            "section_study_pack": "section_study_pack.json" if study_pack_report and study_pack_report.get("section_study_pack") else None,
            "exam_review_pack": "exam_review_pack.json" if study_pack_report and study_pack_report.get("exam_review_pack") else None,
            "final_exam_markdown": "final_exam.md" if study_pack_report and study_pack_report.get("final_exam") else None,
            "final_exam_answers": "final_exam.answers.md" if study_pack_report and study_pack_report.get("final_exam") else None,
            "wrong_answer_review_prompt": "wrong_answer_review_prompt.md" if study_pack_report and study_pack_report.get("wrong_answer_review") else None,
            "export_report": "export_report.json" if export_report else None,
            "notes_toc": _export_artifact_path(export_report, "markdown-toc"),
            "notes_docx": _export_artifact_path(export_report, "docx"),
            "notes_pdf": _export_artifact_path(export_report, "pdf"),
            "notes_latex": _export_artifact_path(export_report, "latex"),
            "figures": "figures.json" if figure_report else None,
            "figure_usage": "figure_usage.json" if figure_report else None,
            "figure_grounding": "figure_grounding.json" if figure_grounding_report else None,
            "llm_usage": "llm_usage.json" if llm_usage else None,
            "page_notes": "page_notes.json" if getattr(args, "note_strategy", "direct") == "lecture-weave" and llm_usage else None,
            "page_notes_markdown": "page_notes.md" if getattr(args, "note_strategy", "direct") == "lecture-weave" and llm_usage else None,
            "weave_report": "weave_report.json" if getattr(args, "note_strategy", "direct") == "lecture-weave" and llm_usage else None,
            "teaching_enrichment": "teaching_enrichment.json" if llm_usage and (llm_usage.get("summary") or {}).get("teaching_enrichment_contexts") else None,
            "ocr_usage": "ocr_usage.json" if ocr_report else None,
            "vision_usage": "vision_usage.json" if vision_report else None,
            "registered": artifact_registry.as_summary() if artifact_registry else {},
        },
        "progress": progress.snapshot(),
    }


def _export_artifact_path(export_report: dict[str, Any] | None, fmt: str) -> str | None:
    if not export_report:
        return None
    results = export_report.get("results")
    if not isinstance(results, list):
        return None
    for result in results:
        if not isinstance(result, dict):
            continue
        if result.get("format") == fmt and result.get("status") == "ok":
            path = result.get("path")
            return str(path) if path else None
    return None


def _display_path(path: Path, output_root: Path) -> str:
    try:
        return path.resolve().relative_to(output_root.resolve()).as_posix()
    except ValueError:
        return str(path)
