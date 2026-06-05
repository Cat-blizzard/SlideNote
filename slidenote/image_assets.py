from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image


def image_metadata(path: Path) -> dict[str, Any]:
    meta: dict[str, Any] = {"file_size": path.stat().st_size if path.exists() else None}
    try:
        with Image.open(path) as image:
            meta["width"] = image.width
            meta["height"] = image.height
    except Exception:
        meta["width"] = None
        meta["height"] = None
    role, ignored, reason = classify_image_asset(meta)
    meta["role"] = role
    meta["ignored"] = ignored
    meta["ignore_reason"] = reason
    return meta


def classify_image_asset(meta: dict[str, Any]) -> tuple[str, bool, str | None]:
    width = meta.get("width")
    height = meta.get("height")
    file_size = meta.get("file_size")
    if not isinstance(width, int) or not isinstance(height, int) or width <= 0 or height <= 0:
        return "unknown", False, None

    area = width * height
    min_dim = min(width, height)
    max_dim = max(width, height)
    aspect_ratio = max_dim / max(1, min_dim)

    if isinstance(file_size, int) and file_size < 512:
        return "decorative", True, "tiny_file"
    if area < 10_000:
        return "decorative", True, "tiny_area"
    if min_dim < 24:
        return "decorative", True, "tiny_dimension"
    if aspect_ratio >= 8 and area < 150_000:
        return "decorative", True, "thin_decoration"
    return "content", False, None


def refine_image_role_for_placement(
    role: str,
    ignored: bool,
    reason: str | None,
    placement_area_ratio: float | None,
    near_page_edge: bool,
) -> tuple[str, bool, str | None]:
    if ignored:
        return role, ignored, reason
    if placement_area_ratio is None:
        return role, ignored, reason
    if placement_area_ratio < 0.01:
        return "decorative", True, "tiny_placement"
    if near_page_edge and placement_area_ratio < 0.018:
        return "decorative", True, "edge_decoration"
    return role, ignored, reason
