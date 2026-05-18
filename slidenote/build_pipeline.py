from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from slidenote.content_guard import build_content_guard, content_guard_warnings, record_required_coverage
from slidenote.coverage import analyze_coverage, render_coverage_markdown
from slidenote.deck_brief import build_deck_brief, render_deck_brief_markdown
from slidenote.exporting import build_export_artifacts, export_blocking_failures, export_warnings, parse_export_formats
from slidenote.extractors import extract_deck
from slidenote.image_ranking import rank_deck_images
from slidenote.ir import build_deck_ir
from slidenote.llm import get_provider_spec
from slidenote.models import Deck
from slidenote.modality import enrich_deck_with_modalities
from slidenote.notes import NoteGenerationResult, estimate_note_generation_steps, generate_notes_result
from slidenote.ocr import enrich_deck_with_ocr
from slidenote.pipeline import ArtifactRegistry, BuildContext, FunctionStage, StageResult, run_stage
from slidenote.progress import ProgressReporter
from slidenote.sections import build_section_plan
from slidenote.source_map import build_source_map
from slidenote.table_understanding import enrich_deck_with_table_understanding
from slidenote.utils import ensure_clean_dir
from slidenote.visual.crop import enrich_deck_with_composite_figures, enrich_deck_with_figures
from slidenote.visual.grounding import enrich_deck_with_figure_grounding
from slidenote.visual.semantic_layout import enrich_deck_with_semantic_layout
from slidenote.visual.vision import enrich_deck_with_vision


class UserFacingConfigError(RuntimeError):
    """Configuration error that should be shown without a Python traceback."""


@dataclass(slots=True)
class BuildState:
    args: argparse.Namespace
    input_path: Path
    output_root: Path
    progress: ProgressReporter
    refresh_slide_ids: set[int]
    concurrency: int
    api_concurrency: dict[str, int]
    cache_dirs: dict[str, Path | None]
    artifacts: ArtifactRegistry
    build_context: BuildContext
    export_formats: list[str]
    deck: Deck | None = None
    modality_report: dict[str, Any] | None = None
    table_understanding_report: dict[str, Any] | None = None
    semantic_layout_report: dict[str, Any] | None = None
    composite_figure_report: dict[str, Any] | None = None
    figure_report: dict[str, Any] | None = None
    image_importance_report: dict[str, Any] | None = None
    ocr_report: dict[str, Any] | None = None
    vision_report: dict[str, Any] | None = None
    figure_grounding_report: dict[str, Any] | None = None
    section_report: dict[str, Any] | None = None
    deck_brief_report: dict[str, Any] | None = None
    content_guard_report: dict[str, Any] | None = None
    notes_result: NoteGenerationResult | None = None
    notes_markdown: str = ""
    coverage_report: dict[str, Any] | None = None
    source_map: dict[str, Any] | None = None
    export_report: dict[str, Any] | None = None
    export_exit_code: int = 0


def run_build(args: argparse.Namespace) -> int:
    _apply_speed_mode_defaults(args)
    try:
        export_formats = parse_export_formats(args.export)
    except ValueError as exc:
        raise UserFacingConfigError(str(exc)) from exc

    state = _create_build_state(args, export_formats)
    try:
        _stage_parse(state)
        _stage_modality(state)
        _stage_table_understanding(state)
        _stage_semantic_layout(state)
        _stage_composite_figures(state)
        _stage_figure_crop(state)
        _stage_image_importance(state)
        _stage_ocr(state)
        _stage_vision(state)
        _stage_figure_grounding(state)
        _stage_sections(state)
        _stage_deck_brief(state)
        _stage_content_guard(state)
        _stage_export_content(state)
        _stage_notes(state)
        _stage_coverage(state)
        _stage_export(state)
        _stage_run_summary(state)
    except Exception as exc:
        friendly_message = _friendly_build_error(exc, args)
        if friendly_message:
            state.progress.fail(friendly_message)
            raise UserFacingConfigError(friendly_message) from exc
        state.progress.fail(str(exc))
        raise

    _print_build_outputs(state)
    return state.export_exit_code


def _create_build_state(args: argparse.Namespace, export_formats: list[str]) -> BuildState:
    input_path = args.input.resolve()
    output_root = args.out.resolve()
    if not input_path.exists():
        raise FileNotFoundError(input_path)

    ensure_clean_dir(output_root)
    progress = ProgressReporter((args.progress_json or (output_root / "progress.json")).resolve(), quiet=args.quiet)
    refresh_slide_ids = _parse_slide_ranges(args.refresh_pages)
    concurrency = max(1, args.concurrency)
    api_concurrency = _resolve_api_concurrency(args)
    cache_dirs = _resolve_cache_dirs(args, output_root)
    artifacts = ArtifactRegistry(output_root)
    artifacts.register("progress", progress.path)
    build_context = BuildContext(
        args=args,
        input_path=input_path,
        output_root=output_root,
        progress=progress,
        cache_dirs=cache_dirs,
        refresh_slide_ids=refresh_slide_ids,
        concurrency=concurrency,
        artifacts=artifacts,
    )
    return BuildState(
        args=args,
        input_path=input_path,
        output_root=output_root,
        progress=progress,
        refresh_slide_ids=refresh_slide_ids,
        concurrency=concurrency,
        api_concurrency=api_concurrency,
        cache_dirs=cache_dirs,
        artifacts=artifacts,
        build_context=build_context,
        export_formats=export_formats,
    )


def _stage_parse(state: BuildState) -> None:
    state.progress.start_stage("parse", message=f"Parsing {state.input_path.name}")
    state.deck = extract_deck(state.input_path, state.output_root)
    state.progress.finish_stage(f"Parsed {len(state.deck.pages)} pages")


def _stage_modality(state: BuildState) -> None:
    deck = _require_deck(state)
    state.modality_report = _run_json_stage(
        deck,
        state.build_context,
        name="modality",
        artifact_name="page_modalities",
        artifact_path="page_modalities.json",
        message="Classifying page modalities",
        complete_message="Page modality classification complete",
        runner=lambda stage_deck: enrich_deck_with_modalities(stage_deck),
    )


def _stage_table_understanding(state: BuildState) -> None:
    deck = _require_deck(state)
    state.table_understanding_report = _run_json_stage(
        deck,
        state.build_context,
        name="table_understanding",
        dependencies=["modality"],
        artifact_name="table_understanding",
        artifact_path="table_understanding.json",
        message="Summarizing table conclusions",
        complete_message="Table understanding complete",
        runner=lambda stage_deck: enrich_deck_with_table_understanding(stage_deck),
    )


def _stage_semantic_layout(state: BuildState) -> None:
    deck = _require_deck(state)
    state.semantic_layout_report = _run_json_stage(
        deck,
        state.build_context,
        name="semantic_layout",
        dependencies=["table_understanding"],
        artifact_name="semantic_layout",
        artifact_path="semantic_layout.json",
        message="Building semantic page blocks",
        complete_message="Semantic layout complete",
        runner=lambda stage_deck: enrich_deck_with_semantic_layout(stage_deck),
    )


def _stage_composite_figures(state: BuildState) -> None:
    args = state.args
    if args.composite_figures == "off":
        return
    deck = _require_deck(state)
    state.progress.start_stage("composite_figures", message="Detecting composite figures")
    state.composite_figure_report = enrich_deck_with_composite_figures(
        deck,
        output_root=state.output_root,
        mode=args.composite_figures,
    )
    state.artifacts.write_json("composite_figures", "composite_figures.json", state.composite_figure_report)
    state.progress.finish_stage("Composite figure detection complete")


def _stage_figure_crop(state: BuildState) -> None:
    args = state.args
    should_run_figure_crop = args.figure_crop == "vision" or (args.figure_crop == "auto" and args.vision != "off")
    if not should_run_figure_crop:
        return
    deck = _require_deck(state)
    state.progress.start_stage("figure_crop", message="Cropping local figures")
    state.figure_report = enrich_deck_with_figures(
        deck,
        output_root=state.output_root,
        mode=args.figure_crop,
        provider=args.vision_provider,
        model=args.vision_model,
        api_key=args.vision_api_key,
        base_url=args.vision_base_url,
        cache_mode=args.figure_cache,
        cache_dir=state.cache_dirs["figure"],
        max_targets=args.figure_max_targets,
        max_crops_per_page=args.figure_max_crops_per_page,
        min_confidence=args.figure_min_confidence,
        min_area=args.figure_min_area,
        max_output_tokens=min(args.vision_max_output_tokens or 1000, 1200),
        temperature=args.vision_temperature,
        detail=args.vision_detail,
        max_edge=args.vision_max_edge,
        concurrency=state.api_concurrency["figure"],
        refresh_slide_ids=state.refresh_slide_ids,
        progress_callback=_target_progress(state.progress, "figure"),
    )
    state.progress.finish_stage("Figure crop complete")


def _stage_image_importance(state: BuildState) -> None:
    args = state.args
    if args.image_ranking == "off":
        return
    deck = _require_deck(state)
    state.progress.start_stage("image_importance", message="Ranking image importance")
    state.image_importance_report = rank_deck_images(deck, state.output_root, mode=args.image_ranking, stage="pre_vision")
    state.progress.finish_stage("Image importance ranking complete")


def _stage_ocr(state: BuildState) -> None:
    args = state.args
    if args.ocr == "off":
        return
    deck = _require_deck(state)
    state.progress.start_stage("ocr", message="Running OCR")
    state.ocr_report = enrich_deck_with_ocr(
        deck,
        output_root=state.output_root,
        mode=args.ocr,
        provider=args.ocr_provider,
        api_key=args.ocr_api_key,
        secret_key=args.ocr_secret_key,
        endpoint=args.ocr_endpoint,
        language=args.ocr_language,
        cache_mode=args.ocr_cache,
        cache_dir=state.cache_dirs["ocr"],
        max_targets=args.ocr_max_targets,
        min_text_chars=args.ocr_min_text_chars,
        min_area=args.ocr_min_area,
        max_edge=args.ocr_max_edge,
        concurrency=state.api_concurrency["ocr"],
        refresh_slide_ids=state.refresh_slide_ids,
        progress_callback=_target_progress(state.progress, "ocr"),
    )
    state.progress.finish_stage("OCR complete")


def _stage_vision(state: BuildState) -> None:
    args = state.args
    should_run_vision = args.vision != "off" or args.figure_grounding == "vision"
    if not should_run_vision:
        return
    deck = _require_deck(state)
    vision_mode = args.vision if args.vision != "off" else "auto"
    state.progress.start_stage("vision", message="Running vision analysis")
    state.vision_report = enrich_deck_with_vision(
        deck,
        output_root=state.output_root,
        mode=vision_mode,
        provider=args.vision_provider,
        model=args.vision_model,
        api_key=args.vision_api_key,
        base_url=args.vision_base_url,
        cache_mode=args.vision_cache,
        cache_dir=state.cache_dirs["vision"],
        max_output_tokens=args.vision_max_output_tokens,
        temperature=args.vision_temperature,
        detail=args.vision_detail,
        max_targets=args.vision_max_targets,
        min_area=args.vision_min_area,
        max_edge=args.vision_max_edge,
        concurrency=state.api_concurrency["vision"],
        refresh_slide_ids=state.refresh_slide_ids,
        progress_callback=_target_progress(state.progress, "vision"),
    )
    state.progress.finish_stage("Vision analysis complete")
    if state.image_importance_report is not None:
        state.image_importance_report = rank_deck_images(deck, state.output_root, mode=args.image_ranking, stage="post_vision")


def _stage_figure_grounding(state: BuildState) -> None:
    args = state.args
    if args.figure_grounding == "off":
        return
    deck = _require_deck(state)
    state.progress.start_stage("figure_grounding", message="Grounding figures to page text")
    state.figure_grounding_report = enrich_deck_with_figure_grounding(
        deck,
        output_root=state.output_root,
        mode=args.figure_grounding,
        placement=args.figure_placement,
        audit=args.figure_audit,
    )
    state.artifacts.write_json("figure_grounding", "figure_grounding.json", state.figure_grounding_report)
    state.progress.finish_stage("Figure grounding complete")


def _stage_sections(state: BuildState) -> None:
    args = state.args
    deck = _require_deck(state)
    state.progress.start_stage("sections", message="Detecting note sections")
    state.section_report = build_section_plan(
        deck,
        output_root=state.output_root,
        mode=args.section_detection,
        use_llm=args.use_llm and (args.note_context == "section" or (args.note_context == "auto" and len(deck.pages) > 12)),
        provider=args.provider,
        model=args.model,
        api_key=args.api_key,
        base_url=args.base_url,
        cache_mode=args.section_cache,
        cache_dir=state.cache_dirs["sections"],
        max_output_tokens=min(args.max_output_tokens or 1800, 2500),
        temperature=0.0,
    )
    state.artifacts.write_json("sections", "sections.json", state.section_report)
    state.progress.finish_stage("Section detection complete")


def _stage_deck_brief(state: BuildState) -> None:
    args = state.args
    if not _should_build_deck_brief(args):
        return
    deck = _require_deck(state)
    state.progress.start_stage("deck_brief", message="Building deck brief")
    state.deck_brief_report = build_deck_brief(
        deck,
        output_root=state.output_root,
        section_plan=state.section_report,
        provider=args.provider,
        model=args.model,
        api_key=args.api_key,
        base_url=args.base_url,
        cache_mode=args.cache,
        cache_dir=state.cache_dirs["llm"],
        max_output_tokens=min(args.max_output_tokens or 3000, 5000),
        temperature=0.0,
    )
    state.artifacts.write_json("deck_brief", "deck_brief.json", state.deck_brief_report)
    state.artifacts.write_text("deck_brief_markdown", "deck_brief.md", render_deck_brief_markdown(state.deck_brief_report))
    state.progress.finish_stage("Deck brief complete")


def _stage_content_guard(state: BuildState) -> None:
    args = state.args
    if args.content_guard == "off":
        return
    deck = _require_deck(state)
    state.progress.start_stage("content_guard", message="Classifying required learning content")
    state.content_guard_report = build_content_guard(
        deck,
        output_root=state.output_root,
        mode=args.content_guard,
        use_llm=args.use_llm,
        provider=args.provider,
        model=args.model,
        api_key=args.api_key,
        base_url=args.base_url,
        cache_mode=args.cache,
        cache_dir=state.cache_dirs["llm"],
        max_output_tokens=min(args.max_output_tokens or 1800, 2500),
        temperature=0.0,
    )
    if state.content_guard_report is not None:
        state.artifacts.write_json("content_guard", "content_guard.json", state.content_guard_report)
    state.progress.finish_stage("Content guard complete")


def _stage_export_content(state: BuildState) -> None:
    deck = _require_deck(state)
    state.progress.start_stage("export_content", message="Writing structured content")
    state.artifacts.write_json("content", "content.json", deck.to_dict())
    state.artifacts.write_json("element_ir", "element_ir.json", build_deck_ir(deck))
    if state.image_importance_report is not None:
        state.artifacts.write_json("image_importance", "image_importance.json", state.image_importance_report)
    if state.figure_report is not None:
        state.artifacts.write_json("figures", "figures.json", _build_figures_export(deck, state.figure_report))
        state.artifacts.write_json("figure_usage", "figure_usage.json", state.figure_report)
    if state.ocr_report is not None:
        state.artifacts.write_json("ocr", "ocr.json", _build_ocr_export(deck, state.ocr_report))
        state.artifacts.write_json("ocr_usage", "ocr_usage.json", state.ocr_report)
    if state.vision_report is not None:
        state.artifacts.write_json("visuals", "visuals.json", _build_visuals_export(deck, state.vision_report))
        state.artifacts.write_json("vision_usage", "vision_usage.json", state.vision_report)
    state.progress.finish_stage("Structured content written")


def _stage_notes(state: BuildState) -> None:
    args = state.args
    deck = _require_deck(state)
    note_total = estimate_note_generation_steps(deck, args.note_context, args.note_strategy, section_plan=state.section_report) if args.use_llm else None
    state.progress.start_stage("notes", total=note_total, message="Generating notes")
    state.notes_result = generate_notes_result(
        deck,
        state.output_root,
        use_llm=args.use_llm,
        provider=args.provider,
        model=args.model,
        api_key=args.api_key,
        base_url=args.base_url,
        max_output_tokens=args.max_output_tokens,
        temperature=args.temperature,
        cache_mode=args.cache,
        cache_dir=state.cache_dirs["llm"],
        concurrency=state.api_concurrency["llm"],
        refresh_slide_ids=state.refresh_slide_ids,
        progress_callback=_llm_progress(state.progress),
        asset_mode=args.asset_mode,
        source_display=args.source_display,
        note_context=args.note_context,
        note_style=args.note_style,
        note_language=args.note_language,
        term_policy=args.term_policy,
        note_strategy=args.note_strategy,
        note_depth=args.note_depth,
        weave_dedup=args.weave_dedup,
        page_neighborhood=args.page_neighborhood,
        screenshot_policy=args.screenshot_policy,
        figure_placement=args.figure_placement,
        section_plan=state.section_report,
        deck_brief=state.deck_brief_report,
        content_guard=state.content_guard_report,
    )
    state.notes_markdown = state.notes_result.markdown
    state.artifacts.write_text("notes", "notes.md", state.notes_markdown)
    if args.asset_mode == "bundle":
        state.artifacts.register("note_assets", state.output_root / "notes.assets")
    if state.notes_result.llm_usage is not None:
        state.artifacts.write_json("llm_usage", "llm_usage.json", state.notes_result.llm_usage)
    if state.notes_result.page_notes is not None:
        state.artifacts.write_json("page_notes", "page_notes.json", state.notes_result.page_notes)
    if state.notes_result.page_notes_markdown is not None:
        state.artifacts.write_text("page_notes_markdown", "page_notes.md", state.notes_result.page_notes_markdown)
    if state.notes_result.weave_report is not None:
        state.artifacts.write_json("weave_report", "weave_report.json", state.notes_result.weave_report)
    if not args.use_llm:
        state.progress.advance(message="Local notes generated")
    state.progress.finish_stage("Notes generated")


def _stage_coverage(state: BuildState) -> None:
    deck = _require_deck(state)
    state.progress.start_stage("coverage", message="Checking coverage")
    state.coverage_report = analyze_coverage(deck, state.notes_markdown, content_guard=state.content_guard_report)
    if state.content_guard_report is not None:
        record_required_coverage(state.content_guard_report, state.coverage_report, stage="final")
        state.artifacts.write_json("content_guard", "content_guard.json", state.content_guard_report)
    state.artifacts.write_json("coverage_json", "coverage.json", state.coverage_report)
    state.artifacts.write_text("coverage", "coverage.md", render_coverage_markdown(state.coverage_report))
    state.source_map = build_source_map(deck, state.notes_markdown, state.output_root)
    state.artifacts.write_json("source_map", "source_map.json", state.source_map)
    state.progress.finish_stage("Coverage complete")


def _stage_export(state: BuildState) -> None:
    args = state.args
    if not state.export_formats:
        return
    state.progress.start_stage("export", message="Exporting requested note formats")
    state.export_report = build_export_artifacts(state.notes_markdown, state.output_root, state.export_formats, export_toc=args.export_toc)
    if state.export_report is not None:
        state.artifacts.write_json("export_report", "export_report.json", state.export_report)
        _register_export_artifacts(state.artifacts, state.export_report)
        if export_blocking_failures(state.export_report):
            state.export_exit_code = 1
    state.progress.finish_stage("Export stage complete")


def _stage_run_summary(state: BuildState) -> None:
    deck = _require_deck(state)
    notes_result = _require_notes_result(state)
    coverage_report = _require_report(state.coverage_report, "coverage")
    source_map = _require_report(state.source_map, "source_map")
    state.progress.complete("Build complete")
    state.artifacts.register("run_summary", state.output_root / "run_summary.json")
    run_summary = _build_run_summary(
        args=state.args,
        input_path=state.input_path,
        output_root=state.output_root,
        deck=deck,
        modality_report=state.modality_report or {},
        table_understanding_report=state.table_understanding_report or {},
        semantic_layout_report=state.semantic_layout_report or {},
        image_importance_report=state.image_importance_report,
        section_report=state.section_report or {},
        deck_brief_report=state.deck_brief_report,
        composite_figure_report=state.composite_figure_report,
        figure_report=state.figure_report,
        figure_grounding_report=state.figure_grounding_report,
        ocr_report=state.ocr_report,
        vision_report=state.vision_report,
        content_guard_report=state.content_guard_report,
        llm_usage=notes_result.llm_usage,
        coverage_report=coverage_report,
        source_map=source_map,
        cache_dirs=state.cache_dirs,
        refresh_slide_ids=state.refresh_slide_ids,
        progress=state.progress,
        note_asset_warnings=notes_result.asset_warnings or [],
        export_report=state.export_report,
        artifact_registry=state.artifacts,
        api_concurrency=state.api_concurrency,
    )
    state.artifacts.write_json("run_summary", "run_summary.json", run_summary)


def _print_build_outputs(state: BuildState) -> None:
    args = state.args
    if args.quiet:
        return
    notes_result = _require_notes_result(state)
    output_root = state.output_root
    print(f"SlideNote build complete: {output_root}")
    print(f"- content:  {output_root / 'content.json'}")
    print(f"- notes:    {output_root / 'notes.md'}")
    print(f"- coverage: {output_root / 'coverage.md'}")
    print(f"- sources:  {output_root / 'source_map.json'}")
    print(f"- element IR: {output_root / 'element_ir.json'}")
    print(f"- modalities: {output_root / 'page_modalities.json'}")
    print(f"- tables:   {output_root / 'table_understanding.json'}")
    print(f"- semantic: {output_root / 'semantic_layout.json'}")
    print(f"- sections: {output_root / 'sections.json'}")
    if state.deck_brief_report is not None:
        print(f"- deck brief: {output_root / 'deck_brief.json'}")
    if state.content_guard_report is not None:
        print(f"- content guard: {output_root / 'content_guard.json'}")
    print(f"- progress: {state.progress.path}")
    print(f"- summary:  {output_root / 'run_summary.json'}")
    if state.image_importance_report is not None:
        print(f"- image rank: {output_root / 'image_importance.json'}")
    if state.composite_figure_report is not None:
        print(f"- composite figs: {output_root / 'composite_figures.json'}")
    if notes_result.llm_usage is not None:
        print(f"- llm use:  {output_root / 'llm_usage.json'}")
    if notes_result.page_notes is not None:
        print(f"- page notes: {output_root / 'page_notes.md'}")
        print(f"- page json:  {output_root / 'page_notes.json'}")
    if notes_result.weave_report is not None:
        print(f"- weave:    {output_root / 'weave_report.json'}")
    if state.figure_report is not None:
        print(f"- figures:  {output_root / 'figures.json'}")
        print(f"- fig use:  {output_root / 'figure_usage.json'}")
    if state.figure_grounding_report is not None:
        print(f"- fig ground: {output_root / 'figure_grounding.json'}")
    if state.ocr_report is not None:
        print(f"- ocr:      {output_root / 'ocr.json'}")
        print(f"- ocr use:  {output_root / 'ocr_usage.json'}")
    if state.vision_report is not None:
        print(f"- visuals:  {output_root / 'visuals.json'}")
        print(f"- vision:   {output_root / 'vision_usage.json'}")
    if state.export_report is not None:
        print(f"- exports:  {output_root / 'export_report.json'}")
    for stage in _slowest_stages(state.progress, limit=3):
        print(f"- slow stage: {stage['name']} {stage['elapsed_seconds']:.1f}s")


def _require_deck(state: BuildState) -> Deck:
    if state.deck is None:
        raise RuntimeError("Build deck is not available before parse stage.")
    return state.deck


def _require_notes_result(state: BuildState) -> NoteGenerationResult:
    if state.notes_result is None:
        raise RuntimeError("Notes result is not available before notes stage.")
    return state.notes_result


def _require_report(report: dict[str, Any] | None, name: str) -> dict[str, Any]:
    if report is None:
        raise RuntimeError(f"Required build report `{name}` is not available.")
    return report


def _run_json_stage(
    deck,
    context: BuildContext,
    *,
    name: str,
    artifact_name: str,
    artifact_path: str,
    message: str,
    complete_message: str,
    runner,
    dependencies: list[str] | None = None,
) -> dict[str, Any]:
    progress = context.progress
    progress.start_stage(name, message=message)

    def stage_runner(stage_deck, stage_context: BuildContext) -> StageResult:
        report = runner(stage_deck)
        artifacts: dict[str, str] = {}
        if stage_context.artifacts is not None:
            stage_context.artifacts.write_json(artifact_name, artifact_path, report)
            registered = stage_context.artifacts.relative_path(artifact_name)
            if registered:
                artifacts[artifact_name] = registered
        return StageResult(name=name, report=report, artifacts=artifacts)

    stage = FunctionStage(
        name=name,
        dependencies=dependencies or [],
        artifacts=[artifact_name],
        runner=stage_runner,
    )
    result = run_stage(deck, context, stage)
    progress.finish_stage(complete_message)
    return result.report or {}


def _register_export_artifacts(artifacts: ArtifactRegistry, export_report: dict[str, Any]) -> None:
    results = export_report.get("results")
    if not isinstance(results, list):
        return
    for result in results:
        if not isinstance(result, dict) or result.get("status") != "ok":
            continue
        path = result.get("path")
        fmt = str(result.get("format") or "").replace("-", "_")
        if not path or not fmt:
            continue
        resolved = Path(path)
        if not resolved.is_absolute():
            resolved = artifacts.output_root / resolved
        artifacts.register(f"notes_{fmt}", resolved)


def _friendly_build_error(exc: Exception, args: argparse.Namespace) -> str | None:
    message = str(exc)
    if "Missing API key for provider `" not in message:
        return None
    provider = _provider_from_missing_key_error(message)
    if not provider:
        return None
    try:
        missing_spec = get_provider_spec(provider)
    except ValueError:
        return None

    if _vision_features_enabled(args):
        try:
            vision_spec = get_provider_spec(args.vision_provider)
        except ValueError:
            vision_spec = None
        if vision_spec and missing_spec.canonical_name == vision_spec.canonical_name:
            envs = ", ".join(vision_spec.api_key_envs)
            features = []
            if args.vision != "off":
                features.append("vision")
            if args.figure_crop == "vision" or (args.figure_crop == "auto" and args.vision != "off"):
                features.append("figure-crop")
            if args.figure_grounding == "vision":
                features.append("figure-grounding")
            feature_text = "/".join(features) or "vision"
            return (
                f"当前开启了 {feature_text}，但视觉模型 provider `{vision_spec.canonical_name}` 没有可用 API key。\n"
                f"请设置 {envs}，或传 `--vision-api-key ...`。\n"
                "如果只想用文本模型生成笔记，请加：`--vision off --figure-crop off`。"
            )

    if args.use_llm:
        try:
            text_spec = get_provider_spec(args.provider)
        except ValueError:
            text_spec = None
        if text_spec and missing_spec.canonical_name == text_spec.canonical_name:
            envs = ", ".join(text_spec.api_key_envs)
            return (
                f"当前开启了 `--use-llm`，但文本模型 provider `{text_spec.canonical_name}` 没有可用 API key。\n"
                f"请设置 {envs}，或传 `--api-key ...`。\n"
                "如果只想先生成本地规则草稿，请去掉 `--use-llm`。"
            )
    return None


def _provider_from_missing_key_error(message: str) -> str | None:
    marker = "Missing API key for provider `"
    start = message.find(marker)
    if start == -1:
        return None
    start += len(marker)
    end = message.find("`", start)
    if end == -1:
        return None
    return message[start:end]


def _vision_features_enabled(args: argparse.Namespace) -> bool:
    return (
        args.vision != "off"
        or args.figure_grounding == "vision"
        or args.figure_crop == "vision"
        or (args.figure_crop == "auto" and args.vision != "off")
    )


def _should_build_deck_brief(args: argparse.Namespace) -> bool:
    if args.deck_brief == "off":
        return False
    if args.deck_brief == "force":
        return True
    return bool(args.use_llm and args.note_strategy == "lecture-weave")


def _apply_speed_mode_defaults(args: argparse.Namespace) -> None:
    presets = {
        "fast": {
            "max_output_tokens": 2500,
            "ocr_max_targets": 40,
            "ocr_max_edge": 1200,
            "figure_max_targets": 25,
            "vision_max_targets": 25,
            "vision_max_edge": 1000,
            "vision_max_output_tokens": 800,
            "vision_detail": "low",
        },
        "balanced": {
            "max_output_tokens": 4096,
            "ocr_max_targets": 120,
            "ocr_max_edge": 1800,
            "figure_max_targets": 80,
            "vision_max_targets": 80,
            "vision_max_edge": 1400,
            "vision_max_output_tokens": 1200,
            "vision_detail": "low",
        },
        "quality": {
            "max_output_tokens": 7000,
            "ocr_max_targets": 0,
            "ocr_max_edge": 2200,
            "figure_max_targets": 160,
            "vision_max_targets": 160,
            "vision_max_edge": 1800,
            "vision_max_output_tokens": 2000,
            "vision_detail": "high",
        },
        "debug": {
            "max_output_tokens": 4096,
            "ocr_max_targets": 20,
            "ocr_max_edge": 1400,
            "figure_max_targets": 20,
            "vision_max_targets": 20,
            "vision_max_edge": 1200,
            "vision_max_output_tokens": 1000,
            "vision_detail": "low",
        },
    }
    preset = presets[args.speed_mode]
    for name, value in preset.items():
        if getattr(args, name, None) is None:
            setattr(args, name, value)


def _resolve_api_concurrency(args: argparse.Namespace) -> dict[str, int]:
    fallback = max(1, int(args.concurrency or 1))
    return {
        "llm": _coerce_api_concurrency(args.llm_concurrency, fallback),
        "vision": _coerce_api_concurrency(args.vision_concurrency, fallback),
        "ocr": _coerce_api_concurrency(args.ocr_concurrency, fallback),
        "figure": _coerce_api_concurrency(args.figure_concurrency, fallback),
    }


def _coerce_api_concurrency(value: int | None, fallback: int) -> int:
    if value is None:
        value = fallback
    return max(1, int(value))


def _resolve_cache_dirs(args: argparse.Namespace, output_root: Path) -> dict[str, Path | None]:
    global_cache = args.global_cache_dir.resolve() if args.global_cache_dir else None
    return {
        "llm": args.cache_dir.resolve() if args.cache_dir else (global_cache / "llm" if global_cache else None),
        "ocr": args.ocr_cache_dir.resolve() if args.ocr_cache_dir else (global_cache / "ocr" if global_cache else None),
        "vision": args.vision_cache_dir.resolve() if args.vision_cache_dir else (global_cache / "vision" if global_cache else None),
        "figure": args.figure_cache_dir.resolve() if args.figure_cache_dir else (global_cache / "figure" if global_cache else None),
        "sections": args.section_cache_dir.resolve() if args.section_cache_dir else (global_cache / "sections" if global_cache else None),
    }


def _parse_slide_ranges(value: str | None) -> set[int]:
    if not value:
        return set()
    result: set[int] = set()
    for raw_part in value.split(","):
        part = raw_part.strip()
        if not part:
            continue
        if "-" in part:
            start_text, end_text = part.split("-", 1)
            start = int(start_text)
            end = int(end_text)
            if start <= 0 or end <= 0 or end < start:
                raise ValueError(f"Invalid slide range: {part}")
            result.update(range(start, end + 1))
        else:
            slide_id = int(part)
            if slide_id <= 0:
                raise ValueError(f"Invalid slide id: {part}")
            result.add(slide_id)
    return result


def _target_progress(progress: ProgressReporter, name: str):
    def callback(event: dict[str, Any]) -> None:
        if event.get("event") == "start":
            progress.set_total(event.get("total"))
            return
        record = event.get("record") or {}
        slide_id = event.get("slide_id")
        cache_hit = record.get("cache_status") == "local_hit"
        api_call = bool(record.get("api_call") or record.get("llm_call"))
        skipped = record.get("cache_status") == "skipped"
        progress.advance(
            message=f"{name} slide {slide_id}",
            cache_hit=cache_hit,
            api_call=api_call,
            skipped=skipped,
        )

    return callback


def _llm_progress(progress: ProgressReporter):
    def callback(record: dict[str, Any]) -> None:
        label = record.get("context_id") or record.get("slide_id")
        progress.advance(
            message=f"LLM context {label}",
            cache_hit=record.get("cache_status") == "local_hit",
            api_call=bool(record.get("llm_call")),
        )

    return callback


def _stage_metrics(progress: ProgressReporter) -> dict[str, Any]:
    snapshot = progress.snapshot()
    stages = snapshot.get("stages") if isinstance(snapshot, dict) else []
    stage_records = [stage for stage in stages if isinstance(stage, dict)]
    return {
        "elapsed_seconds": snapshot.get("elapsed_seconds") if isinstance(snapshot, dict) else None,
        "stages": stage_records,
        "slowest_stages": _slowest_stage_records(stage_records, limit=3),
    }


def _slowest_stages(progress: ProgressReporter, limit: int = 3) -> list[dict[str, Any]]:
    snapshot = progress.snapshot()
    stages = snapshot.get("stages") if isinstance(snapshot, dict) else []
    return _slowest_stage_records([stage for stage in stages if isinstance(stage, dict)], limit=limit)


def _slowest_stage_records(stages: list[dict[str, Any]], limit: int = 3) -> list[dict[str, Any]]:
    ranked = sorted(stages, key=lambda stage: float(stage.get("elapsed_seconds") or 0.0), reverse=True)
    return [
        {
            "name": stage.get("name"),
            "elapsed_seconds": float(stage.get("elapsed_seconds") or 0.0),
            "api_calls": int(stage.get("api_calls") or 0),
            "cache_hits": int(stage.get("cache_hits") or 0),
            "skipped": int(stage.get("skipped") or 0),
        }
        for stage in ranked[: max(0, limit)]
    ]


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
    composite_figure_report: dict[str, Any] | None,
    figure_report: dict[str, Any] | None,
    figure_grounding_report: dict[str, Any] | None,
    ocr_report: dict[str, Any] | None,
    vision_report: dict[str, Any] | None,
    content_guard_report: dict[str, Any] | None,
    llm_usage: dict[str, Any] | None,
    coverage_report: dict[str, Any],
    source_map: dict[str, Any],
    cache_dirs: dict[str, Path | None],
    refresh_slide_ids: set[int],
    progress: ProgressReporter,
    note_asset_warnings: list[str],
    export_report: dict[str, Any] | None = None,
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
            "speed_mode": args.speed_mode,
            "concurrency": max(1, args.concurrency),
            "api_concurrency": api_concurrency or _resolve_api_concurrency(args),
            "refresh_slide_ids": sorted(refresh_slide_ids),
            "cache_dirs": {name: str(path) if path else None for name, path in cache_dirs.items()},
            "asset_mode": args.asset_mode,
            "source_display": args.source_display,
            "note_context": args.note_context,
            "note_style": args.note_style,
            "note_language": args.note_language,
            "term_policy": args.term_policy,
            "note_strategy": args.note_strategy,
            "note_depth": args.note_depth,
            "deck_brief": args.deck_brief,
            "content_guard": args.content_guard,
            "export": parse_export_formats(args.export),
            "export_toc": args.export_toc,
            "weave_dedup": args.weave_dedup,
            "page_neighborhood": args.page_neighborhood,
            "section_detection": args.section_detection,
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
        "ocr": ocr_report.get("summary") if ocr_report else None,
        "vision": vision_report.get("summary") if vision_report else None,
        "content_guard": content_guard_report.get("summary") if content_guard_report else None,
        "llm": llm_usage.get("summary") if llm_usage else None,
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
            "export": export_warnings(export_report),
        },
        "artifacts": {
            "content": "content.json",
            "element_ir": "element_ir.json",
            "notes": "notes.md",
            "note_assets": "notes.assets" if args.asset_mode == "bundle" else None,
            "coverage": "coverage.md",
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
            "content_guard": "content_guard.json" if content_guard_report else None,
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


def _build_ocr_export(deck, ocr_report):
    return {
        "schema_version": 1,
        "source_path": deck.source_path,
        "source_type": deck.source_type,
        "summary": ocr_report.get("summary", {}),
        "pages": [
            {
                "slide_id": page.slide_id,
                "page_screenshot": page.page_screenshot,
                "page_ocr_text": page.page_ocr_text,
                "page_ocr_status": page.page_ocr_status,
                "images": [
                    {
                        "id": image.id,
                        "path": image.path,
                        "ocr_text": image.ocr_text,
                        "ocr_status": image.ocr_status,
                    }
                    for image in page.images
                    if image.ocr_text or image.ocr_status
                ],
            }
            for page in deck.pages
            if page.page_ocr_text or page.page_ocr_status or any(image.ocr_text or image.ocr_status for image in page.images)
        ],
    }


def _build_figures_export(deck, figure_report):
    return {
        "schema_version": 1,
        "source_path": deck.source_path,
        "source_type": deck.source_type,
        "summary": figure_report.get("summary", {}),
        "pages": [
            {
                "slide_id": page.slide_id,
                "page_screenshot": page.page_screenshot,
                "figures": [
                    {
                        "id": image.id,
                        "path": image.path,
                        "caption": image.caption,
                        "crop_source_path": image.crop_source_path,
                        "crop_bbox": image.crop_bbox,
                        "crop_method": image.crop_method,
                        "crop_quality": image.crop_quality,
                        "crop_warnings": list(image.crop_warnings),
                        "confidence": image.confidence,
                        "width": image.width,
                        "height": image.height,
                        "importance_score": image.importance_score,
                        "importance_rank": image.importance_rank,
                        "importance_reason": image.importance_reason,
                        "source_element_ids": list(image.source_element_ids),
                    }
                    for image in page.images
                    if image.role in {"figure_crop", "composite_figure"}
                ],
            }
            for page in deck.pages
            if any(image.role in {"figure_crop", "composite_figure"} for image in page.images)
        ],
    }


def _build_visuals_export(deck, vision_report):
    return {
        "schema_version": 1,
        "source_path": deck.source_path,
        "source_type": deck.source_type,
        "summary": vision_report.get("summary", {}),
        "pages": [
            {
                "slide_id": page.slide_id,
                "page_screenshot": page.page_screenshot,
                "page_ocr_text": page.page_ocr_text,
                "page_ocr_status": page.page_ocr_status,
                "page_visual_summary": page.page_visual_summary,
                "page_visual_status": page.page_visual_status,
                "images": [
                    {
                        "id": image.id,
                        "path": image.path,
                        "ocr_text": image.ocr_text,
                        "ocr_status": image.ocr_status,
                        "visual_summary": image.visual_summary,
                        "visual_status": image.visual_status,
                        "importance_score": image.importance_score,
                        "importance_rank": image.importance_rank,
                    }
                    for image in page.images
                ],
            }
            for page in deck.pages
            if page.page_visual_summary or page.page_ocr_text or any(image.visual_summary or image.ocr_text for image in page.images)
        ],
    }
