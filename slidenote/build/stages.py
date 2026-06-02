from __future__ import annotations

from typing import Any

from slidenote.build.artifacts import (
    _build_figures_export,
    _build_ocr_export,
    _build_visuals_export,
    _register_export_artifacts,
    _run_json_stage,
)
from slidenote.build.config import _should_build_deck_brief, _vision_features_enabled
from slidenote.build.progress import _llm_progress, _slowest_stages, _target_progress
from slidenote.build.state import BuildState
from slidenote.build.summary import _build_run_summary
from slidenote.content_guard import build_content_guard, record_required_coverage
from slidenote.coverage import analyze_coverage, render_coverage_markdown
from slidenote.deck_brief import build_deck_brief, render_deck_brief_markdown
from slidenote.exporting import build_export_artifacts, export_blocking_failures
from slidenote.extractors import extract_deck
from slidenote.image_ranking import rank_deck_images
from slidenote.ir import build_deck_ir
from slidenote.models import Deck
from slidenote.notes import NoteGenerationResult, estimate_note_generation_steps, generate_notes_result
from slidenote.notes.quality import build_note_quality_report
from slidenote.ocr import enrich_deck_with_ocr
from slidenote.sections import build_section_plan
from slidenote.source_map import build_source_map
from slidenote.study_pack import (
    build_study_pack,
    render_exam_html,
    render_exam_markdown,
    render_final_exam_answers_markdown,
    render_final_exam_markdown,
    render_review_markdown,
    render_wrong_answer_review_prompt,
)
from slidenote.table_understanding import enrich_deck_with_table_understanding
from slidenote.visual.crop import enrich_deck_with_composite_figures, enrich_deck_with_figures
from slidenote.visual.grounding import enrich_deck_with_figure_grounding
from slidenote.visual.semantic_layout import enrich_deck_with_semantic_layout
from slidenote.visual.vision import enrich_deck_with_vision
from slidenote.modality import enrich_deck_with_modalities


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
    args = state.args
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
        runner=lambda stage_deck: enrich_deck_with_semantic_layout(
            stage_deck,
            output_root=state.output_root,
            mode=args.semantic_layout,
            provider=args.vision_provider,
            model=args.vision_model,
            api_key=args.vision_api_key,
            base_url=args.vision_base_url,
            cache_mode=args.vision_cache,
            cache_dir=state.cache_dirs["vision"],
            max_output_tokens=min(args.vision_max_output_tokens or 1000, 1400),
            temperature=args.vision_temperature,
            detail=args.vision_detail or "low",
            max_edge=args.vision_max_edge,
            concurrency=state.api_concurrency["vision"],
            refresh_slide_ids=state.refresh_slide_ids,
        ),
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
        provider=args.vision_provider,
        model=args.vision_model,
        api_key=args.vision_api_key,
        base_url=args.vision_base_url,
        cache_mode=args.vision_cache,
        cache_dir=state.cache_dirs["vision"],
        max_output_tokens=min(args.vision_max_output_tokens or 1000, 1400),
        temperature=args.vision_temperature,
        detail=args.vision_detail or "low",
        max_edge=args.vision_max_edge,
        concurrency=state.api_concurrency["vision"],
        refresh_slide_ids=state.refresh_slide_ids,
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
    state.artifacts.write_json("element_ir", "element_ir.json", build_deck_ir(deck, content_guard=state.content_guard_report))
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
    note_total = (
        estimate_note_generation_steps(
            deck,
            args.note_context,
            args.note_strategy,
            section_plan=state.section_report,
            note_profile=args.note_profile,
            teaching_enrichment=args.teaching_enrichment,
        )
        if args.use_llm
        else None
    )
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
        note_profile=args.note_profile,
        note_language=args.note_language,
        term_policy=args.term_policy,
        note_strategy=args.note_strategy,
        note_depth=args.note_depth,
        teaching_enrichment=args.teaching_enrichment,
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
    if state.notes_result.teaching_report is not None:
        state.artifacts.write_json("teaching_enrichment", "teaching_enrichment.json", state.notes_result.teaching_report)
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
    state.artifacts.write_json(
        "element_ir",
        "element_ir.json",
        build_deck_ir(deck, content_guard=state.content_guard_report, coverage_report=state.coverage_report),
    )
    state.source_map = build_source_map(deck, state.notes_markdown, state.output_root)
    state.artifacts.write_json("source_map", "source_map.json", state.source_map)
    state.progress.finish_stage("Coverage complete")


def _stage_quality_report(state: BuildState) -> None:
    args = state.args
    deck = _require_deck(state)
    state.progress.start_stage("quality_report", message="Scoring note learning quality")
    state.quality_report = build_note_quality_report(
        deck=deck,
        notes_markdown=state.notes_markdown,
        coverage_report=state.coverage_report,
        note_profile=args.note_profile,
        note_context=args.note_context,
        note_strategy=args.note_strategy,
        note_depth=args.note_depth,
        study_pack_report=state.study_pack_report,
    )
    state.artifacts.write_json("quality_report", "quality_report.json", state.quality_report)
    state.progress.finish_stage("Quality report complete")


def _stage_study_pack(state: BuildState) -> None:
    args = state.args
    if args.review_mode == "off" and args.exam_mode == "off":
        return
    deck = _require_deck(state)
    state.progress.start_stage("study_pack", message="Generating review and exam pack")
    state.study_pack_report = build_study_pack(
        deck=deck,
        notes_markdown=state.notes_markdown,
        output_root=state.output_root,
        review_mode=args.review_mode,
        exam_mode=args.exam_mode,
        question_count=args.exam_question_count,
        use_llm=args.use_llm,
        provider=args.provider,
        model=args.model,
        api_key=args.api_key,
        base_url=args.base_url,
        cache_mode=args.cache,
        cache_dir=state.cache_dirs["llm"],
        max_output_tokens=min(args.max_output_tokens or 4096, 7000),
        temperature=args.temperature if args.temperature is not None else 0.0,
        note_language=args.note_language,
        section_plan=state.section_report,
        deck_brief=state.deck_brief_report,
        content_guard=state.content_guard_report,
        coverage_report=state.coverage_report,
        source_map=state.source_map,
    )
    if state.study_pack_report is not None:
        state.artifacts.write_json("study_pack", "study_pack.json", state.study_pack_report)
        if state.study_pack_report.get("review"):
            state.artifacts.write_text("review_markdown", "review.md", render_review_markdown(state.study_pack_report))
        if state.study_pack_report.get("exam"):
            state.artifacts.write_json("exam_json", "exam.json", state.study_pack_report["exam"])
            state.artifacts.write_text("exam_markdown", "exam.md", render_exam_markdown(state.study_pack_report))
            state.artifacts.write_text("exam_html", "exam.html", render_exam_html(state.study_pack_report))
        if state.study_pack_report.get("section_study_pack"):
            state.artifacts.write_json("section_study_pack", "section_study_pack.json", state.study_pack_report["section_study_pack"])
        if state.study_pack_report.get("exam_review_pack"):
            state.artifacts.write_json("exam_review_pack", "exam_review_pack.json", state.study_pack_report["exam_review_pack"])
        if state.study_pack_report.get("final_exam"):
            state.artifacts.write_text("final_exam_markdown", "final_exam.md", render_final_exam_markdown(state.study_pack_report))
            state.artifacts.write_text("final_exam_answers", "final_exam.answers.md", render_final_exam_answers_markdown(state.study_pack_report))
        if state.study_pack_report.get("wrong_answer_review"):
            state.artifacts.write_text("wrong_answer_review_prompt", "wrong_answer_review_prompt.md", render_wrong_answer_review_prompt(state.study_pack_report))
    state.progress.finish_stage("Review and exam pack complete")


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
        quality_report=state.quality_report,
        source_map=source_map,
        cache_dirs=state.cache_dirs,
        refresh_slide_ids=state.refresh_slide_ids,
        progress=state.progress,
        note_asset_warnings=notes_result.asset_warnings or [],
        export_report=state.export_report,
        study_pack_report=state.study_pack_report,
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
    print(f"- quality:  {output_root / 'quality_report.json'}")
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
    if state.study_pack_report is not None:
        print(f"- study:    {output_root / 'study_pack.json'}")
        if state.study_pack_report.get("review"):
            print(f"- review:   {output_root / 'review.md'}")
        if state.study_pack_report.get("exam"):
            print(f"- exam:     {output_root / 'exam.md'}")
            print(f"- exam html:{output_root / 'exam.html'}")
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


BUILD_STAGES = (
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
    _stage_deck_brief,
    _stage_content_guard,
    _stage_export_content,
    _stage_notes,
    _stage_coverage,
    _stage_study_pack,
    _stage_quality_report,
    _stage_export,
    _stage_run_summary,
)
