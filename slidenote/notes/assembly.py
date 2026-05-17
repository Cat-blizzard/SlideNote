from __future__ import annotations

import base64
import mimetypes
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from slidenote.figure_grounding import FIGURE_PLACEMENT_MODES, note_candidate_images
from slidenote.image_ranking import sorted_images_by_importance
from slidenote.models import Deck, ImageAsset, SlidePage, TableBlock, TextBlock

SOURCE_COMMENT_PREFIX = "slidenote-source:"


@dataclass(frozen=True, slots=True)
class NoteContext:
    id: str
    kind: str
    title: str
    pages: list[SlidePage]


# ---------------------------------------------------------------------------
# Figure grounding & image helpers
# ---------------------------------------------------------------------------

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
            if _image_markdown_present(current, image_path):
                if image.id not in _source_tokens(current):
                    current = _ensure_image_source_marker(current, page, image, image_path, source_display)
                continue
            block = "\n".join(_render_image(page, image, asset_map=asset_map, source_display=source_display)).strip()
            if not block:
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


# ---------------------------------------------------------------------------
# Image rendering (moved from local to break circular dependency)
# ---------------------------------------------------------------------------

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
    if image.ocr_text and image.figure_explanation_status != "ocr_text":
        if explanation:
            lines.append("")
        lines.append("\u56fe\u7247 OCR \u6587\u5b57\uff1a")
        lines.extend(_quote_multiline(image.ocr_text))
    if explanation or (image.ocr_text and image.figure_explanation_status != "ocr_text"):
        lines.append("")
    lines.append(f"![{caption}]({_asset_display_path(image.path, asset_map)})")
    return lines


def _ensure_sentence(text: str) -> str:
    value = " ".join(text.split()).strip()
    if value and value[-1] not in "\u3002.!!\uff1f?\uff1a:":
        value += "\u3002"
    return value


def _quote_multiline(text: str) -> list[str]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return [f"> {line}" for line in lines]


# ---------------------------------------------------------------------------
# Composition / final markdown assembly
# ---------------------------------------------------------------------------

def _compose_final_markdown(
    deck: Deck,
    contexts: list[NoteContext],
    final_chunks: dict[str, str],
    section_plan: dict[str, Any] | None,
    source_display: str,
) -> str:
    del source_display
    lines = [f"# {_document_title(deck)}", ""]
    add_context_headings = _should_add_context_headings(contexts)
    leading_frontmatter_slide_ids = _leading_frontmatter_slide_ids(deck.pages)
    section_number = 1
    for context in contexts:
        content = final_chunks.get(context.id, "").strip()
        if not content:
            continue
        if add_context_headings:
            heading_title = _context_heading_title(context, section_plan)
            if _is_frontmatter_heading(heading_title, context) and len(contexts) > 1:
                content = _frontmatter_source_markers(context.pages)
            else:
                lines.append(_context_heading(context, heading_title, section_number))
                lines.append("")
                section_number += 1
                content = _prepare_context_chunk(content, heading_title, add_outer_heading=True)
                content = _strip_leading_frontmatter_content(content, context, leading_frontmatter_slide_ids)
                content = _number_subsection_headings(content)
        else:
            content = _prepare_context_chunk(content, context.title, add_outer_heading=False)
        if content:
            lines.append(content)
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Heading / title utilities
# ---------------------------------------------------------------------------

def _document_title(deck: Deck) -> str:
    stem = Path(deck.source_path).stem
    for page in deck.pages[:3]:
        title = (page.title or "").strip()
        if title and not _is_generic_heading_text(title):
            return f"{stem}\uff1a{title}" if title != stem else stem
    return stem


def _should_add_context_headings(contexts: list[NoteContext]) -> bool:
    if not contexts:
        return False
    if len(contexts) > 1:
        return True
    return contexts[0].kind == "section"


def _context_heading(context: NoteContext, title: str, section_number: int) -> str:
    if context.kind == "page":
        slide_id = context.pages[0].slide_id if context.pages else section_number
        return f"## \u7b2c {slide_id} \u9875\uff1a{title}"
    return f"## {_chinese_ordinal(section_number)}\u3001{title}"


def _context_heading_title(context: NoteContext, section_plan: dict[str, Any] | None) -> str:
    planned_title = _planned_context_title(context, section_plan)
    title = planned_title or context.title or ""
    title = _clean_heading_text(title)
    if title:
        return title
    if context.kind == "page" and context.pages:
        return context.pages[0].title or f"\u7b2c {context.pages[0].slide_id} \u9875"
    return "\u672c\u8282\u5185\u5bb9"


def _planned_context_title(context: NoteContext, section_plan: dict[str, Any] | None) -> str | None:
    if not section_plan:
        return None
    sections = section_plan.get("sections")
    if not isinstance(sections, list):
        return None
    slide_ids = [page.slide_id for page in context.pages]
    for section in sections:
        if not isinstance(section, dict):
            continue
        if section.get("section_id") == context.id or section.get("slide_ids") == slide_ids:
            title = str(section.get("title") or "").strip()
            return title or None
    return None


def _prepare_context_chunk(markdown: str, context_title: str, add_outer_heading: bool) -> str:
    text = _remove_generation_info_sections(markdown)
    text = _drop_redundant_leading_headings(text, context_title) if add_outer_heading else text
    text = _demote_chunk_headings(text, minimum_level=3 if add_outer_heading else 2)
    text = _remove_empty_sections(text)
    return text.strip()


def _drop_redundant_leading_headings(markdown: str, context_title: str) -> str:
    lines = markdown.splitlines()
    while True:
        first_index = next((index for index, line in enumerate(lines) if line.strip()), None)
        if first_index is None:
            return ""
        match = re.match(r"^(#{1,6})\s+(.*)$", lines[first_index].strip())
        if not match:
            return "\n".join(lines).strip()
        heading_text = _clean_heading_text(match.group(2))
        if not _is_redundant_context_heading(heading_text, context_title):
            return "\n".join(lines).strip()
        del lines[first_index]
        while first_index < len(lines) and not lines[first_index].strip():
            del lines[first_index]


def _is_redundant_context_heading(heading_text: str, context_title: str) -> bool:
    heading_norm = _normalize_title_key(heading_text)
    context_norm = _normalize_title_key(context_title)
    if not heading_norm:
        return True
    if _is_generic_heading_text(heading_text):
        return True
    return bool(context_norm and (heading_norm == context_norm or heading_norm in context_norm or context_norm in heading_norm))


def _demote_chunk_headings(markdown: str, minimum_level: int) -> str:
    lines: list[str] = []
    for line in markdown.splitlines():
        match = re.match(r"^(#{1,6})\s+(.*)$", line)
        if not match:
            lines.append(line)
            continue
        text = _clean_heading_text(match.group(2))
        if not text or _is_generic_heading_text(text):
            continue
        level = max(minimum_level, len(match.group(1)))
        lines.append("#" * min(level, 6) + " " + text)
    return "\n".join(lines)


def _strip_leading_frontmatter_content(markdown: str, context: NoteContext, leading_frontmatter_slide_ids: set[int]) -> str:
    frontmatter_slide_ids = {page.slide_id for page in context.pages if page.slide_id in leading_frontmatter_slide_ids}
    if not frontmatter_slide_ids:
        return markdown
    blocks = re.split(r"\n\s*\n", markdown.strip())
    kept: list[str] = []
    dropping = True
    for block in blocks:
        stripped = block.strip()
        if not stripped:
            continue
        if dropping and _is_horizontal_rule(stripped):
            continue
        if dropping and _is_droppable_frontmatter_block(stripped, frontmatter_slide_ids):
            continue
        dropping = False
        kept.append(stripped)
    marker = _frontmatter_source_markers([page for page in context.pages if page.slide_id in frontmatter_slide_ids])
    if not marker:
        return "\n\n".join(kept).strip()
    body = "\n\n".join(kept).strip()
    return f"{marker}\n\n{body}".strip() if body else marker


def _is_droppable_frontmatter_block(block: str, frontmatter_slide_ids: set[int]) -> bool:
    match = re.match(r"^(#{1,6})\s+(.*)$", block)
    if match:
        return _is_generic_heading_text(match.group(2)) or _looks_like_frontmatter_text(match.group(2))
    slide_ids = _source_slide_ids(block)
    if slide_ids and slide_ids.issubset(frontmatter_slide_ids):
        return True
    return _looks_like_frontmatter_text(block)


def _is_horizontal_rule(block: str) -> bool:
    return bool(re.fullmatch(r"[-*_]{3,}", block.strip()))


def _number_subsection_headings(markdown: str) -> str:
    counters: list[int] = []
    base_level: int | None = None
    lines: list[str] = []
    for line in markdown.splitlines():
        match = re.match(r"^(#{1,6})\s+(.*)$", line)
        if not match:
            lines.append(line)
            continue
        level = len(match.group(1))
        title = _strip_heading_number(match.group(2).strip())
        if level < 3:
            counters = []
            base_level = None
            lines.append(line)
            continue
        if base_level is None or level < base_level:
            base_level = level
            counters = []
        depth = max(0, level - base_level)
        while len(counters) <= depth:
            counters.append(0)
        counters = counters[: depth + 1]
        counters[depth] += 1
        prefix = ".".join(str(value) for value in counters)
        separator = ". " if len(counters) == 1 else " "
        lines.append(f"{match.group(1)} {prefix}{separator}{title}")
    return "\n".join(lines)


def _strip_heading_number(title: str) -> str:
    return re.sub(
        r"^\s*(?:\d+(?:\.\d+)*\.?|[\u4e00\u4e8c\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d\u5341]+[\u3001.])\s*",
        "",
        title,
    ).strip()


def _remove_generation_info_sections(markdown: str) -> str:
    lines = markdown.splitlines()
    kept: list[str] = []
    skipping = False
    skip_level = 0
    for line in lines:
        match = re.match(r"^(#{1,6})\s+(.*)$", line)
        if match:
            level = len(match.group(1))
            heading = _normalize_title_key(match.group(2))
            if heading in {"\u751f\u6210\u4fe1\u606f", "generationinfo", "generationmetadata"}:
                skipping = True
                skip_level = level
                continue
            if skipping and level <= skip_level:
                skipping = False
        if not skipping:
            kept.append(line)
    return "\n".join(kept)


def _remove_empty_sections(markdown: str) -> str:
    lines = markdown.splitlines()
    cleaned: list[str] = []
    previous_blank = False
    for line in lines:
        blank = not line.strip()
        if blank and previous_blank:
            continue
        cleaned.append(line)
        previous_blank = blank
    return "\n".join(cleaned).strip()


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
    return index <= 3 and _looks_like_outline_page(text)


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


def _looks_like_outline_page(text: str) -> bool:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    numbered = [
        line
        for line in lines
        if re.match(r"^\s*(?:\d+|[\u4e00\u4e8c\u4e09\u56db\u4e94])\s*[.\u3001\uff0e]", line)
    ]
    return len(numbered) >= 3 and sum(len(line) for line in numbered) <= 260


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


def _chinese_ordinal(index: int) -> str:
    numerals = ["\u4e00", "\u4e8c", "\u4e09", "\u56db", "\u4e94", "\u516d", "\u4e03", "\u516b", "\u4e5d", "\u5341"]
    if 1 <= index <= 10:
        return numerals[index - 1]
    if 11 <= index <= 19:
        return "\u5341" + numerals[index - 11]
    if index == 20:
        return "\u4e8c\u5341"
    return str(index)


# ---------------------------------------------------------------------------
# Source markers
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Asset management
# ---------------------------------------------------------------------------

def _prepare_note_assets(deck: Deck, output_root: Path, asset_mode: str, screenshot_policy: str) -> tuple[dict[str, str], list[str]]:
    asset_map: dict[str, str] = {}
    warnings: list[str] = []
    seen_destinations: set[Path] = set()
    for rel_path, kind in _iter_note_asset_paths(deck, screenshot_policy=screenshot_policy):
        if rel_path in asset_map:
            continue
        source_path = _resolve_output_asset(output_root, rel_path)
        if not source_path.exists():
            warnings.append(f"Missing note asset: {rel_path}")
            continue
        if asset_mode == "absolute":
            asset_map[rel_path] = source_path.as_posix()
        elif asset_mode == "embed":
            embedded = _embed_asset(source_path)
            if embedded:
                asset_map[rel_path] = embedded
            else:
                warnings.append(f"Could not embed note asset: {rel_path}")
        else:
            destination = _bundled_asset_destination(output_root, rel_path, kind, seen_destinations)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, destination)
            asset_map[rel_path] = destination.relative_to(output_root).as_posix()
            seen_destinations.add(destination)
    return asset_map, warnings


def _iter_note_asset_paths(deck: Deck, screenshot_policy: str) -> list[tuple[str, str]]:
    paths: list[tuple[str, str]] = []
    for page in deck.pages:
        if _should_render_screenshot(page, screenshot_policy):
            paths.append((page.page_screenshot, "screenshots"))
        for image in sorted_images_by_importance(page.images):
            if not image.ignored:
                kind = "figures" if image.role in {"figure_crop", "composite_figure"} else "images"
                paths.append((image.path, kind))
    return paths


def _resolve_output_asset(output_root: Path, path: str) -> Path:
    asset_path = Path(path)
    if asset_path.is_absolute():
        return asset_path
    return (output_root / asset_path).resolve()


def _bundled_asset_destination(output_root: Path, rel_path: str, kind: str, seen_destinations: set[Path]) -> Path:
    source = Path(rel_path)
    subdir = "screenshots" if kind == "screenshots" else "figures" if kind == "figures" else "images"
    stem = source.stem or "asset"
    suffix = source.suffix or ".png"
    destination = output_root / "notes.assets" / subdir / f"{stem}{suffix}"
    counter = 2
    while destination in seen_destinations:
        destination = output_root / "notes.assets" / subdir / f"{stem}-{counter}{suffix}"
        counter += 1
    return destination


def _embed_asset(source_path: Path) -> str | None:
    try:
        data = source_path.read_bytes()
    except OSError:
        return None
    mime_type, _ = mimetypes.guess_type(source_path.name)
    if not mime_type:
        mime_type = "application/octet-stream"
    return f"data:{mime_type};base64,{base64.b64encode(data).decode('ascii')}"


def _asset_display_path(path: str, asset_map: dict[str, str]) -> str:
    return asset_map.get(path, path)


def _should_render_screenshot(page: SlidePage, screenshot_policy: str) -> bool:
    if not page.page_screenshot:
        return False
    if screenshot_policy == "always":
        return True
    if screenshot_policy == "never":
        return False
    return not any(not image.ignored and image.role != "page_image" for image in page.images)


def _validate_markdown_image_links(markdown: str, output_root: Path) -> list[str]:
    warnings: list[str] = []
    for target in re.findall(r"!\[[^\]]*]\(([^)]+)\)", markdown):
        cleaned = target.strip().strip("<>")
        if not cleaned or cleaned.startswith(("data:", "http://", "https://")):
            continue
        path = Path(cleaned)
        if path.is_absolute():
            candidate = path
        else:
            candidate = output_root / path
        if not candidate.exists():
            warnings.append(f"Markdown image link target is missing: {cleaned}")
    for target in re.findall(r"`(!\[[^\]]*]\([^)]+\))`", markdown):
        warnings.append(f"Markdown image is wrapped as code and will not render: {target}")
    return warnings


# ---------------------------------------------------------------------------
# Context selection
# ---------------------------------------------------------------------------

def _select_note_contexts(deck: Deck, requested: str, section_plan: dict[str, Any] | None = None) -> list[NoteContext]:
    resolved = _resolved_context_mode(deck, requested)
    if resolved == "document":
        return [NoteContext(id="doc", kind="document", title=Path(deck.source_path).stem, pages=list(deck.pages))]
    if resolved == "page":
        return [
            NoteContext(id=f"p{page.slide_id}", kind="page", title=page.title or f"\u7b2c {page.slide_id} \u9875", pages=[page])
            for page in deck.pages
        ]
    return _section_contexts(deck, section_plan=section_plan)


def _resolved_context_mode(deck: Deck, requested: str) -> str:
    if requested != "auto":
        return requested
    if len(deck.pages) <= 12 and _structured_char_count(deck) <= 16_000:
        return "document"
    return "section"


def _structured_char_count(deck: Deck) -> int:
    total = 0
    for page in deck.pages:
        total += sum(len(block.content) for block in page.text_blocks)
        total += sum(len(cell) for table in page.tables for row in table.rows for cell in row)
        total += len(page.page_ocr_text or "") + len(page.page_visual_summary or "")
        total += sum(len(image.ocr_text or "") + len(image.visual_summary or "") for image in page.images)
    return total


def _section_contexts(deck: Deck, section_plan: dict[str, Any] | None = None) -> list[NoteContext]:
    if not deck.pages:
        return []
    if section_plan:
        planned_contexts = _section_contexts_from_plan(deck, section_plan)
        if planned_contexts:
            return planned_contexts
    boundaries = _section_boundaries(deck)
    if len(boundaries) <= 1:
        boundaries = [deck.pages[index].slide_id for index in range(0, len(deck.pages), 8)]
    contexts: list[NoteContext] = []
    slide_to_index = {page.slide_id: index for index, page in enumerate(deck.pages)}
    boundary_indexes = sorted({slide_to_index[slide_id] for slide_id in boundaries if slide_id in slide_to_index})
    if not boundary_indexes or boundary_indexes[0] != 0:
        boundary_indexes.insert(0, 0)
    for position, start_index in enumerate(boundary_indexes):
        end_index = boundary_indexes[position + 1] if position + 1 < len(boundary_indexes) else len(deck.pages)
        pages = deck.pages[start_index:end_index]
        if not pages:
            continue
        title = _context_title(pages, position + 1)
        contexts.append(NoteContext(id=f"sec{position + 1}", kind="section", title=title, pages=pages))
    return contexts


def _section_contexts_from_plan(deck: Deck, section_plan: dict[str, Any]) -> list[NoteContext]:
    pages_by_id = {page.slide_id: page for page in deck.pages}
    contexts: list[NoteContext] = []
    sections = section_plan.get("sections")
    if not isinstance(sections, list):
        return []
    for index, section in enumerate(sections, start=1):
        if not isinstance(section, dict):
            continue
        raw_ids = section.get("slide_ids")
        if not isinstance(raw_ids, list):
            continue
        pages = [pages_by_id[slide_id] for slide_id in raw_ids if isinstance(slide_id, int) and slide_id in pages_by_id]
        if not pages:
            continue
        context_id = str(section.get("section_id") or f"sec{index}")
        title = str(section.get("title") or _context_title(pages, index)).strip() or _context_title(pages, index)
        contexts.append(NoteContext(id=context_id, kind="section", title=title, pages=pages))
    return contexts


def _section_boundaries(deck: Deck) -> list[int]:
    outline_titles = _outline_titles(deck)
    boundaries = [deck.pages[0].slide_id]
    for page in deck.pages[1:]:
        title = _normalize_heading_text(page.title or "")
        if not title or "\u76ee\u5f55" in title or title.lower() == "contents":
            continue
        if any(title == outline or title in outline or outline in title for outline in outline_titles):
            boundaries.append(page.slide_id)
        elif not outline_titles and _looks_like_section_title_page(page):
            boundaries.append(page.slide_id)
    return sorted(set(boundaries))


def _outline_titles(deck: Deck) -> set[str]:
    titles: set[str] = set()
    for page in deck.pages:
        page_text = "\n".join(block.content for block in page.text_blocks)
        if "\u76ee\u5f55" not in page_text and "Contents" not in page_text:
            continue
        for line in page_text.splitlines():
            normalized = _normalize_heading_text(line)
            if not normalized or normalized.lower() in {"\u76ee\u5f55", "contents"}:
                continue
            if len(normalized) >= 4:
                titles.add(normalized)
    return titles


def _normalize_heading_text(value: str) -> str:
    value = re.sub(r"^\s*(?:\d+|[\u4e00\u4e8c\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d\u5341]+)(?:[.\u3001\s-]+)", "", value.strip())
    return re.sub(r"\s+", "", value).strip("\uff1a:")


def _looks_like_section_title_page(page: SlidePage) -> bool:
    content_blocks = [
        block
        for block in page.text_blocks
        if block.content.strip() and not re.fullmatch(r"\d+", block.content.strip())
    ]
    if not page.title or len(content_blocks) > 3:
        return False
    text_len = sum(len(block.content.strip()) for block in content_blocks)
    return text_len <= 120


def _context_title(pages: list[SlidePage], index: int) -> str:
    for page in pages:
        if page.title:
            return page.title
    return f"\u7b2c {index} \u8282"


# ---------------------------------------------------------------------------
# Postprocessing
# ---------------------------------------------------------------------------

def _postprocess_llm_markdown(markdown: str, source_display: str) -> str:
    text = _unwrap_code_images(markdown)
    text = _fill_empty_image_alts(text)
    text = _remove_meta_paragraphs(text)
    text = _normalize_chunk_headings(text)
    text = _convert_visible_sources(text, source_display)
    return text.strip()


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


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------

def _build_page_notes_report(
    deck: Deck,
    output_root: Path,
    provider: str,
    model: str,
    base_url: str | None,
    note_depth: str,
    note_language: str,
    term_policy: str,
    page_neighborhood: int,
    pages: list[NoteContext],
    page_markdown_by_slide: dict[int, str],
    page_records: list[dict[str, Any]],
    deck_brief: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from slidenote.llm_cache import utc_now_iso
    from .prompts import _prompt_deck_brief, _prompt_brief_hash
    prompt_brief = _prompt_deck_brief(deck_brief)
    record_by_slide = {record.get("slide_id"): record for record in page_records}
    page_entries: list[dict[str, Any]] = []
    for context in pages:
        page = context.pages[0]
        record = record_by_slide.get(page.slide_id, {})
        markdown = page_markdown_by_slide.get(page.slide_id, "")
        page_entries.append(
            {
                "slide_id": page.slide_id,
                "title": page.title,
                "markdown": markdown,
                "source_ids": sorted(_source_tokens(markdown)),
                "cache_status": record.get("cache_status"),
                "llm_call": record.get("llm_call"),
                "cache_file": record.get("cache_file"),
                "input_tokens": record.get("input_tokens"),
                "output_tokens": record.get("output_tokens"),
                "total_tokens": record.get("total_tokens"),
            }
        )
    return {
        "schema_version": 1,
        "generated_at": utc_now_iso(),
        "source_path": deck.source_path,
        "source_type": deck.source_type,
        "provider": provider,
        "model": model,
        "base_url": base_url,
        "prompt_version": "page-lecture-v4",
        "request": {
            "note_depth": note_depth,
            "note_language": note_language,
            "term_policy": term_policy,
            "page_neighborhood": page_neighborhood,
            "deck_brief_used": bool(prompt_brief),
            "deck_brief_hash": _prompt_brief_hash(prompt_brief),
        },
        "summary": {
            "pages_total": len(page_entries),
            "llm_calls": sum(1 for record in page_records if record.get("llm_call")),
            "local_cache_hits": sum(1 for record in page_records if record.get("cache_status") == "local_hit"),
            "input_tokens": _sum_int(record.get("input_tokens") for record in page_records),
            "output_tokens": _sum_int(record.get("output_tokens") for record in page_records),
            "total_tokens": _sum_int(record.get("total_tokens") for record in page_records),
        },
        "pages": page_entries,
    }


def _render_page_notes_markdown(deck: Deck, page_notes: dict[str, Any]) -> str:
    lines = [f"# {Path(deck.source_path).stem} Page Notes", ""]
    for page in page_notes.get("pages", []):
        title = page.get("title") or f"\u7b2c {page.get('slide_id')} \u9875"
        lines.append(f"## \u7b2c {page.get('slide_id')} \u9875\uff1a{title}")
        lines.append("")
        markdown = str(page.get("markdown") or "").strip()
        if markdown:
            lines.append(markdown)
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _build_weave_report(
    deck: Deck,
    output_root: Path,
    note_context: str,
    note_depth: str,
    note_language: str,
    term_policy: str,
    weave_dedup: str,
    contexts: list[NoteContext],
    final_chunks: dict[str, str],
    page_markdown_by_slide: dict[int, str],
    weave_records: list[dict[str, Any]],
    deck_brief: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from slidenote.llm_cache import utc_now_iso
    from .prompts import _prompt_deck_brief, _prompt_brief_hash
    prompt_brief = _prompt_deck_brief(deck_brief)
    record_by_context = {record.get("context_id"): record for record in weave_records}
    context_entries: list[dict[str, Any]] = []
    for context in contexts:
        markdown = final_chunks.get(context.id, "")
        final_tokens = _source_tokens(markdown)
        input_tokens: set[str] = set()
        pages: list[dict[str, Any]] = []
        for page in context.pages:
            page_tokens = _source_tokens(page_markdown_by_slide.get(page.slide_id, ""))
            input_tokens.update(page_tokens)
            pages.append(
                {
                    "slide_id": page.slide_id,
                    "title": page.title,
                    "page_note_source_ids": sorted(page_tokens),
                    "retained_source_ids": sorted(page_tokens.intersection(final_tokens)),
                    "possibly_compressed_source_ids": sorted(page_tokens - final_tokens),
                }
            )
        record = record_by_context.get(f"weave_{context.id}", {})
        context_entries.append(
            {
                "context_id": context.id,
                "context_title": context.title,
                "slide_ids": [page.slide_id for page in context.pages],
                "input_source_ids": sorted(input_tokens),
                "final_source_ids": sorted(final_tokens),
                "possibly_compressed_source_ids": sorted(input_tokens - final_tokens),
                "cache_status": record.get("cache_status"),
                "llm_call": record.get("llm_call"),
                "cache_file": record.get("cache_file"),
                "pages": pages,
            }
        )
    return {
        "schema_version": 1,
        "generated_at": utc_now_iso(),
        "source_path": deck.source_path,
        "source_type": deck.source_type,
        "prompt_version": "weave-v4",
        "request": {
            "note_context": note_context,
            "note_depth": note_depth,
            "note_language": note_language,
            "term_policy": term_policy,
            "weave_dedup": weave_dedup,
            "deck_brief_used": bool(prompt_brief),
            "deck_brief_hash": _prompt_brief_hash(prompt_brief),
        },
        "summary": {
            "contexts_total": len(context_entries),
            "llm_calls": sum(1 for record in weave_records if record.get("llm_call")),
            "local_cache_hits": sum(1 for record in weave_records if record.get("cache_status") == "local_hit"),
            "input_tokens": _sum_int(record.get("input_tokens") for record in weave_records),
            "output_tokens": _sum_int(record.get("output_tokens") for record in weave_records),
            "total_tokens": _sum_int(record.get("total_tokens") for record in weave_records),
        },
        "contexts": context_entries,
    }


def _source_tokens(markdown: str) -> set[str]:
    return set(re.findall(r"\bs\d+_(?:t|tbl|img|fig)\d+\b", markdown))


def _source_slide_ids(markdown: str) -> set[int]:
    return {int(match) for match in re.findall(r"\bp(\d+):", markdown)}


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _display_path(path: Path, output_root: Path) -> str:
    try:
        return path.resolve().relative_to(output_root.resolve()).as_posix()
    except ValueError:
        return str(path)


def _sum_int(values: object) -> int:
    total = 0
    for value in values:
        if isinstance(value, int):
            total += value
    return total
