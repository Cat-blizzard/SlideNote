"""Clean up raw LLM Markdown before composition."""

from __future__ import annotations

import re

from .sources import SOURCE_COMMENT_PREFIX, _source_marker


def _postprocess_llm_markdown(markdown: str, source_display: str) -> str:
    text = _clean_llm_markdown(markdown)
    text = _normalize_chunk_headings(text)
    text = _convert_visible_sources(text, source_display)
    return text.strip()


def _postprocess_document_markdown(markdown: str, source_display: str) -> str:
    """Clean a full-document rewrite without changing its heading hierarchy."""
    text = _clean_llm_markdown(markdown)
    text = _convert_visible_sources(text, source_display)
    return text.strip()


def _clean_llm_markdown(markdown: str) -> str:
    text = _unwrap_code_images(markdown)
    text = _fill_empty_image_alts(text)
    text = _remove_meta_paragraphs(text)
    return text


def _unwrap_code_images(markdown: str) -> str:
    return re.sub(r"`(!\[[^\]]*]\([^)]+\))`", r"\1", markdown)


def _fill_empty_image_alts(markdown: str) -> str:
    return re.sub(r"!\[\s*]\(", "![\u56fe\u793a](", markdown)


def _remove_meta_paragraphs(markdown: str) -> str:
    paragraphs = re.split(r"\n\s*\n", markdown)
    kept = [paragraph.strip() for paragraph in paragraphs if paragraph.strip() and not _is_meta_paragraph(paragraph)]
    return "\n\n".join(kept)


def _is_meta_paragraph(paragraph: str) -> bool:
    normalized = " ".join(line.strip() for line in paragraph.splitlines() if line.strip())
    banned_patterns = [
        "\u597d\u7684\uff0c\u8fd9\u662f",
        "\u597d\u7684\uff0c\u6211\u5c06",
        "\u4ee5\u4e0b\u662f\u6839\u636e",
        "\u4e0b\u9762\u662f\u4f9d\u636e",
        "\u6839\u636e\u60a8\u63d0\u4f9b\u7684 JSON",
        "\u6839\u636e\u4f60\u63d0\u4f9b\u7684 JSON",
        "\u8bfe\u7a0b\u6750\u6599 JSON",
        "\u7b14\u8bb0\u5df2\u4e25\u683c\u9075\u5faa",
        "\u4e25\u683c\u9075\u5faa\u5168\u90e8\u786c\u6027\u8981\u6c42",
        "\u8986\u76d6\u4e86\u6240\u6709\u6587\u672c\u5757",
        "\u8986\u76d6\u6bcf\u4e00\u4e2a\u6587\u672c\u5757",
        "\u6bcf\u6bb5\u5747\u6807\u6ce8",
        "\u6bcf\u4e00\u6bb5\u90fd\u6807\u6ce8",
        "\u672a\u63d0\u4f9b\u56fe\u7247\u50cf\u7d20",
        "\u672a\u63d0\u4f9b\u56fe\u50cf\u50cf\u7d20",
        "\u672a\u63d0\u4f9b\u56fe\u7247\u7684 OCR",
        "\u672a\u63d0\u4f9b\u8be5\u622a\u56fe\u7684 OCR",
        "\u672a\u8fdb\u884c\u89c6\u89c9\u89e3\u6790",
        "\u65e0\u6cd5\u8fdb\u884c\u5177\u4f53\u63cf\u8ff0",
        "\u65e0\u6cd5\u8fdb\u4e00\u6b65\u8bf4\u660e",
        "\u65e0\u6cd5\u5bf9\u622a\u56fe\u5185\u5bb9",
        "\u5efa\u8bae\u5728\u539f\u59cb\u5e7b\u706f\u7247",
        "\u82e5\u9700\u4e86\u89e3\u56fe\u7247\u5177\u4f53\u5185\u5bb9",
        "\u56fe\u7247\u7559\u4f5c\u539f\u59cb\u8bc1\u636e",
        "\u4ec5\u4f5c\u4e3a\u8bc1\u636e\u4fdd\u7559",
    ]
    if any(pattern in normalized for pattern in banned_patterns):
        return True
    structure_only_patterns = [
        "\u5e7b\u706f\u7247\u9996\u5148\u63d0\u51fa",
        "\u8fd9\u4e00\u9875\u5728\u4e0a\u4e00\u9875\u7684\u57fa\u7840\u4e0a",
        "\u4e0a\u4e00\u9875\u4ecb\u7ecd\u4e86",
        "\u4e0b\u4e00\u9875\u5c06",
        "\u672c\u9875\u4e3b\u8981\u8bb2\u89e3",
        "\u672c\u9875\u4ecb\u7ecd\u4e86",
        "\u8fd9\u9875\u5c55\u793a",
        "\u6b64\u9875\u5185\u5bb9",
        "\u6b64\u5e7b\u706f\u7247",
        "\u8fd9\u5f20\u5e7b\u706f\u7247",
        "\u8fd9\u7ec4\u5e7b\u706f\u7247",
        "\u8be5\u5e7b\u706f\u7247",
        "\u5f53\u524d\u5e7b\u706f\u7247",
    ]
    if SOURCE_COMMENT_PREFIX in normalized or len(normalized) > 80:
        return False
    return any(normalized.startswith(pattern) for pattern in structure_only_patterns)


def _normalize_chunk_headings(markdown: str) -> str:
    lines: list[str] = []
    for line in markdown.splitlines():
        match = re.match(r"^(#{1,6})\s+(.*)$", line)
        if not match:
            lines.append(line)
            continue
        text = re.sub(r"^\u8bfe\u7a0b\u7b14\u8bb0[\uff1a:\s-]*", "", match.group(2).strip())
        if not text:
            continue
        level = max(2, len(match.group(1)))
        lines.append("#" * level + " " + text)
    return "\n".join(lines)


def _convert_visible_sources(markdown: str, source_display: str) -> str:
    if source_display == "inline":
        return _ensure_source_comments_for_inline(markdown)

    def replace(match: re.Match[str]) -> str:
        citation = match.group(0)
        element_ids = re.findall(r"\bs\d+_(?:t|tbl|img|fig)\d+\b", citation)
        slide_match = re.search(r"\u7b2c\s*(\d+)\s*\u9875", citation)
        if not slide_match:
            return ""
        slide_id = int(slide_match.group(1))
        if source_display == "footnote":
            return _source_marker(slide_id, element_ids, "footnote")
        return _source_marker(slide_id, element_ids, "hidden")

    return re.sub(r"\u3010[^\u3011]*?PPT[^\u3011]*?\u3011", replace, markdown)


def _ensure_source_comments_for_inline(markdown: str) -> str:
    def replace(match: re.Match[str]) -> str:
        citation = match.group(0)
        if SOURCE_COMMENT_PREFIX in citation:
            return citation
        element_ids = re.findall(r"\bs\d+_(?:t|tbl|img|fig)\d+\b", citation)
        slide_match = re.search(r"\u7b2c\s*(\d+)\s*\u9875", citation)
        if not slide_match or not element_ids:
            return citation
        return f"{citation} {_source_marker(int(slide_match.group(1)), element_ids, 'hidden')}"

    return re.sub(r"\u3010[^\u3011]*?PPT[^\u3011]*?\u3011", replace, markdown)
