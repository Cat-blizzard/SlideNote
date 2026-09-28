from __future__ import annotations

from typing import Any

from slidenote.build.progress import _stage_metrics
from slidenote.content_guard import content_guard_warnings
from slidenote.exporting import export_warnings
from slidenote.llm import resolve_provider_runtime

# Artifact keys always present in run_summary["artifacts"] (None when the
# stage did not run); actual paths come from the ArtifactRegistry.
RUN_SUMMARY_ARTIFACT_KEYS = (
    "content",
    "element_ir",
    "notes",
    "note_assets",
    "coverage",
    "quality_report",
    "source_map",
    "progress",
    "run_summary",
    "page_modalities",
    "table_understanding",
    "semantic_layout",
    "image_importance",
    "composite_figures",
    "sections",
    "deck_brief",
    "deck_brief_markdown",
    "deck_understanding",
    "page_understanding",
    "content_guard",
    "export_report",
    "notes_zip",
    "notes_toc",
    "notes_docx",
    "notes_pdf",
    "notes_latex",
    "figures",
    "figure_usage",
    "figure_grounding",
    "llm_usage",
    "page_notes",
    "page_notes_markdown",
    "weave_report",
    "teaching_enrichment",
    "ocr_usage",
    "vision_usage",
)


def _summary_of(report: dict[str, Any] | None) -> Any:
    return report.get("summary") if report else None


def _build_run_summary(state: "BuildState") -> dict[str, Any]:
    args = state.args
    deck = state.deck
    notes_result = state.notes_result
    coverage_report = state.coverage_report or {}
    source_map = state.source_map or {}
    llm_usage = notes_result.llm_usage if notes_result else None
    try:
        text_runtime = resolve_provider_runtime(args.provider, model=args.model, base_url=args.base_url)
    except (RuntimeError, ValueError):
        # Local builds can run without a configured text model. Preserve any
        # explicit values while leaving the unresolved fields empty.
        text_runtime = {}
    pages = deck.pages
    registered = state.artifacts.as_summary()
    return {
        "schema_version": 1,
        "source_path": str(state.input_path),
        "source_type": deck.source_type,
        "output_root": str(state.output_root),
        "run": {
            "preset": getattr(args, "preset", "lecture"),
            "provider": getattr(args, "provider", "deepseek"),
            "model": text_runtime.get("model") or args.model,
            "base_url": text_runtime.get("base_url") or args.base_url,
            "vision": getattr(args, "vision", "auto"),
            "speed_mode": args.speed_mode,
            "concurrency": state.concurrency,
            "api_concurrency": state.api_concurrency,
            "refresh_slide_ids": sorted(state.refresh_slide_ids),
            "cache_dirs": {name: str(path) if path else None for name, path in state.cache_dirs.items()},
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
            "export": list(state.export_formats),
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
            "images": sum(len(page.images) for page in pages),
            "figure_crops": sum(1 for page in pages for image in page.images if image.role == "figure_crop"),
            "composite_figures": sum(1 for page in pages for image in page.images if image.role == "composite_figure"),
            "page_screenshots": sum(1 for page in pages if page.page_screenshot),
        },
        "composite_figures": _summary_of(state.composite_figure_report),
        "figure_crop": _summary_of(state.figure_report),
        "figure_grounding": _summary_of(state.figure_grounding_report),
        "page_modalities": _summary_of(state.modality_report),
        "table_understanding": _summary_of(state.table_understanding_report),
        "semantic_layout": _summary_of(state.semantic_layout_report),
        "image_importance": _summary_of(state.image_importance_report),
        "sections": _summary_of(state.section_report),
        "deck_brief": _summary_of(state.deck_brief_report),
        "deck_understanding": _summary_of(state.deck_understanding_report),
        "page_understanding": _summary_of(state.page_understanding_report),
        "ocr": _summary_of(state.ocr_report),
        "vision": _summary_of(state.vision_report),
        "content_guard": _summary_of(state.content_guard_report),
        "llm": _summary_of(llm_usage),
        "quality": _summary_of(state.quality_report),
        "coverage": {
            key: coverage_report.get(key)
            for key in (
                "total",
                "covered",
                "missing",
                "coverage_ratio",
                "page_coverage",
                "trace_coverage",
                "visible_coverage",
                "required_visible_coverage",
                "marker_only",
                "structural_marker_only",
            )
        },
        "source_map": {
            "note_blocks": len(source_map.get("note_blocks", [])),
            "default_display_mode": source_map.get("default_display_mode"),
        },
        "stage_timings": _stage_metrics(state.progress),
        "warnings": {
            "config": list(getattr(args, "_config_warnings", None) or []),
            "note_assets": list((notes_result.asset_warnings if notes_result else None) or []),
            "notes": list((notes_result.generation_warnings if notes_result else None) or []),
            "content_guard": content_guard_warnings(state.content_guard_report),
            "export": export_warnings(state.export_report),
        },
        "artifacts": {
            **{key: None for key in RUN_SUMMARY_ARTIFACT_KEYS},
            **registered,
            "registered": registered,
        },
        "progress": state.progress.snapshot(),
    }
