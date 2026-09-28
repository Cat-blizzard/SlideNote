"""Insert grounded figures next to the text they explain."""

from __future__ import annotations

import re

from slidenote.figure_grounding import note_candidate_images
from slidenote.models import Deck, ImageAsset, SlidePage
from slidenote.utils import source_tokens
from .assets import _asset_display_path
from .frontmatter import _leading_frontmatter_slide_ids
from .render_blocks import _render_image
from .sources import SOURCE_COMMENT_PREFIX, _collapse_blank_lines, _image_source_ids, _source_marker


def _ensure_grounded_figures(
    markdown: str,
    deck: Deck,
    asset_map: dict[str, str],
    source_display: str,
    figure_placement: str,
) -> str:
    current = markdown.rstrip()
    frontmatter_slide_ids = _leading_frontmatter_slide_ids(deck.pages)
    for page in deck.pages:
        if page.slide_id in frontmatter_slide_ids:
            continue
        for image in note_candidate_images(page):
            image_path = _asset_display_path(image.path, asset_map)
            block = "\n".join(_render_image(page, image, asset_map=asset_map, source_display=source_display)).strip()
            if not block:
                continue
            if figure_placement == "inline":
                current = _remove_existing_image_block(current, image_path, image)
                current = _insert_figure_block(current, page, image, block, figure_placement)
                continue
            if _image_markdown_present(current, image_path):
                if image.id not in source_tokens(current):
                    current = _ensure_image_source_marker(current, page, image, image_path, source_display)
                continue
            current = _insert_figure_block(current, page, image, block, figure_placement)
    return current.rstrip() + "\n"


def _image_markdown_present(markdown: str, image_path: str) -> bool:
    if not image_path:
        return False
    escaped = re.escape(image_path.strip())
    return bool(re.search(rf"!\[[^\]]*]\({escaped}\)", markdown)) or image_path in markdown


def _ensure_image_source_marker(
    markdown: str,
    page: SlidePage,
    image: ImageAsset,
    image_path: str,
    source_display: str,
) -> str:
    marker = _source_marker(page.slide_id, _image_source_ids(image), source_display)
    if not marker:
        return markdown
    lines = markdown.splitlines()
    for index, line in enumerate(lines):
        if image_path in line and line.lstrip().startswith("!["):
            if marker in line or (index + 1 < len(lines) and marker in lines[index + 1]):
                return markdown
            new_lines = list(lines)
            new_lines.insert(index + 1, marker)
            return "\n".join(new_lines).rstrip() + "\n"
    return markdown


def _remove_existing_image_block(markdown: str, image_path: str, image: ImageAsset) -> str:
    if not image_path:
        return markdown
    lines = markdown.splitlines()
    remove: set[int] = set()
    source_ids = set(_image_source_ids(image))
    for index, line in enumerate(lines):
        if not _line_has_image_target(line, image_path):
            continue
        remove.update(_image_block_indexes_to_remove(lines, index, image, source_ids))
    if not remove:
        return markdown
    kept = [line for index, line in enumerate(lines) if index not in remove]
    return _collapse_blank_lines(kept).rstrip() + "\n"


def _image_block_indexes_to_remove(lines: list[str], image_index: int, image: ImageAsset, source_ids: set[str]) -> set[int]:
    remove = {image_index}
    before = image_index - 1
    while before >= 0 and not lines[before].strip():
        remove.add(before)
        before -= 1
    if before >= 0 and _is_marker_only_for_ids(lines[before], source_ids):
        remove.add(before)
        caption = before - 1
        while caption >= 0 and not lines[caption].strip():
            remove.add(caption)
            caption -= 1
        if caption >= 0 and _is_image_caption_line(lines[caption], image):
            remove.add(caption)

    after = image_index + 1
    while after < len(lines) and not lines[after].strip():
        remove.add(after)
        after += 1
    if after < len(lines) and _is_marker_only_for_ids(lines[after], source_ids):
        remove.add(after)
    return remove


def _is_image_caption_line(line: str, image: ImageAsset) -> bool:
    stripped = line.strip()
    caption = (image.caption or "").strip()
    if caption and stripped in {caption, f"{caption}\u3002"}:
        return True
    return bool(re.fullmatch(r"\u7b2c\s*\d+\s*\u9875(?:\u56fe\u7247|\u56fe\u793a|\u622a\u56fe).*[\u3002.]?", stripped))


def _line_has_image_target(line: str, image_path: str) -> bool:
    normalized_path = image_path.strip().strip("<>").replace("\\", "/")
    for target in re.findall(r"!\[[^\]]*]\(([^)]+)\)", line):
        normalized_target = target.strip().strip("<>").replace("\\", "/")
        if normalized_target == normalized_path:
            return True
    return False


def _is_marker_only_for_ids(line: str, source_ids: set[str]) -> bool:
    stripped = line.strip()
    if not stripped or SOURCE_COMMENT_PREFIX not in stripped:
        return False
    if not re.fullmatch(r"<!--.*?-->", stripped):
        return False
    return bool(source_ids.intersection(source_tokens(stripped)))


def _insert_figure_block(markdown: str, page: SlidePage, image: ImageAsset, block: str, figure_placement: str) -> str:
    if figure_placement == "inline":
        inserted = _insert_after_anchor_source(markdown, image.anchor_element_ids, block)
        if inserted != markdown:
            return inserted
    inserted = _insert_after_page_source(markdown, page.slide_id, block)
    if inserted != markdown:
        return inserted
    fallback_heading = f"### \u7b2c {page.slide_id} \u9875\u56fe\u793a"
    return f"{markdown.rstrip()}\n\n{fallback_heading}\n\n{block}"


def _insert_after_anchor_source(markdown: str, anchor_ids: list[str], block: str) -> str:
    if not anchor_ids:
        return markdown
    lines = markdown.splitlines()
    for index, line in enumerate(lines):
        if SOURCE_COMMENT_PREFIX not in line:
            continue
        if not any(anchor_id in line for anchor_id in anchor_ids):
            continue
        insert_at = _paragraph_end_after(lines, index)
        return _insert_lines(lines, insert_at, block)
    return markdown


def _insert_after_page_source(markdown: str, slide_id: int, block: str) -> str:
    lines = markdown.splitlines()
    marker = f"p{slide_id}:"
    candidate_index: int | None = None
    for index, line in enumerate(lines):
        if SOURCE_COMMENT_PREFIX in line and marker in line:
            candidate_index = index
    if candidate_index is None:
        return markdown
    insert_at = _paragraph_end_after(lines, candidate_index)
    return _insert_lines(lines, insert_at, block)


def _paragraph_end_after(lines: list[str], index: int) -> int:
    cursor = index + 1
    while cursor < len(lines) and lines[cursor].strip():
        cursor += 1
    while cursor < len(lines) and not lines[cursor].strip():
        cursor += 1
    return cursor


def _insert_lines(lines: list[str], index: int, block: str) -> str:
    new_lines = list(lines)
    insert = ["", *block.splitlines(), ""]
    new_lines[index:index] = insert
    return "\n".join(new_lines).rstrip() + "\n"
