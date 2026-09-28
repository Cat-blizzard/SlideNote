"""Bounding-box conventions shared by the parsing and understanding stages.

Raw ``bbox`` values on ``TextBlock`` / ``TableBlock`` / ``ImageAsset`` are stored in
the parser's native space:

- ``pptx``: ``[x, y, width, height]`` in EMU.
- ``pdf`` and everything else: ``[x1, y1, x2, y2]`` in points.

Assets cropped from a page screenshot (``figure_crop``, ``composite_figure``) keep the
pixel rectangle of the screenshot in ``bbox`` and the page-normalized rectangle in
``crop_bbox``. Their ``bbox`` must never be interpreted as page coordinates; use
:func:`asset_source_bbox` to pick the right field.

Normalized boxes are ``[x1, y1, x2, y2]`` page fractions in ``0..1``.
"""

from __future__ import annotations

from typing import Any

from slidenote.models import ImageAsset, SlidePage


def coerce_bbox(value: Any) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        return [float(part) for part in value]
    except (TypeError, ValueError):
        return None


def looks_normalized(bbox: list[float]) -> bool:
    return len(bbox) == 4 and all(-0.001 <= float(value) <= 1.001 for value in bbox)


def clamp_bbox(bbox: list[float], precision: int = 4) -> list[float]:
    x1, y1, x2, y2 = [max(0.0, min(1.0, float(value))) for value in bbox]
    if x2 < x1:
        x1, x2 = x2, x1
    if y2 < y1:
        y1, y2 = y2, y1
    return [round(x1, precision), round(y1, precision), round(x2, precision), round(y2, precision)]


def normalize_bbox(
    source_type: str | None,
    bbox: Any,
    page_width: float | None,
    page_height: float | None,
    *,
    precision: int = 4,
) -> list[float] | None:
    """Convert a native-space bbox (see module docstring) to a normalized xyxy box."""
    values = coerce_bbox(bbox)
    if values is None:
        return None
    if looks_normalized(values):
        return clamp_bbox(values, precision)
    try:
        width = float(page_width or 0.0)
        height = float(page_height or 0.0)
    except (TypeError, ValueError):
        return None
    if width <= 0 or height <= 0:
        return None
    x1, y1, third, fourth = values
    if source_type == "pptx":
        x2, y2 = x1 + third, y1 + fourth
    else:
        x2, y2 = third, fourth
    return clamp_bbox([x1 / width, y1 / height, x2 / width, y2 / height], precision)


def bbox_format(source_type: str | None, bbox: list[float] | None) -> str | None:
    if not bbox:
        return None
    if looks_normalized(bbox):
        return "normalized_xyxy"
    if source_type == "pptx":
        return "source_xywh"
    return "source_xyxy"


def asset_source_bbox(image: ImageAsset) -> list[float] | None:
    """Return the bbox of an image asset in page space (normalized or native).

    Screenshot crops store pixel coordinates in ``bbox``; only their ``crop_bbox``
    describes the position on the page.
    """
    if image.crop_bbox:
        return coerce_bbox(image.crop_bbox)
    if image.crop_source_path:
        return None
    return coerce_bbox(image.bbox)


def normalize_page_bbox(source_type: str | None, bbox: Any, page: SlidePage, *, precision: int = 4) -> list[float] | None:
    return normalize_bbox(source_type, bbox, page.page_width, page.page_height, precision=precision)


def normalize_asset_bbox(source_type: str | None, page: SlidePage, image: ImageAsset, *, precision: int = 4) -> list[float] | None:
    return normalize_page_bbox(source_type, asset_source_bbox(image), page, precision=precision)


PAGE_LIKE_AREA_RATIO = 0.85
PAGE_EDGE_MARGIN = 0.08


def placement_metrics(
    source_type: str | None,
    bbox: Any,
    page_width: float | None,
    page_height: float | None,
) -> tuple[float | None, bool, bool]:
    """Return ``(area_ratio, near_page_edge, page_like)`` for a native-space bbox."""
    normalized = normalize_bbox(source_type, bbox, page_width, page_height, precision=6)
    if normalized is None:
        return None, False, False
    x1, y1, x2, y2 = normalized
    area_ratio = (x2 - x1) * (y2 - y1)
    near_edge = x1 <= PAGE_EDGE_MARGIN or y1 <= PAGE_EDGE_MARGIN or x2 >= 1 - PAGE_EDGE_MARGIN or y2 >= 1 - PAGE_EDGE_MARGIN
    return area_ratio, near_edge, area_ratio >= PAGE_LIKE_AREA_RATIO
