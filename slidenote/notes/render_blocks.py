"""Render images and styled text blocks as Markdown."""

from __future__ import annotations

import html
import re

from slidenote.models import ImageAsset, SlidePage, TextBlock
from .assets import _asset_display_path
from .sources import _image_source_ids, _source_marker


_CSS_HEX_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


def _render_image(page: SlidePage, image: ImageAsset, asset_map: dict[str, str], source_display: str) -> list[str]:
    caption = image.caption or f"\u7b2c {page.slide_id} \u9875\u56fe\u7247"
    lines = [
        f"{caption}\u3002",
        _source_marker(page.slide_id, _image_source_ids(image), source_display),
        "",
    ]
    explanation = image.figure_explanation or image.visual_summary
    if explanation:
        label = "\u56fe\u793a\u8bf4\u660e" if image.figure_explanation else "\u56fe\u7247\u89c6\u89c9\u89e3\u6790"
        lines.append(f"{label}\uff1a{_ensure_sentence(explanation)}")
    if _should_render_image_ocr(image, explanation):
        if explanation:
            lines.append("")
        lines.append("\u56fe\u7247 OCR \u6587\u5b57\uff1a")
        lines.extend(_quote_multiline(image.ocr_text))
    if explanation or _should_render_image_ocr(image, explanation):
        lines.append("")
    lines.append(f"![{caption}]({_asset_display_path(image.path, asset_map)})")
    return lines


def _should_render_image_ocr(image: ImageAsset, explanation: str | None) -> bool:
    if not image.ocr_text:
        return False
    if image.figure_explanation_status == "ocr_text":
        return False
    return not bool(explanation)


def _ensure_sentence(text: str) -> str:
    value = " ".join(text.split()).strip()
    if value and value[-1] not in "\u3002.!!\uff1f?\uff1a:":
        value += "\u3002"
    return value


def _quote_multiline(text: str) -> list[str]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return [f"> {line}" for line in lines]


def _styled_block_text(block: TextBlock) -> str | None:
    if not block.style_runs:
        return None
    pieces: list[str] = []
    has_visible_style = False
    for run in block.style_runs:
        text = str(run.get("text") or "")
        if not text:
            continue
        escaped = html.escape(text).replace("\n", "<br>")
        css: list[str] = []
        color = _safe_css_color(run.get("color"))
        if color:
            css.append(f"color:{color}")
        if run.get("bold") is True:
            css.append("font-weight:700")
        if run.get("italic") is True:
            css.append("font-style:italic")
        if css:
            has_visible_style = True
            pieces.append(f'<span style="{";".join(css)}">{escaped}</span>')
        else:
            pieces.append(escaped)
    rendered = "".join(pieces).strip()
    return rendered if has_visible_style and rendered else None


def _safe_css_color(value: object) -> str | None:
    color = str(value or "").strip()
    return color.upper() if _CSS_HEX_COLOR_RE.fullmatch(color) else None
