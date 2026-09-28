"""Detect cover/agenda front matter and generic headings."""

from __future__ import annotations

import re

from slidenote.models import SlidePage
from slidenote.utils import looks_like_outline_page
from .contexts import NoteContext
from .sources import _page_source_ids, _source_marker


def _clean_heading_text(value: str) -> str:
    text = re.sub(r"<!--.*?-->", "", value).strip()
    text = re.sub(r"^\u8bfe\u7a0b\u7b14\u8bb0[\uff1a:\s-]*", "", text).strip()
    text = re.sub(r"^\s*[\uff08(]?\s*(?:\d+|[\u4e00\u4e8c\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d\u5341]+)\s*[)\uff09.\u3001]\s*", "", text).strip()
    return text.strip("\uff1a: -")


def _normalize_title_key(value: str) -> str:
    return re.sub(r"[\s:\uff1a,\uff0c.\u3002;\uff1b\u3001\-_\u2014\uff08\uff09()\u300a\u300b<>]+", "", _clean_heading_text(value)).lower()


def _is_generic_heading_text(value: str) -> bool:
    normalized = _normalize_title_key(value)
    return normalized in {
        "",
        "\u8bfe\u7a0b\u7b14\u8bb0",
        "\u7b14\u8bb0",
        "\u8bb2\u4e49",
        "\u751f\u6210\u4fe1\u606f",
        "\u89e3\u6790\u63d0\u9192",
        "\u76ee\u5f55",
        "contents",
        "overview",
    }


def _is_frontmatter_heading(title: str, context: NoteContext) -> bool:
    normalized = _normalize_title_key(title)
    if normalized in {"\u76ee\u5f55", "contents", "\u8bfe\u7a0b\u6982\u89c8", "overview"}:
        return True
    if len(context.pages) <= 2 and all(_normalize_title_key(page.title or "") in {"\u76ee\u5f55", "contents"} for page in context.pages):
        return True
    return False


def _leading_frontmatter_slide_ids(pages: list[SlidePage]) -> set[int]:
    slide_ids: set[int] = set()
    for index, page in enumerate(pages):
        if not _is_frontmatter_page(page, index):
            break
        slide_ids.add(page.slide_id)
    return slide_ids


def _is_frontmatter_page(page: SlidePage, index: int) -> bool:
    title = page.title or ""
    normalized_title = _normalize_title_key(title)
    if normalized_title in {"\u76ee\u5f55", "contents", "outline", "agenda"}:
        return True
    text = "\n".join([title, *(block.content for block in page.text_blocks)])
    if "\u76ee\u5f55" in text or "Contents" in text:
        return True
    if index == 0 and _looks_like_cover_page(text):
        return True
    return index <= 3 and looks_like_outline_page(text)


def _looks_like_cover_page(text: str) -> bool:
    normalized = _normalize_title_key(text)
    cover_markers = {
        "\u8bb2\u5e08",
        "\u6559\u5e08",
        "\u6559\u6388",
        "\u8054\u7cfb\u90ae\u7bb1",
        "\u90ae\u7bb1",
        "\u4e3b\u9875",
        "email",
        "homepage",
        "http",
        "www",
    }
    return any(marker in normalized for marker in cover_markers)


def _looks_like_frontmatter_text(text: str) -> bool:
    normalized = _normalize_title_key(text)
    markers = {
        "\u76ee\u5f55",
        "\u8bfe\u7a0b\u76ee\u5f55",
        "\u672c\u7ae0\u76ee\u5f55",
        "\u4e3b\u6807\u9898",
        "\u526f\u6807\u9898",
        "\u8bb2\u5e08",
        "\u6559\u6388",
        "\u8054\u7cfb\u90ae\u7bb1",
        "\u4e3b\u9875",
        "contents",
        "overview",
    }
    return any(marker in normalized for marker in markers)


def _frontmatter_source_markers(pages: list[SlidePage]) -> str:
    markers = [_source_marker(page.slide_id, _page_source_ids(page), "hidden") for page in pages]
    return "\n".join(marker for marker in markers if marker)
