from __future__ import annotations

from typing import Any

from slidenote.models import Deck


def crop_quality_summary(deck: Deck) -> dict[str, Any]:
    counts: dict[str, int] = {}
    warnings = 0
    for page in deck.pages:
        for image in page.images:
            if not image.crop_quality:
                continue
            counts[image.crop_quality] = counts.get(image.crop_quality, 0) + 1
            warnings += len(image.crop_warnings)
    return {
        "crop_quality": counts,
        "crop_warnings": warnings,
    }


__all__ = ["crop_quality_summary"]
