from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image

from slidenote.llm_cache import utc_now_iso
from slidenote.models import Deck, ImageAsset, SlidePage


def rank_deck_images(deck: Deck, output_root: Path, mode: str = "local", stage: str = "pre_vision") -> dict[str, Any] | None:
    if mode == "off":
        return None
    if mode != "local":
        raise ValueError("image ranking mode must be one of: off, local")

    page_entries: list[dict[str, Any]] = []
    image_records: list[dict[str, Any]] = []
    for page in deck.pages:
        records = [_score_image(page, image, output_root) for image in page.images]
        ranked = sorted(
            [record for record in records if not record["ignored"]],
            key=lambda record: (-record["importance_score"], record["image_id"]),
        )
        rank_by_id = {record["image_id"]: index + 1 for index, record in enumerate(ranked)}
        for record in records:
            image = next((item for item in page.images if item.id == record["image_id"]), None)
            if image is None:
                continue
            rank = rank_by_id.get(record["image_id"])
            image.importance_score = record["importance_score"]
            image.importance_rank = rank
            image.importance_reason = "; ".join(record["reasons"])
            record["importance_rank"] = rank
            image_records.append(record)
        page_entries.append(
            {
                "slide_id": page.slide_id,
                "title": page.title,
                "page_modality": page.page_modality,
                "images_total": len(records),
                "ranked_images": sorted(records, key=lambda record: (record.get("importance_rank") or 9999, -record["importance_score"])),
            }
        )

    return {
        "schema_version": 1,
        "generated_at": utc_now_iso(),
        "source_path": deck.source_path,
        "source_type": deck.source_type,
        "mode": mode,
        "stage": stage,
        "summary": {
            "pages_total": len(deck.pages),
            "images_total": len(image_records),
            "ranked_images": sum(1 for record in image_records if record.get("importance_rank") is not None),
            "high_value_images": sum(1 for record in image_records if record["importance_score"] >= 0.65 and not record["ignored"]),
            "ignored_images": sum(1 for record in image_records if record["ignored"]),
        },
        "pages": page_entries,
    }


def sorted_images_by_importance(images: list[ImageAsset]) -> list[ImageAsset]:
    return sorted(
        images,
        key=lambda image: (
            image.ignored,
            image.importance_rank if image.importance_rank is not None else 9999,
            -(image.importance_score or 0.0),
            image.id,
        ),
    )


def _score_image(page: SlidePage, image: ImageAsset, output_root: Path) -> dict[str, Any]:
    reasons: list[str] = []
    if image.ignored:
        reasons.append(f"ignored:{image.ignore_reason or 'decorative'}")
        return _record(page, image, 0.0, reasons, output_root)

    score = 0.35
    reasons.append("content_candidate")

    role = image.role or "content"
    if role == "figure_crop":
        score += 0.35
        reasons.append("figure_crop")
    elif role == "composite_figure":
        score += 0.38
        reasons.append("composite_figure")
    elif role == "page_image":
        score -= 0.22
        reasons.append("page_image_penalty")
    elif role == "content":
        score += 0.08
        reasons.append("embedded_content")

    width, height = _image_dimensions(image, output_root)
    if width and height:
        area = width * height
        min_dim = min(width, height)
        max_dim = max(width, height)
        aspect_ratio = max_dim / max(1, min_dim)
        if area >= 500_000:
            score += 0.18
            reasons.append("large_visual_area")
        elif area >= 120_000:
            score += 0.12
            reasons.append("medium_visual_area")
        elif area >= 40_000:
            score += 0.04
            reasons.append("small_but_usable_area")
        else:
            score -= 0.18
            reasons.append("low_area_penalty")
        if min_dim < 80:
            score -= 0.16
            reasons.append("thin_or_tiny_dimension_penalty")
        if aspect_ratio >= 6:
            score -= 0.16
            reasons.append("extreme_aspect_ratio_penalty")
        elif aspect_ratio >= 3.5:
            score -= 0.06
            reasons.append("wide_or_tall_aspect_ratio")

    if image.confidence is not None and role == "figure_crop":
        delta = max(-0.08, min(0.12, (image.confidence - 0.45) * 0.28))
        score += delta
        reasons.append(f"crop_confidence:{image.confidence:.2f}")
    if image.visual_summary:
        score += 0.12
        reasons.append("has_visual_summary")
    if image.ocr_text:
        score += 0.08
        reasons.append("has_ocr_text")
    if image.caption and not image.caption.startswith("第 "):
        score += 0.04
        reasons.append("has_caption")
    if page.page_modality == "shape_diagram" and role == "figure_crop":
        score += 0.08
        reasons.append("shape_diagram_crop")
    elif page.page_modality == "image_only":
        score += 0.05
        reasons.append("image_only_page")
    elif page.page_modality == "mixed":
        score += 0.03
        reasons.append("mixed_page")

    return _record(page, image, max(0.0, min(1.0, score)), reasons, output_root)


def _record(page: SlidePage, image: ImageAsset, score: float, reasons: list[str], output_root: Path) -> dict[str, Any]:
    width, height = _image_dimensions(image, output_root)
    return {
        "slide_id": page.slide_id,
        "image_id": image.id,
        "path": image.path,
        "role": image.role,
        "ignored": image.ignored,
        "ignore_reason": image.ignore_reason,
        "width": width,
        "height": height,
        "importance_score": round(score, 3),
        "importance_rank": None,
        "reasons": reasons,
    }


def _image_dimensions(image: ImageAsset, output_root: Path) -> tuple[int | None, int | None]:
    if isinstance(image.width, int) and isinstance(image.height, int):
        return image.width, image.height
    path = output_root / image.path
    try:
        with Image.open(path) as opened:
            return opened.width, opened.height
    except Exception:
        return image.width, image.height
