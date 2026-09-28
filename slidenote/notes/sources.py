"""Source markers and shared markdown helpers used across note assembly."""

from __future__ import annotations

import re

from slidenote.models import ImageAsset, SlidePage


SOURCE_COMMENT_PREFIX = "slidenote-source:"


def _collapse_blank_lines(lines: list[str]) -> str:
    collapsed: list[str] = []
    blank = False
    for line in lines:
        is_blank = not line.strip()
        if is_blank and blank:
            continue
        collapsed.append(line)
        blank = is_blank
    return "\n".join(collapsed)


def _source_marker(slide_id: int, element_ids: list[str], source_display: str) -> str:
    ids = [element_id for element_id in element_ids if element_id]
    comment = f"<!-- {SOURCE_COMMENT_PREFIX} p{slide_id}:{','.join(ids)} -->" if ids else ""
    if source_display == "hidden":
        return comment
    if source_display == "footnote":
        return f"\uff08PPT \u7b2c {slide_id} \u9875\uff09 {comment}".rstrip()
    detail = "\u3001".join(ids)
    return f"\u3010\u5bf9\u5e94 PPT\uff1a\u7b2c {slide_id} \u9875\uff0c\u5143\u7d20 {detail}\u3011 {comment}".rstrip()


def _image_source_ids(image: ImageAsset) -> list[str]:
    ids: list[str] = []
    seen: set[str] = set()
    for element_id in [image.id, *image.source_element_ids]:
        if element_id and element_id not in seen:
            ids.append(element_id)
            seen.add(element_id)
    return ids


def _page_element_ids(page: SlidePage) -> list[str]:
    ids = [block.id for block in page.text_blocks]
    ids.extend(table.id for table in page.tables)
    return ids


def _page_source_ids(page: SlidePage) -> list[str]:
    ids = _page_element_ids(page)
    ids.extend(image.id for image in page.images if not image.ignored)
    return ids


def _source_slide_ids(markdown: str) -> set[int]:
    return {int(match) for match in re.findall(r"\bp(\d+):", markdown)}
