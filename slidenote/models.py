from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class TextBlock:
    id: str
    type: str
    content: str
    bbox: list[float] | None = None


@dataclass(slots=True)
class TableBlock:
    id: str
    rows: list[list[str]]
    bbox: list[float] | None = None
    table_summary: str | None = None
    table_conclusion: str | None = None
    key_rows: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class ImageAsset:
    id: str
    path: str
    caption: str | None = None
    ocr_text: str | None = None
    ocr_status: str | None = None
    visual_summary: str | None = None
    visual_status: str | None = None
    bbox: list[float] | None = None
    source_format: str | None = None
    width: int | None = None
    height: int | None = None
    file_size: int | None = None
    role: str | None = None
    ignored: bool = False
    ignore_reason: str | None = None
    crop_source_path: str | None = None
    crop_bbox: list[float] | None = None
    crop_method: str | None = None
    crop_quality: str | None = None
    crop_warnings: list[str] = field(default_factory=list)
    confidence: float | None = None
    importance_score: float | None = None
    importance_rank: int | None = None
    importance_reason: str | None = None
    layout_order: float | None = None
    source_element_ids: list[str] = field(default_factory=list)
    anchor_element_ids: list[str] = field(default_factory=list)
    anchor_reason: str | None = None
    grounding_confidence: float | None = None
    figure_explanation: str | None = None
    figure_explanation_status: str | None = None
    figure_audit_status: str | None = None


@dataclass(slots=True)
class SlidePage:
    slide_id: int
    page_width: float | None = None
    page_height: float | None = None
    title: str | None = None
    text_blocks: list[TextBlock] = field(default_factory=list)
    tables: list[TableBlock] = field(default_factory=list)
    images: list[ImageAsset] = field(default_factory=list)
    page_screenshot: str | None = None
    page_ocr_text: str | None = None
    page_ocr_status: str | None = None
    page_visual_summary: str | None = None
    page_visual_status: str | None = None
    notes: str | None = None
    warnings: list[str] = field(default_factory=list)
    page_modality: str | None = None
    modality_confidence: float | None = None
    modality_reasons: list[str] = field(default_factory=list)
    processing_hints: list[str] = field(default_factory=list)
    semantic_blocks: list[dict[str, Any]] = field(default_factory=list)
    semantic_groups: list[dict[str, Any]] = field(default_factory=list)
    semantic_relations: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class Deck:
    source_path: str
    source_type: str
    pages: list[SlidePage]
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalize_rel_path(path: Path, root: Path) -> str:
    try:
        rel = path.relative_to(root)
    except ValueError:
        rel = path
    return rel.as_posix()
