from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from slidenote.pipeline import ArtifactRegistry

# Files a build (or a follow-up study-pack/cost run) writes into the output
# directory. They are removed at build start so artifacts from an earlier run
# with different options (e.g. vision_usage.json after `--vision off`) cannot be
# mistaken for results of the current run. `.cache/`, user-written
# `page_modalities.overrides.json`, progress.json (rewritten immediately) and
# any unknown files are intentionally preserved.
GENERATED_ARTIFACT_FILES = (
    "content.json",
    "element_ir.json",
    "notes.md",
    "coverage.md",
    "coverage.json",
    "quality_report.json",
    "source_map.json",
    "run_summary.json",
    "page_modalities.json",
    "table_understanding.json",
    "semantic_layout.json",
    "image_importance.json",
    "composite_figures.json",
    "sections.json",
    "deck_brief.json",
    "deck_brief.md",
    "deck_understanding.json",
    "page_understanding.json",
    "content_guard.json",
    "figures.json",
    "figure_usage.json",
    "figure_grounding.json",
    "ocr.json",
    "ocr_usage.json",
    "visuals.json",
    "vision_usage.json",
    "llm_usage.json",
    "page_notes.json",
    "page_notes.md",
    "weave_report.json",
    "teaching_enrichment.json",
    "export_report.json",
    "notes.zip",
    "notes.toc.md",
    "notes.docx",
    "notes.pdf",
    "notes.tex",
    "cost_report.json",
    "cost_report.md",
    "cost_dashboard.html",
    "study_pack.json",
    "review.md",
    "exam.json",
    "exam.md",
    "exam.html",
    "section_study_pack.json",
    "exam_review_pack.json",
    "final_exam.md",
    "final_exam.answers.md",
    "wrong_answer_review_prompt.md",
)
GENERATED_ARTIFACT_DIRS = ("notes.assets", "figures", "images", "screenshots")
# A progress.json file can also be created by a failed setup attempt, so it is
# not enough to identify a directory as a previous SlideNote build.


def _is_previous_build(output_root: Path) -> bool:
    for name in ("run_summary.json", "content.json"):
        try:
            marker = json.loads((output_root / name).read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if not isinstance(marker, dict):
            continue
        if name == "run_summary.json":
            artifacts = marker.get("artifacts")
            if (
                marker.get("schema_version") == 1
                and isinstance(marker.get("source_path"), str)
                and isinstance(marker.get("source_type"), str)
                and isinstance(artifacts, dict)
                and artifacts.get("content") == "content.json"
            ):
                return True
        elif (
            isinstance(marker.get("source_path"), str)
            and marker.get("source_type") in {"pdf", "pptx"}
            and isinstance(marker.get("pages"), list)
            and all(isinstance(page, dict) and isinstance(page.get("slide_id"), int) for page in marker["pages"])
        ):
            return True
    return False


def remove_stale_build_artifacts(output_root: Path, keep: tuple[Path, ...] = ()) -> list[str]:
    """Delete known SlideNote-generated artifacts from a previous run.

    Paths in ``keep`` (and anything containing them, such as the input file
    living inside ``images/``) are never removed. Returns the removed names.
    """
    if not _is_previous_build(output_root):
        return []
    resolved_keep = [path.resolve() for path in keep]
    removed: list[str] = []
    for name in (*GENERATED_ARTIFACT_FILES, *GENERATED_ARTIFACT_DIRS):
        target = output_root / name
        if not target.exists() and not target.is_symlink():
            continue
        resolved = target.resolve()
        if any(kept == resolved or resolved in kept.parents for kept in resolved_keep):
            continue
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)
        else:
            target.unlink()
        removed.append(name)
    return removed


def _run_json_stage(
    deck,
    state,
    *,
    name: str,
    artifact_name: str,
    artifact_path: str,
    message: str,
    complete_message: str,
    runner,
) -> dict[str, Any]:
    progress = state.progress
    progress.start_stage(name, message=message)
    report = runner(deck)
    state.artifacts.write_json(artifact_name, artifact_path, report)
    progress.finish_stage(complete_message)
    return report or {}


EXPORT_ARTIFACT_NAMES = {
    "markdown-zip": "notes_zip",
    "markdown-toc": "notes_toc",
    "docx": "notes_docx",
    "pdf": "notes_pdf",
    "latex": "notes_latex",
}


def _register_export_artifacts(artifacts: ArtifactRegistry, export_report: dict[str, Any]) -> None:
    results = export_report.get("results")
    if not isinstance(results, list):
        return
    for result in results:
        if not isinstance(result, dict) or result.get("status") != "ok":
            continue
        path = result.get("path")
        fmt = str(result.get("format") or "")
        if not path or not fmt:
            continue
        resolved = Path(path)
        if not resolved.is_absolute():
            resolved = artifacts.output_root / resolved
        artifacts.register(EXPORT_ARTIFACT_NAMES.get(fmt, f"notes_{fmt.replace('-', '_')}"), resolved)

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
