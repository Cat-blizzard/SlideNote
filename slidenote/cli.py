from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from slidenote.coverage import analyze_coverage, render_coverage_markdown
from slidenote.doctor import render_doctor_report, run_doctor
from slidenote.extractors import extract_deck
from slidenote.figures import enrich_deck_with_figures
from slidenote.image_ranking import rank_deck_images
from slidenote.llm import supported_provider_names
from slidenote.modality import enrich_deck_with_modalities
from slidenote.notes import estimate_note_generation_steps, generate_notes_result
from slidenote.ocr import enrich_deck_with_ocr
from slidenote.progress import ProgressReporter
from slidenote.sections import build_section_plan
from slidenote.source_map import build_source_map
from slidenote.utils import ensure_clean_dir, write_json, write_text
from slidenote.vision import enrich_deck_with_vision


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "build":
        return _build(args)
    if args.command == "doctor":
        return _doctor(args)
    parser.print_help()
    return 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="slidenote", description="Coverage-aware course note generator.")
    subparsers = parser.add_subparsers(dest="command")

    build = subparsers.add_parser("build", help="Build notes from a PPTX/PPT/PDF file.")
    build.add_argument("input", type=Path, help="Input .pptx, .ppt, or .pdf file.")
    build.add_argument("--out", type=Path, default=Path("outputs") / "slidenote", help="Output directory.")
    build.add_argument(
        "--speed-mode",
        choices=["fast", "balanced", "quality", "debug"],
        default="quality",
        help="Preset for cost/time limits. Defaults to quality; it fills unset limits but does not enable OCR or LLM by itself.",
    )
    build.add_argument("--concurrency", type=int, default=1, help="Maximum parallel API calls for OCR, vision, and page-level LLM work.")
    build.add_argument("--global-cache-dir", type=Path, default=None, help="Shared cache root. Defaults to per-output .cache folders.")
    build.add_argument(
        "--refresh-pages",
        default=None,
        help="Comma-separated slide IDs or ranges to bypass local cache, for example 3,5-8.",
    )
    build.add_argument("--progress-json", type=Path, default=None, help="Progress JSON path. Defaults to <out>/progress.json.")
    build.add_argument("--quiet", action="store_true", help="Suppress live progress output while still writing progress.json.")
    build.add_argument("--use-llm", action="store_true", help="Use an LLM provider for AI rewriting.")
    build.add_argument(
        "--provider",
        default="openai",
        help=f"LLM provider when --use-llm is enabled. Supported: {', '.join(supported_provider_names())}.",
    )
    build.add_argument("--model", default=None, help="Model name or provider endpoint id when --use-llm is enabled.")
    build.add_argument("--api-key", default=None, help="API key override. Prefer environment variables for normal use.")
    build.add_argument("--base-url", default=None, help="Provider base URL override for compatible/proxy endpoints.")
    build.add_argument("--max-output-tokens", type=int, default=None, help="Maximum generated tokens per page.")
    build.add_argument("--temperature", type=float, default=None, help="Optional model temperature.")
    build.add_argument(
        "--asset-mode",
        choices=["bundle", "absolute", "embed"],
        default="bundle",
        help="How notes.md references images. bundle copies assets into notes.assets, absolute uses local absolute paths, embed writes data URLs.",
    )
    build.add_argument(
        "--source-display",
        choices=["hidden", "footnote", "inline"],
        default="hidden",
        help="How source page/element references appear in notes.md.",
    )
    build.add_argument(
        "--note-context",
        choices=["auto", "document", "section", "page"],
        default="section",
        help="LLM note generation context. section is the quality-first default; auto uses document context for short files and section context for larger files.",
    )
    build.add_argument(
        "--note-style",
        choices=["article", "faithful"],
        default="article",
        help="Article mode favors fluent notes; faithful mode keeps closer slide order.",
    )
    build.add_argument(
        "--note-strategy",
        choices=["direct", "lecture-weave"],
        default="lecture-weave",
        help="direct uses the selected context directly; lecture-weave first explains each page, then weaves sections.",
    )
    build.add_argument(
        "--note-depth",
        choices=["concise", "balanced", "detailed"],
        default="detailed",
        help="Detail level for LLM note writing. detailed is best for lecture-weave quality mode.",
    )
    build.add_argument(
        "--weave-dedup",
        choices=["soft", "normal", "aggressive"],
        default="soft",
        help="How strongly lecture-weave merges repeated page-note content.",
    )
    build.add_argument(
        "--page-neighborhood",
        type=int,
        choices=[0, 1, 2],
        default=1,
        help="How many nearby page titles/briefs each page lecture may see.",
    )
    build.add_argument(
        "--screenshot-policy",
        choices=["fallback", "always", "never"],
        default="fallback",
        help="How notes.md includes full-page screenshots. fallback shows them only when no local figure/image exists.",
    )
    build.add_argument(
        "--section-detection",
        choices=["auto", "local", "llm"],
        default="auto",
        help="How SlideNote detects section boundaries. auto uses LLM section detection when LLM notes are enabled, otherwise local rules.",
    )
    build.add_argument(
        "--section-cache",
        choices=["on", "off", "refresh"],
        default="on",
        help="Section detection cache mode when --section-detection uses an LLM.",
    )
    build.add_argument("--section-cache-dir", type=Path, default=None, help="Section detection cache directory. Defaults to <out>/.cache/sections.")
    build.add_argument(
        "--cache",
        choices=["on", "off", "refresh"],
        default="on",
        help="LLM local cache mode: on reads/writes, refresh rewrites, off disables.",
    )
    build.add_argument(
        "--cache-dir",
        type=Path,
        default=None,
        help="LLM cache directory. Defaults to <out>/.cache/llm.",
    )
    build.add_argument(
        "--ocr",
        choices=["off", "auto", "all"],
        default="off",
        help="Dedicated OCR mode before vision/note generation. auto OCRs pages with little extracted text.",
    )
    build.add_argument(
        "--ocr-provider",
        default="baidu",
        help="OCR provider. Supported: baidu, mathpix, google.",
    )
    build.add_argument("--ocr-api-key", default=None, help="OCR API key/app id override.")
    build.add_argument("--ocr-secret-key", default=None, help="OCR secret key/app key override when the provider needs one.")
    build.add_argument("--ocr-endpoint", default=None, help="OCR endpoint override.")
    build.add_argument("--ocr-language", default="CHN_ENG", help="OCR language hint, for example CHN_ENG, ENG, or CHN.")
    build.add_argument(
        "--ocr-cache",
        choices=["on", "off", "refresh"],
        default="on",
        help="OCR local cache mode.",
    )
    build.add_argument("--ocr-cache-dir", type=Path, default=None, help="OCR cache directory. Defaults to <out>/.cache/ocr.")
    build.add_argument("--ocr-max-targets", type=int, default=None, help="Maximum OCR targets. Use 0 for unlimited.")
    build.add_argument("--ocr-min-text-chars", type=int, default=80, help="auto mode OCRs pages below this extracted text length.")
    build.add_argument("--ocr-min-area", type=int, default=120000, help="Minimum embedded image pixel area for OCR fallback.")
    build.add_argument("--ocr-max-edge", type=int, default=None, help="Resize OCR target long edge before API calls.")
    build.add_argument(
        "--figure-crop",
        choices=["off", "auto", "vision"],
        default="auto",
        help="Crop meaningful local figures from page screenshots. auto only runs when vision is enabled; vision forces bbox detection.",
    )
    build.add_argument("--figure-max-targets", type=int, default=None, help="Maximum pages to send for figure bbox detection.")
    build.add_argument("--figure-max-crops-per-page", type=int, default=3, help="Maximum local figure crops per page.")
    build.add_argument("--figure-min-confidence", type=float, default=0.45, help="Minimum model confidence for accepting a figure crop.")
    build.add_argument("--figure-min-area", type=int, default=40000, help="Minimum crop area in pixels.")
    build.add_argument(
        "--image-ranking",
        choices=["off", "local"],
        default="local",
        help="Rank images by study value before vision and note generation.",
    )
    build.add_argument(
        "--figure-cache",
        choices=["on", "off", "refresh"],
        default="on",
        help="Figure crop local cache mode.",
    )
    build.add_argument("--figure-cache-dir", type=Path, default=None, help="Figure crop cache directory. Defaults to <out>/.cache/figure.")
    build.add_argument(
        "--vision",
        choices=["off", "auto", "all"],
        default="auto",
        help="Vision extraction mode before note generation. auto selects high-value images; all reads every page screenshot when possible.",
    )
    build.add_argument(
        "--vision-provider",
        default="openai",
        help="Vision provider. Supported image providers currently include openai, qwen, doubao, gemini, and claude.",
    )
    build.add_argument("--vision-model", default=None, help="Vision model override.")
    build.add_argument("--vision-api-key", default=None, help="Vision API key override. Prefer environment variables.")
    build.add_argument("--vision-base-url", default=None, help="Vision provider base URL override.")
    build.add_argument(
        "--vision-cache",
        choices=["on", "off", "refresh"],
        default="on",
        help="Vision local cache mode.",
    )
    build.add_argument("--vision-cache-dir", type=Path, default=None, help="Vision cache directory. Defaults to <out>/.cache/vision.")
    build.add_argument("--vision-max-targets", type=int, default=None, help="Maximum images/screenshots to parse. Use 0 for unlimited.")
    build.add_argument("--vision-min-area", type=int, default=120000, help="Minimum embedded image pixel area for auto fallback selection.")
    build.add_argument("--vision-max-edge", type=int, default=None, help="Resize image long edge before API calls to reduce cost.")
    build.add_argument("--vision-max-output-tokens", type=int, default=None, help="Maximum vision output tokens per image.")
    build.add_argument("--vision-temperature", type=float, default=0.0, help="Vision model temperature.")
    build.add_argument("--vision-detail", choices=["low", "high", "auto"], default=None, help="OpenAI image detail setting.")

    doctor = subparsers.add_parser("doctor", help="Check local dependencies, optional tools, and API key environment variables.")
    doctor.add_argument("--json", type=Path, default=None, help="Write the doctor report as JSON to this path.")
    return parser


def _doctor(args: argparse.Namespace) -> int:
    report = run_doctor()
    if args.json:
        write_json(args.json.resolve(), report)
    print(render_doctor_report(report))
    return 0


def _build(args: argparse.Namespace) -> int:
    _apply_speed_mode_defaults(args)
    input_path = args.input.resolve()
    output_root = args.out.resolve()
    if not input_path.exists():
        raise FileNotFoundError(input_path)

    ensure_clean_dir(output_root)
    progress = ProgressReporter((args.progress_json or (output_root / "progress.json")).resolve(), quiet=args.quiet)
    refresh_slide_ids = _parse_slide_ranges(args.refresh_pages)
    concurrency = max(1, args.concurrency)
    cache_dirs = _resolve_cache_dirs(args, output_root)

    try:
        progress.start_stage("parse", message=f"Parsing {input_path.name}")
        deck = extract_deck(input_path, output_root)
        progress.finish_stage(f"Parsed {len(deck.pages)} pages")

        progress.start_stage("modality", message="Classifying page modalities")
        modality_report = enrich_deck_with_modalities(deck)
        write_json(output_root / "page_modalities.json", modality_report)
        progress.finish_stage("Page modality classification complete")

        figure_report = None
        should_run_figure_crop = args.figure_crop == "vision" or (args.figure_crop == "auto" and args.vision != "off")
        if should_run_figure_crop:
            progress.start_stage("figure_crop", message="Cropping local figures")
            figure_report = enrich_deck_with_figures(
                deck,
                output_root=output_root,
                mode=args.figure_crop,
                provider=args.vision_provider,
                model=args.vision_model,
                api_key=args.vision_api_key,
                base_url=args.vision_base_url,
                cache_mode=args.figure_cache,
                cache_dir=cache_dirs["figure"],
                max_targets=args.figure_max_targets,
                max_crops_per_page=args.figure_max_crops_per_page,
                min_confidence=args.figure_min_confidence,
                min_area=args.figure_min_area,
                max_output_tokens=min(args.vision_max_output_tokens or 1000, 1200),
                temperature=args.vision_temperature,
                detail=args.vision_detail,
                max_edge=args.vision_max_edge,
                concurrency=concurrency,
                refresh_slide_ids=refresh_slide_ids,
                progress_callback=_target_progress(progress, "figure"),
            )
            progress.finish_stage("Figure crop complete")

        image_importance_report = None
        if args.image_ranking != "off":
            progress.start_stage("image_importance", message="Ranking image importance")
            image_importance_report = rank_deck_images(deck, output_root, mode=args.image_ranking, stage="pre_vision")
            progress.finish_stage("Image importance ranking complete")

        ocr_report = None
        if args.ocr != "off":
            progress.start_stage("ocr", message="Running OCR")
            ocr_report = enrich_deck_with_ocr(
                deck,
                output_root=output_root,
                mode=args.ocr,
                provider=args.ocr_provider,
                api_key=args.ocr_api_key,
                secret_key=args.ocr_secret_key,
                endpoint=args.ocr_endpoint,
                language=args.ocr_language,
                cache_mode=args.ocr_cache,
                cache_dir=cache_dirs["ocr"],
                max_targets=args.ocr_max_targets,
                min_text_chars=args.ocr_min_text_chars,
                min_area=args.ocr_min_area,
                max_edge=args.ocr_max_edge,
                concurrency=concurrency,
                refresh_slide_ids=refresh_slide_ids,
                progress_callback=_target_progress(progress, "ocr"),
            )
            progress.finish_stage("OCR complete")

        vision_report = None
        if args.vision != "off":
            progress.start_stage("vision", message="Running vision analysis")
            vision_report = enrich_deck_with_vision(
                deck,
                output_root=output_root,
                mode=args.vision,
                provider=args.vision_provider,
                model=args.vision_model,
                api_key=args.vision_api_key,
                base_url=args.vision_base_url,
                cache_mode=args.vision_cache,
                cache_dir=cache_dirs["vision"],
                max_output_tokens=args.vision_max_output_tokens,
                temperature=args.vision_temperature,
                detail=args.vision_detail,
                max_targets=args.vision_max_targets,
                min_area=args.vision_min_area,
                max_edge=args.vision_max_edge,
                concurrency=concurrency,
                refresh_slide_ids=refresh_slide_ids,
                progress_callback=_target_progress(progress, "vision"),
            )
            progress.finish_stage("Vision analysis complete")

        if image_importance_report is not None and vision_report is not None:
            image_importance_report = rank_deck_images(deck, output_root, mode=args.image_ranking, stage="post_vision")

        progress.start_stage("sections", message="Detecting note sections")
        section_report = build_section_plan(
            deck,
            output_root=output_root,
            mode=args.section_detection,
            use_llm=args.use_llm and (args.note_context == "section" or (args.note_context == "auto" and len(deck.pages) > 12)),
            provider=args.provider,
            model=args.model,
            api_key=args.api_key,
            base_url=args.base_url,
            cache_mode=args.section_cache,
            cache_dir=cache_dirs["sections"],
            max_output_tokens=min(args.max_output_tokens or 1800, 2500),
            temperature=0.0,
        )
        write_json(output_root / "sections.json", section_report)
        progress.finish_stage("Section detection complete")

        progress.start_stage("export_content", message="Writing structured content")
        write_json(output_root / "content.json", deck.to_dict())
        if image_importance_report is not None:
            write_json(output_root / "image_importance.json", image_importance_report)
        if figure_report is not None:
            write_json(output_root / "figures.json", _build_figures_export(deck, figure_report))
            write_json(output_root / "figure_usage.json", figure_report)
        if ocr_report is not None:
            write_json(output_root / "ocr.json", _build_ocr_export(deck, ocr_report))
            write_json(output_root / "ocr_usage.json", ocr_report)
        if vision_report is not None:
            write_json(output_root / "visuals.json", _build_visuals_export(deck, vision_report))
            write_json(output_root / "vision_usage.json", vision_report)
        progress.finish_stage("Structured content written")

        note_total = estimate_note_generation_steps(deck, args.note_context, args.note_strategy, section_plan=section_report) if args.use_llm else None
        progress.start_stage("notes", total=note_total, message="Generating notes")
        notes_result = generate_notes_result(
            deck,
            output_root,
            use_llm=args.use_llm,
            provider=args.provider,
            model=args.model,
            api_key=args.api_key,
            base_url=args.base_url,
            max_output_tokens=args.max_output_tokens,
            temperature=args.temperature,
            cache_mode=args.cache,
            cache_dir=cache_dirs["llm"],
            concurrency=concurrency,
            refresh_slide_ids=refresh_slide_ids,
            progress_callback=_llm_progress(progress),
            asset_mode=args.asset_mode,
            source_display=args.source_display,
            note_context=args.note_context,
            note_style=args.note_style,
            note_strategy=args.note_strategy,
            note_depth=args.note_depth,
            weave_dedup=args.weave_dedup,
            page_neighborhood=args.page_neighborhood,
            screenshot_policy=args.screenshot_policy,
            section_plan=section_report,
        )
        notes_markdown = notes_result.markdown
        write_text(output_root / "notes.md", notes_markdown)
        if notes_result.llm_usage is not None:
            write_json(output_root / "llm_usage.json", notes_result.llm_usage)
        if notes_result.page_notes is not None:
            write_json(output_root / "page_notes.json", notes_result.page_notes)
        if notes_result.page_notes_markdown is not None:
            write_text(output_root / "page_notes.md", notes_result.page_notes_markdown)
        if notes_result.weave_report is not None:
            write_json(output_root / "weave_report.json", notes_result.weave_report)
        if not args.use_llm:
            progress.advance(message="Local notes generated")
        progress.finish_stage("Notes generated")

        progress.start_stage("coverage", message="Checking coverage")
        coverage_report = analyze_coverage(deck, notes_markdown)
        write_json(output_root / "coverage.json", coverage_report)
        write_text(output_root / "coverage.md", render_coverage_markdown(coverage_report))
        source_map = build_source_map(deck, notes_markdown, output_root)
        write_json(output_root / "source_map.json", source_map)
        progress.finish_stage("Coverage complete")

        progress.complete("Build complete")
        run_summary = _build_run_summary(
            args=args,
            input_path=input_path,
            output_root=output_root,
            deck=deck,
            modality_report=modality_report,
            image_importance_report=image_importance_report,
            section_report=section_report,
            figure_report=figure_report,
            ocr_report=ocr_report,
            vision_report=vision_report,
            llm_usage=notes_result.llm_usage,
            coverage_report=coverage_report,
            source_map=source_map,
            cache_dirs=cache_dirs,
            refresh_slide_ids=refresh_slide_ids,
            progress=progress,
            note_asset_warnings=notes_result.asset_warnings or [],
        )
        write_json(output_root / "run_summary.json", run_summary)
    except Exception as exc:
        progress.fail(str(exc))
        raise

    if not args.quiet:
        print(f"SlideNote build complete: {output_root}")
        print(f"- content:  {output_root / 'content.json'}")
        print(f"- notes:    {output_root / 'notes.md'}")
        print(f"- coverage: {output_root / 'coverage.md'}")
        print(f"- sources:  {output_root / 'source_map.json'}")
        print(f"- modalities: {output_root / 'page_modalities.json'}")
        print(f"- sections: {output_root / 'sections.json'}")
        print(f"- progress: {progress.path}")
        print(f"- summary:  {output_root / 'run_summary.json'}")
        if image_importance_report is not None:
            print(f"- image rank: {output_root / 'image_importance.json'}")
        if notes_result.llm_usage is not None:
            print(f"- llm use:  {output_root / 'llm_usage.json'}")
        if notes_result.page_notes is not None:
            print(f"- page notes: {output_root / 'page_notes.md'}")
            print(f"- page json:  {output_root / 'page_notes.json'}")
        if notes_result.weave_report is not None:
            print(f"- weave:    {output_root / 'weave_report.json'}")
        if figure_report is not None:
            print(f"- figures:  {output_root / 'figures.json'}")
            print(f"- fig use:  {output_root / 'figure_usage.json'}")
        if ocr_report is not None:
            print(f"- ocr:      {output_root / 'ocr.json'}")
            print(f"- ocr use:  {output_root / 'ocr_usage.json'}")
        if vision_report is not None:
            print(f"- visuals:  {output_root / 'visuals.json'}")
            print(f"- vision:   {output_root / 'vision_usage.json'}")
    return 0


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


def _build_run_summary(
    args: argparse.Namespace,
    input_path: Path,
    output_root: Path,
    deck,
    modality_report: dict[str, Any],
    image_importance_report: dict[str, Any] | None,
    section_report: dict[str, Any],
    figure_report: dict[str, Any] | None,
    ocr_report: dict[str, Any] | None,
    vision_report: dict[str, Any] | None,
    llm_usage: dict[str, Any] | None,
    coverage_report: dict[str, Any],
    source_map: dict[str, Any],
    cache_dirs: dict[str, Path | None],
    refresh_slide_ids: set[int],
    progress: ProgressReporter,
    note_asset_warnings: list[str],
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
            "refresh_slide_ids": sorted(refresh_slide_ids),
            "cache_dirs": {name: str(path) if path else None for name, path in cache_dirs.items()},
            "asset_mode": args.asset_mode,
            "source_display": args.source_display,
            "note_context": args.note_context,
            "note_style": args.note_style,
            "note_strategy": args.note_strategy,
            "note_depth": args.note_depth,
            "weave_dedup": args.weave_dedup,
            "page_neighborhood": args.page_neighborhood,
            "section_detection": args.section_detection,
            "image_ranking": args.image_ranking,
            "figure_crop": args.figure_crop,
            "screenshot_policy": args.screenshot_policy,
        },
        "counts": {
            "pages": len(pages),
            "text_blocks": sum(len(page.text_blocks) for page in pages),
            "tables": sum(len(page.tables) for page in pages),
            "images": images_count,
            "figure_crops": sum(1 for page in pages for image in page.images if image.role == "figure_crop"),
            "page_screenshots": sum(1 for page in pages if page.page_screenshot),
        },
        "figure_crop": figure_report.get("summary") if figure_report else None,
        "page_modalities": modality_report.get("summary") if modality_report else None,
        "image_importance": image_importance_report.get("summary") if image_importance_report else None,
        "sections": section_report.get("summary") if section_report else None,
        "ocr": ocr_report.get("summary") if ocr_report else None,
        "vision": vision_report.get("summary") if vision_report else None,
        "llm": llm_usage.get("summary") if llm_usage else None,
        "coverage": {
            "total": coverage_report.get("total"),
            "covered": coverage_report.get("covered"),
            "missing": coverage_report.get("missing"),
            "coverage_ratio": coverage_report.get("coverage_ratio"),
        },
        "source_map": {
            "note_blocks": len(source_map.get("note_blocks", [])),
            "default_display_mode": source_map.get("default_display_mode"),
        },
        "warnings": {
            "note_assets": note_asset_warnings,
        },
        "artifacts": {
            "content": "content.json",
            "notes": "notes.md",
            "note_assets": "notes.assets" if args.asset_mode == "bundle" else None,
            "coverage": "coverage.md",
            "source_map": "source_map.json",
            "progress": _display_path(progress.path, output_root),
            "run_summary": "run_summary.json",
            "page_modalities": "page_modalities.json",
            "image_importance": "image_importance.json" if image_importance_report else None,
            "sections": "sections.json",
            "figures": "figures.json" if figure_report else None,
            "figure_usage": "figure_usage.json" if figure_report else None,
            "llm_usage": "llm_usage.json" if llm_usage else None,
            "page_notes": "page_notes.json" if getattr(args, "note_strategy", "direct") == "lecture-weave" and llm_usage else None,
            "page_notes_markdown": "page_notes.md" if getattr(args, "note_strategy", "direct") == "lecture-weave" and llm_usage else None,
            "weave_report": "weave_report.json" if getattr(args, "note_strategy", "direct") == "lecture-weave" and llm_usage else None,
            "ocr_usage": "ocr_usage.json" if ocr_report else None,
            "vision_usage": "vision_usage.json" if vision_report else None,
        },
        "progress": progress.snapshot(),
    }


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
                        "confidence": image.confidence,
                        "width": image.width,
                        "height": image.height,
                        "importance_score": image.importance_score,
                        "importance_rank": image.importance_rank,
                        "importance_reason": image.importance_reason,
                    }
                    for image in page.images
                    if image.role == "figure_crop"
                ],
            }
            for page in deck.pages
            if any(image.role == "figure_crop" for image in page.images)
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


if __name__ == "__main__":
    raise SystemExit(main())
