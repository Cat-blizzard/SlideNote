from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from slidenote.build.artifacts import remove_stale_build_artifacts
from slidenote.build.errors import UserFacingConfigError
from slidenote.build.config import _parse_slide_ranges, _resolve_api_concurrency, _resolve_cache_dirs
from slidenote.models import Deck
from slidenote.notes import NoteGenerationResult
from slidenote.pipeline import ArtifactRegistry
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
    deck_understanding_report: dict[str, Any] | None = None
    page_understanding_report: dict[str, Any] | None = None
    notes_result: NoteGenerationResult | None = None
    notes_markdown: str = ""
    coverage_report: dict[str, Any] | None = None
    quality_report: dict[str, Any] | None = None
    source_map: dict[str, Any] | None = None
    export_report: dict[str, Any] | None = None
    export_exit_code: int = 0

def resolve_progress_path(args: argparse.Namespace) -> Path:
    return (args.progress_json or (args.out / "progress.json")).resolve()


def create_build_state(args: argparse.Namespace, export_formats: list[str]) -> BuildState:
    input_path = args.input.resolve()
    output_root = args.out.resolve()
    if not input_path.exists():
        raise UserFacingConfigError(f"Input file not found: {input_path}")
    try:
        refresh_slide_ids = _parse_slide_ranges(args.refresh_pages)
    except ValueError as exc:
        raise UserFacingConfigError(f"Invalid refresh page range `{args.refresh_pages}`: {exc}") from exc

    ensure_clean_dir(output_root)
    progress_path = resolve_progress_path(args)
    remove_stale_build_artifacts(output_root, keep=(input_path, progress_path))
    progress = ProgressReporter(progress_path, quiet=args.quiet)
    concurrency = max(1, args.concurrency)
    api_concurrency = _resolve_api_concurrency(args)
    cache_dirs = _resolve_cache_dirs(args, output_root)
    artifacts = ArtifactRegistry(output_root)
    artifacts.register("progress", progress.path)
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
        export_formats=export_formats,
    )
