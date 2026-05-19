from __future__ import annotations

from pathlib import Path
from typing import Any

from slidenote.pipeline import ArtifactRegistry, BuildContext, FunctionStage, StageResult, run_stage


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
