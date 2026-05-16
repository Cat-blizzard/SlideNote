from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any

from slidenote.llm_cache import utc_now_iso
from slidenote.models import Deck, SlidePage

PAGE_MODALITIES = {"native_text", "mixed", "image_only", "shape_diagram", "decorative"}


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
    counts = Counter(result.modality for result in results)
    return {
        "schema_version": 1,
        "generated_at": utc_now_iso(),
        "source_path": deck.source_path,
        "source_type": deck.source_type,
        "summary": {
            "pages_total": len(results),
            "modalities": dict(sorted(counts.items())),
            "image_driven_pages": sum(1 for result in results if result.modality in {"image_only", "shape_diagram"}),
            "embedded_image_pages": sum(1 for result in results if "use_embedded_images" in result.processing_hints),
            "ocr_recommended_pages": sum(1 for result in results if "ocr_page_screenshot" in result.processing_hints),
            "figure_crop_recommended_pages": sum(
                1 for result in results if "crop_figures_from_screenshot" in result.processing_hints
            ),
        },
        "pages": [
            {
                "slide_id": result.slide_id,
                "modality": result.modality,
                "confidence": result.confidence,
                "reasons": result.reasons,
                "processing_hints": result.processing_hints,
                "stats": result.stats,
            }
            for result in results
        ],
    }


def page_has_hint(page: SlidePage, hint: str) -> bool:
    return hint in (page.processing_hints or [])


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
