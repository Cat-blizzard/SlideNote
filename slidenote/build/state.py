from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from slidenote.build.config import _parse_slide_ranges, _resolve_api_concurrency, _resolve_cache_dirs
from slidenote.models import Deck
from slidenote.notes import NoteGenerationResult
from slidenote.pipeline import ArtifactRegistry, BuildContext
from slidenote.progress import ProgressReporter
from slidenote.utils import ensure_clean_dir


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
    study_pack_report: dict[str, Any] | None = None
    export_report: dict[str, Any] | None = None
    export_exit_code: int = 0

def create_build_state(args: argparse.Namespace, export_formats: list[str]) -> BuildState:
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
