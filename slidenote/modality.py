from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from slidenote.llm_cache import utc_now_iso
from slidenote.models import Deck, SlidePage

PAGE_MODALITIES = {"native_text", "mixed", "image_only", "shape_diagram", "decorative"}
OVERRIDE_MODALITIES = PAGE_MODALITIES | {"unknown"}


@dataclass(frozen=True, slots=True)
class PageModalityResult:
    slide_id: int
    modality: str
    confidence: float
    reasons: list[str]
    processing_hints: list[str]
    stats: dict[str, Any]


def enrich_deck_with_modalities(deck: Deck) -> dict[str, Any]:
    results = [classify_page_modality(page) for page in deck.pages]
    for page, result in zip(deck.pages, results):
        page.page_modality = result.modality
        page.modality_confidence = result.confidence
        page.modality_reasons = list(result.reasons)
        page.processing_hints = list(result.processing_hints)
    return build_modality_report(deck, results)


def apply_modality_overrides(deck: Deck, report: dict[str, Any], manifest_path: Path | str) -> dict[str, Any]:
    """Apply reviewer page labels after local classification, before visual processing.

    The GUI's version 1 manifest stores overrides under string slide IDs. Invalid or
    stale entries are reported and ignored so a handwritten manifest cannot break a
    build. A source hash, when provided, takes precedence over the upload path.
    """
    path = Path(manifest_path)
    if not path.is_file():
        return report

    warnings: list[str] = []
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        warnings.append(f"Could not read modality override manifest: {exc}")
        return _record_override_result(report, path, [], warnings)

    if not isinstance(manifest, dict) or type(manifest.get("schema_version")) is not int or manifest["schema_version"] != 1:
        warnings.append("Modality override manifest must be a version 1 JSON object.")
        return _record_override_result(report, path, [], warnings)
    if not _manifest_matches_source(manifest, deck.source_path, warnings):
        return _record_override_result(report, path, [], warnings)

    overrides = manifest.get("pages")
    if not isinstance(overrides, dict):
        warnings.append("Modality override manifest.pages must be an object keyed by slide ID.")
        return _record_override_result(report, path, [], warnings)

    pages_by_id = {page.slide_id: page for page in deck.pages}
    report_pages = {
        item.get("slide_id"): item
        for item in report.get("pages", [])
        if isinstance(item, dict)
    }
    applied: list[dict[str, Any]] = []
    for raw_slide_id, entry in overrides.items():
        if not isinstance(raw_slide_id, str) or not raw_slide_id.isdecimal() or int(raw_slide_id) < 1 or str(int(raw_slide_id)) != raw_slide_id:
            warnings.append(f"Invalid modality override slide ID: {raw_slide_id!r}.")
            continue
        slide_id = int(raw_slide_id)
        page = pages_by_id.get(slide_id)
        report_page = report_pages.get(slide_id)
        if page is None or report_page is None:
            warnings.append(f"Modality override slide {slide_id} is not in this deck.")
            continue
        if not isinstance(entry, dict) or not isinstance(entry.get("modality"), str) or entry["modality"] not in OVERRIDE_MODALITIES:
            warnings.append(f"Invalid modality override for slide {slide_id}.")
            continue

        modality = entry["modality"]
        previous_modality = page.page_modality
        note = entry.get("note") if isinstance(entry.get("note"), str) else ""
        updated_at = entry.get("updated_at") if isinstance(entry.get("updated_at"), str) else None
        hints = list(page.processing_hints) if modality == "unknown" else _override_processing_hints(page, modality)
        confidence = 0.0 if modality == "unknown" else 1.0
        page.page_modality = modality
        page.modality_confidence = confidence
        page.modality_reasons = ["manual_override"]
        page.processing_hints = hints
        report_page.update(
            modality=modality,
            confidence=confidence,
            reasons=list(page.modality_reasons),
            processing_hints=list(hints),
            classifier_modality=previous_modality,
            manual_override={"note": note, "updated_at": updated_at},
        )
        applied.append({"slide_id": slide_id, "modality": modality, "previous_modality": previous_modality})

    if applied:
        report["summary"] = _modality_summary(report["pages"])
    return _record_override_result(report, path, applied, warnings)


def classify_page_modality(page: SlidePage) -> PageModalityResult:
    stats = _page_stats(page)
    text_len = int(stats["text_chars"])
    content_images = int(stats["content_images"])
    page_images = int(stats["page_images"])
    tables = int(stats["tables"])
    has_screenshot = bool(stats["has_screenshot"])
    warnings = int(stats["warnings"])
    reasons: list[str] = []
    hints: list[str] = []

    if content_images:
        modality = "mixed"
        confidence = 0.82 if text_len or tables else 0.72
        reasons.append("has_content_images")
        hints.extend(["use_embedded_images", "vision_large_images"])
        if text_len or tables:
            reasons.append("has_extracted_text_or_tables")
            hints.append("use_extracted_text")
        if text_len < 80 and has_screenshot:
            hints.append("ocr_page_screenshot")
    elif page_images or (has_screenshot and text_len == 0 and not tables):
        modality = "image_only"
        confidence = 0.9 if page_images or warnings else 0.72
        reasons.append("full_page_image_or_low_text_screenshot")
        hints.extend(["ocr_page_screenshot", "crop_figures_from_screenshot", "vision_page_screenshot"])
    elif has_screenshot and (tables or 0 < text_len < 500 or warnings):
        modality = "shape_diagram"
        confidence = 0.68 if text_len else 0.58
        reasons.append("screenshot_with_limited_extracted_objects")
        hints.extend(["use_extracted_text", "crop_figures_from_screenshot", "vision_page_screenshot"])
    elif text_len >= 80 or tables:
        modality = "native_text"
        confidence = 0.86
        reasons.append("text_rich_or_table_extracted")
        hints.append("use_extracted_text")
    else:
        modality = "decorative"
        confidence = 0.55
        reasons.append("little_or_no_learning_content_detected")
        hints.append("low_priority")

    return PageModalityResult(
        slide_id=page.slide_id,
        modality=modality,
        confidence=confidence,
        reasons=reasons,
        processing_hints=_dedupe(hints),
        stats=stats,
    )


def build_modality_report(deck: Deck, results: list[PageModalityResult]) -> dict[str, Any]:
    pages = [
        {
            "slide_id": result.slide_id,
            "modality": result.modality,
            "confidence": result.confidence,
            "reasons": result.reasons,
            "processing_hints": result.processing_hints,
            "stats": result.stats,
        }
        for result in results
    ]
    return {
        "schema_version": 1,
        "generated_at": utc_now_iso(),
        "source_path": deck.source_path,
        "source_type": deck.source_type,
        "summary": _modality_summary(pages),
        "pages": pages,
    }


def page_has_hint(page: SlidePage, hint: str) -> bool:
    return hint in (page.processing_hints or [])


def page_has_manual_modality(page: SlidePage) -> bool:
    return page.page_modality != "unknown" and "manual_override" in (page.modality_reasons or [])


def _override_processing_hints(page: SlidePage, modality: str) -> list[str]:
    has_text = bool(page.text_blocks or page.tables)
    has_images = any(not image.ignored and image.role != "page_image" for image in page.images)
    has_ocr_images = any(not image.ignored for image in page.images)
    has_screenshot = bool(page.page_screenshot)
    if modality == "native_text":
        return ["use_extracted_text"]
    if modality == "mixed":
        hints = ["use_extracted_text"] if has_text else []
        if has_images:
            hints.extend(["use_embedded_images", "vision_large_images"])
        elif has_screenshot:
            hints.append("vision_page_screenshot")
        if has_screenshot and sum(len(block.content.strip()) for block in page.text_blocks) < 80:
            hints.append("ocr_page_screenshot")
        return hints
    if modality == "image_only":
        hints = ["ocr_page_screenshot"] if has_screenshot or has_ocr_images else []
        if has_screenshot:
            hints.extend(["crop_figures_from_screenshot", "vision_page_screenshot"])
        if has_images:
            hints.extend(["use_embedded_images", "vision_large_images"])
        return hints
    if modality == "shape_diagram":
        hints = ["use_extracted_text"] if has_text else []
        if has_screenshot:
            hints.extend(["crop_figures_from_screenshot", "vision_page_screenshot"])
        if has_images:
            hints.extend(["use_embedded_images", "vision_large_images"])
        return hints
    if modality == "decorative":
        return ["low_priority"]
    return []


def _modality_summary(pages: list[dict[str, Any]]) -> dict[str, Any]:
    counts = Counter(str(page["modality"]) for page in pages)
    return {
        "pages_total": len(pages),
        "modalities": dict(sorted(counts.items())),
        "image_driven_pages": sum(1 for page in pages if page["modality"] in {"image_only", "shape_diagram"}),
        "embedded_image_pages": sum(1 for page in pages if "use_embedded_images" in page["processing_hints"]),
        "ocr_recommended_pages": sum(1 for page in pages if "ocr_page_screenshot" in page["processing_hints"]),
        "figure_crop_recommended_pages": sum(1 for page in pages if "crop_figures_from_screenshot" in page["processing_hints"]),
    }


def _record_override_result(
    report: dict[str, Any], path: Path, applied: list[dict[str, Any]], warnings: list[str]
) -> dict[str, Any]:
    report.setdefault("summary", {})["override_pages"] = len(applied)
    report["overrides"] = {
        "manifest_path": str(path),
        "applied": applied,
        "warnings": warnings,
    }
    return report


def _manifest_matches_source(manifest: dict[str, Any], source_path: str, warnings: list[str]) -> bool:
    source_hash = manifest.get("source_sha256")
    if source_hash is not None:
        if not isinstance(source_hash, str) or len(source_hash) != 64 or any(char not in "0123456789abcdefABCDEF" for char in source_hash):
            warnings.append("Modality override source_sha256 must be a 64-character hex digest.")
            return False
        try:
            digest = hashlib.sha256()
            with Path(source_path).open("rb") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(chunk)
        except OSError as exc:
            warnings.append(f"Could not verify modality override source: {exc}")
            return False
        if digest.hexdigest() != source_hash.lower():
            warnings.append("Modality override source hash does not match this deck.")
            return False
        return True

    manifest_source_path = manifest.get("source_path")
    if manifest_source_path is not None:
        if not isinstance(manifest_source_path, str) or not manifest_source_path:
            warnings.append("Modality override source_path must be a non-empty string.")
            return False
        expected = os.path.normcase(os.path.normpath(str(Path(manifest_source_path).resolve())))
        actual = os.path.normcase(os.path.normpath(str(Path(source_path).resolve())))
        if expected != actual:
            warnings.append("Modality override source path does not match this deck.")
            return False
    return True


def _page_stats(page: SlidePage) -> dict[str, Any]:
    text_chars = sum(len(block.content.strip()) for block in page.text_blocks)
    content_images = sum(1 for image in page.images if not image.ignored and image.role != "page_image")
    page_images = sum(1 for image in page.images if image.role == "page_image")
    decorative_images = sum(1 for image in page.images if image.ignored and image.role != "page_image")
    return {
        "text_blocks": len(page.text_blocks),
        "text_chars": text_chars,
        "tables": len(page.tables),
        "images_total": len(page.images),
        "content_images": content_images,
        "page_images": page_images,
        "decorative_images": decorative_images,
        "has_screenshot": bool(page.page_screenshot),
        "warnings": len(page.warnings),
        "title": page.title,
    }


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            result.append(value)
            seen.add(value)
    return result
