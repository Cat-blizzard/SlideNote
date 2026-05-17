from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Any

from PIL import Image

from slidenote.figure_grounding import normalized_element_bbox, normalized_image_bbox
from slidenote.image_assets import image_metadata
from slidenote.llm_cache import utc_now_iso
from slidenote.models import Deck, ImageAsset, SlidePage, TableBlock, TextBlock, normalize_rel_path


COMPOSITE_FIGURE_MODES = {"off", "auto"}


@dataclass(frozen=True, slots=True)
class FigurePart:
    image: ImageAsset
    bbox: list[float]
    area: float
    aspect_ratio: float


@dataclass(frozen=True, slots=True)
class FigureCluster:
    parts: list[FigurePart]
    bbox: list[float]
    confidence: float
    reason: str


def enrich_deck_with_composite_figures(
    deck: Deck,
    output_root: Path,
    mode: str = "auto",
    max_composites_per_page: int = 2,
    min_children: int = 3,
) -> dict[str, Any] | None:
    """Create whole-region crops for diagrams made from many embedded images.

    PPT authors often build a flow chart from many small picture objects. Those
    objects are poor standalone note assets, so this pass crops their union from
    the page screenshot and marks the pieces as composite children.
    """

    if mode == "off":
        return None
    if mode not in COMPOSITE_FIGURE_MODES:
        raise ValueError(f"composite figure mode must be one of: {', '.join(sorted(COMPOSITE_FIGURE_MODES))}")

    figures_dir = output_root / "figures"
    figures_dir.mkdir(parents=True, exist_ok=True)
    page_records: list[dict[str, Any]] = []

    for page in deck.pages:
        record = _process_page(
            deck=deck,
            page=page,
            output_root=output_root,
            figures_dir=figures_dir,
            max_composites_per_page=max_composites_per_page,
            min_children=min_children,
        )
        page_records.append(record)

    summary = {
        "pages_total": len(deck.pages),
        "pages_with_candidates": sum(1 for record in page_records if record["candidate_parts"] > 0),
        "pages_with_composites": sum(1 for record in page_records if record["composites_created"] > 0),
        "composites_created": sum(record["composites_created"] for record in page_records),
        "child_images_absorbed": sum(record["child_images_absorbed"] for record in page_records),
    }
    return {
        "schema_version": 1,
        "generated_at": utc_now_iso(),
        "source_path": deck.source_path,
        "source_type": deck.source_type,
        "mode": mode,
        "summary": summary,
        "pages": page_records,
    }


def _process_page(
    deck: Deck,
    page: SlidePage,
    output_root: Path,
    figures_dir: Path,
    max_composites_per_page: int,
    min_children: int,
) -> dict[str, Any]:
    base_record = {
        "slide_id": page.slide_id,
        "title": page.title,
        "candidate_parts": 0,
        "clusters_considered": 0,
        "composites_created": 0,
        "child_images_absorbed": 0,
        "composites": [],
        "skipped": [],
    }
    if not page.page_screenshot:
        base_record["skipped"].append({"reason": "missing_page_screenshot"})
        return base_record

    screenshot_path = (output_root / page.page_screenshot).resolve()
    if not screenshot_path.exists():
        base_record["skipped"].append({"reason": "missing_page_screenshot_file", "path": page.page_screenshot})
        return base_record

    parts = _candidate_parts(deck, page)
    base_record["candidate_parts"] = len(parts)
    if len(parts) < min_children:
        base_record["skipped"].append({"reason": "too_few_candidate_parts", "count": len(parts)})
        return base_record

    clusters = _select_clusters(deck, page, parts, min_children=min_children)
    base_record["clusters_considered"] = len(clusters)
    if not clusters:
        base_record["skipped"].append({"reason": "no_composite_like_cluster"})
        return base_record

    used_child_ids: set[str] = set()
    next_index = _next_figure_index(page)
    try:
        with Image.open(screenshot_path) as source_image:
            width, height = source_image.width, source_image.height
            rgb = source_image.convert("RGB")
            for cluster in clusters[:max(0, max_composites_per_page)]:
                usable_parts = [part for part in cluster.parts if part.image.id not in used_child_ids]
                if len(usable_parts) < min_children:
                    continue
                cluster = FigureCluster(
                    parts=usable_parts,
                    bbox=_union_bbox([part.bbox for part in usable_parts]),
                    confidence=cluster.confidence,
                    reason=cluster.reason,
                )
                crop_bbox = _expand_bbox(cluster.bbox, margin=0.025)
                pixel_box = _pixel_box(crop_bbox, width, height)
                if pixel_box[2] <= pixel_box[0] or pixel_box[3] <= pixel_box[1]:
                    base_record["skipped"].append({"reason": "invalid_crop_box", "bbox": crop_bbox})
                    continue

                figure_id = f"s{page.slide_id}_fig{next_index}"
                next_index += 1
                crop_path = figures_dir / f"slide{page.slide_id}_composite{len(base_record['composites']) + 1}.png"
                rgb.crop(pixel_box).save(crop_path, format="PNG")
                meta = image_metadata(crop_path)
                source_ids = _source_ids_for_cluster(deck, page, cluster)
                image_asset = ImageAsset(
                    id=figure_id,
                    path=normalize_rel_path(crop_path, output_root),
                    caption=f"\u7b2c {page.slide_id} \u9875\u7ec4\u5408\u56fe {len(base_record['composites']) + 1}",
                    bbox=[float(value) for value in pixel_box],
                    source_format="cropped_png",
                    width=meta["width"],
                    height=meta["height"],
                    file_size=meta["file_size"],
                    role="composite_figure",
                    ignored=False,
                    crop_source_path=page.page_screenshot,
                    crop_bbox=crop_bbox,
                    crop_method="composite_layout",
                    confidence=cluster.confidence,
                    source_element_ids=source_ids,
                )
                page.images.append(image_asset)
                child_ids = [part.image.id for part in usable_parts]
                for part in usable_parts:
                    part.image.role = "composite_child"
                    part.image.ignored = True
                    part.image.ignore_reason = f"part_of_composite:{figure_id}"
                    used_child_ids.add(part.image.id)

                base_record["composites"].append(
                    {
                        "id": image_asset.id,
                        "path": image_asset.path,
                        "bbox": crop_bbox,
                        "pixel_bbox": [float(value) for value in pixel_box],
                        "confidence": image_asset.confidence,
                        "reason": cluster.reason,
                        "child_image_ids": child_ids,
                        "source_element_ids": source_ids,
                    }
                )
    except Exception as exc:
        base_record["skipped"].append({"reason": "composite_crop_failed", "error": str(exc)})

    base_record["composites_created"] = len(base_record["composites"])
    base_record["child_images_absorbed"] = len(used_child_ids)
    return base_record


def _candidate_parts(deck: Deck, page: SlidePage) -> list[FigurePart]:
    parts: list[FigurePart] = []
    for image in page.images:
        if image.role in {"page_image", "figure_crop", "composite_figure", "composite_child"}:
            continue
        bbox = normalized_image_bbox(deck, page, image)
        if not bbox:
            continue
        area = _area(bbox)
        if area < 0.0002 or area > 0.18:
            continue
        if _edge_decoration_like(bbox, area):
            continue
        width = max(0.0001, bbox[2] - bbox[0])
        height = max(0.0001, bbox[3] - bbox[1])
        aspect_ratio = max(width / height, height / width)
        parts.append(FigurePart(image=image, bbox=bbox, area=area, aspect_ratio=aspect_ratio))
    return parts


def _select_clusters(deck: Deck, page: SlidePage, parts: list[FigurePart], min_children: int) -> list[FigureCluster]:
    clusters: list[FigureCluster] = []
    for group in _connected_groups(parts):
        if len(group) < min_children:
            continue
        cluster = _evaluate_group(deck, page, group, min_children=min_children)
        if cluster is not None:
            clusters.append(cluster)
    return sorted(clusters, key=lambda item: (-item.confidence, item.bbox[1], item.bbox[0]))


def _connected_groups(parts: list[FigurePart]) -> list[list[FigurePart]]:
    parent = list(range(len(parts)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        root_left = find(left)
        root_right = find(right)
        if root_left != root_right:
            parent[root_right] = root_left

    for left_index, left in enumerate(parts):
        for right_index in range(left_index + 1, len(parts)):
            right = parts[right_index]
            if _parts_are_neighbors(left.bbox, right.bbox):
                union(left_index, right_index)

    groups: dict[int, list[FigurePart]] = {}
    for index, part in enumerate(parts):
        groups.setdefault(find(index), []).append(part)
    return list(groups.values())


def _parts_are_neighbors(left: list[float], right: list[float]) -> bool:
    dx = max(0.0, max(left[0] - right[2], right[0] - left[2]))
    dy = max(0.0, max(left[1] - right[3], right[1] - left[3]))
    if math.hypot(dx, dy) <= 0.09:
        return True
    horizontal_overlap = _overlap_ratio(left[0], left[2], right[0], right[2])
    vertical_overlap = _overlap_ratio(left[1], left[3], right[1], right[3])
    return (horizontal_overlap >= 0.2 and dy <= 0.14) or (vertical_overlap >= 0.2 and dx <= 0.14)


def _evaluate_group(deck: Deck, page: SlidePage, parts: list[FigurePart], min_children: int) -> FigureCluster | None:
    bbox = _union_bbox([part.bbox for part in parts])
    union_area = _area(bbox)
    if union_area < 0.04 or union_area > 0.75:
        return None
    span_width = bbox[2] - bbox[0]
    span_height = bbox[3] - bbox[1]
    if span_width < 0.18 and span_height < 0.18:
        return None

    areas = [part.area for part in parts]
    median_area = median(areas)
    max_area = max(areas)
    has_tiny_part = any(area <= 0.015 for area in areas)
    has_connector_like_part = any(part.aspect_ratio >= 2.8 for part in parts)
    nearby_text_ids = _nearby_text_table_ids(deck, page, _expand_bbox(bbox, margin=0.06))
    diagram_signal = (
        len(parts) >= 5
        or has_connector_like_part
        or (has_tiny_part and nearby_text_ids)
        or page.page_modality == "shape_diagram"
    )
    if not diagram_signal:
        return None
    if len(parts) <= 4 and median_area >= 0.045 and not has_connector_like_part and page.page_modality != "shape_diagram":
        return None
    if max_area > 0.14 and len(parts) < 5:
        return None

    density = sum(areas) / max(union_area, 0.0001)
    confidence = 0.54
    confidence += min(0.18, len(parts) * 0.025)
    if has_connector_like_part:
        confidence += 0.1
    if nearby_text_ids:
        confidence += 0.06
    if page.page_modality == "shape_diagram":
        confidence += 0.05
    if density < 0.18:
        confidence -= 0.04
    reason = "small_embedded_images_cluster"
    if has_connector_like_part:
        reason += "+connector_like_part"
    if nearby_text_ids:
        reason += "+nearby_labels"
    return FigureCluster(parts=parts, bbox=bbox, confidence=round(max(0.45, min(0.92, confidence)), 3), reason=reason)


def _source_ids_for_cluster(deck: Deck, page: SlidePage, cluster: FigureCluster) -> list[str]:
    ids: list[str] = []
    seen: set[str] = set()
    for part in cluster.parts:
        if part.image.id not in seen:
            ids.append(part.image.id)
            seen.add(part.image.id)
    for element_id in _nearby_text_table_ids(deck, page, _expand_bbox(cluster.bbox, margin=0.06)):
        if element_id not in seen:
            ids.append(element_id)
            seen.add(element_id)
    return ids


def _nearby_text_table_ids(deck: Deck, page: SlidePage, bbox: list[float]) -> list[str]:
    ids: list[str] = []
    for element in [*page.text_blocks, *page.tables]:
        element_bbox = _element_bbox(deck, page, element)
        if not element_bbox:
            continue
        if _intersection_area(bbox, element_bbox) > 0 or _center_inside(element_bbox, bbox):
            ids.append(element.id)
    return ids


def _element_bbox(deck: Deck, page: SlidePage, element: TextBlock | TableBlock) -> list[float] | None:
    return normalized_element_bbox(deck, page, element)


def _next_figure_index(page: SlidePage) -> int:
    next_index = 1
    prefix = f"s{page.slide_id}_fig"
    for image in page.images:
        if not image.id.startswith(prefix):
            continue
        suffix = image.id[len(prefix) :]
        if suffix.isdigit():
            next_index = max(next_index, int(suffix) + 1)
    return next_index


def _union_bbox(boxes: list[list[float]]) -> list[float]:
    return [
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    ]


def _expand_bbox(bbox: list[float], margin: float) -> list[float]:
    return [
        round(max(0.0, bbox[0] - margin), 4),
        round(max(0.0, bbox[1] - margin), 4),
        round(min(1.0, bbox[2] + margin), 4),
        round(min(1.0, bbox[3] + margin), 4),
    ]


def _pixel_box(bbox: list[float], width: int, height: int) -> tuple[int, int, int, int]:
    x1, y1, x2, y2 = bbox
    return (
        max(0, min(width - 1, int(round(x1 * width)))),
        max(0, min(height - 1, int(round(y1 * height)))),
        max(1, min(width, int(round(x2 * width)))),
        max(1, min(height, int(round(y2 * height)))),
    )


def _area(bbox: list[float]) -> float:
    return max(0.0, bbox[2] - bbox[0]) * max(0.0, bbox[3] - bbox[1])


def _intersection_area(left: list[float], right: list[float]) -> float:
    x1 = max(left[0], right[0])
    y1 = max(left[1], right[1])
    x2 = min(left[2], right[2])
    y2 = min(left[3], right[3])
    if x2 <= x1 or y2 <= y1:
        return 0.0
    return (x2 - x1) * (y2 - y1)


def _overlap_ratio(a1: float, a2: float, b1: float, b2: float) -> float:
    overlap = max(0.0, min(a2, b2) - max(a1, b1))
    denom = max(0.0001, min(a2 - a1, b2 - b1))
    return overlap / denom


def _center_inside(inner: list[float], outer: list[float]) -> bool:
    cx = (inner[0] + inner[2]) / 2
    cy = (inner[1] + inner[3]) / 2
    return outer[0] <= cx <= outer[2] and outer[1] <= cy <= outer[3]


def _edge_decoration_like(bbox: list[float], area: float) -> bool:
    if area >= 0.012:
        return False
    x1, y1, x2, y2 = bbox
    near_edge = x1 <= 0.025 or y1 <= 0.025 or x2 >= 0.975 or y2 >= 0.975
    return near_edge
