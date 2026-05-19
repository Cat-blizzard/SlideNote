from __future__ import annotations

import re
from dataclasses import asdict
from typing import Any

from slidenote.content_guard import learning_items_for_page, page_role_for_slide
from slidenote.deck_brief import deck_brief_for_prompt
from slidenote.figure_grounding import ordered_page_elements
from slidenote.ir import build_page_ir
from slidenote.llm_cache import sha256_text, stable_json
from slidenote.models import Deck, SlidePage
from slidenote.semantic_layout import semantic_layout_for_prompt
from slidenote.table_understanding import table_preview

from .assembly import _asset_display_path, _section_contexts, _should_render_screenshot


def _page_payload_for_prompt(
    page: SlidePage,
    asset_map: dict[str, str],
    supports_image_input: bool,
    screenshot_policy: str,
    source_type: str,
    content_guard: dict[str, Any] | None = None,
) -> dict[str, Any]:
    page_payload = asdict(page)
    semantic_payload = semantic_layout_for_prompt(page)
    page_payload.pop("semantic_blocks", None)
    page_payload.pop("semantic_groups", None)
    page_payload.pop("semantic_relations", None)
    if semantic_payload:
        page_payload["semantic_layout"] = semantic_payload
    if page_payload.get("page_screenshot") and _should_render_screenshot(page, screenshot_policy):
        page_payload["page_screenshot"] = _asset_display_path(page_payload["page_screenshot"], asset_map)
    else:
        page_payload["page_screenshot"] = None
    if page_payload.get("images"):
        page_payload["images"] = sorted(
            page_payload["images"],
            key=lambda image: (
                image.get("ignored", False),
                image.get("importance_rank") if image.get("importance_rank") is not None else 9999,
                -(image.get("importance_score") or 0.0),
                image.get("id") or "",
            ),
        )
        for image in page_payload["images"]:
            image["path"] = _asset_display_path(image["path"], asset_map)
            image["visual_status"] = (
                "image pixels are available to the model"
                if supports_image_input
                else "image pixels are not attached to this note-writing call; use only supplied OCR/visual_summary"
            )
    prompt_deck = Deck(source_path="", source_type=source_type, pages=[page])
    page_payload["element_ir"] = build_page_ir(prompt_deck, page)
    page_payload["ordered_elements"] = ordered_page_elements(prompt_deck, page, asset_map=asset_map)
    if content_guard:
        page_payload["page_role"] = page_role_for_slide(content_guard, page.slide_id)
        page_payload["learning_items"] = learning_items_for_page(content_guard, page.slide_id)
    return page_payload


def _nearby_page_payloads(deck: Deck, slide_id: int, radius: int) -> list[dict[str, Any]]:
    if radius <= 0:
        return []
    page_indexes = {page.slide_id: index for index, page in enumerate(deck.pages)}
    current_index = page_indexes.get(slide_id)
    if current_index is None:
        return []
    start = max(0, current_index - radius)
    end = min(len(deck.pages), current_index + radius + 1)
    payloads: list[dict[str, Any]] = []
    for page in deck.pages[start:end]:
        if page.slide_id == slide_id:
            continue
        payloads.append(
            {
                "slide_id": page.slide_id,
                "title": page.title,
                "brief": _page_brief(page),
            }
        )
    return payloads


def _prompt_slide_scope(deck: Deck, slide_id: int, radius: int) -> list[int]:
    page_indexes = {page.slide_id: index for index, page in enumerate(deck.pages)}
    current_index = page_indexes.get(slide_id)
    if current_index is None:
        return [slide_id]
    start = max(0, current_index - max(0, radius))
    end = min(len(deck.pages), current_index + max(0, radius) + 1)
    return [page.slide_id for page in deck.pages[start:end]]


def _prompt_deck_brief(deck_brief: dict[str, Any] | None, slide_ids: list[int] | set[int] | None = None) -> dict[str, Any] | None:
    return deck_brief_for_prompt(deck_brief, slide_ids=slide_ids)


def _prompt_brief_hash(prompt_brief: dict[str, Any] | None) -> str | None:
    if not prompt_brief:
        return None
    return sha256_text(stable_json(prompt_brief))


def _page_brief(page: SlidePage, limit: int = 260) -> str:
    parts: list[str] = []
    if page.title:
        parts.append(page.title)
    parts.extend(block.content for block in page.text_blocks[:3] if block.content.strip())
    for table in page.tables[:2]:
        preview = table_preview(table, limit=220)
        if preview:
            parts.append(preview)
    if page.page_visual_summary:
        parts.append(page.page_visual_summary)
    text = re.sub(r"\s+", " ", " ".join(parts)).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "\u2026"


def _section_title_by_slide(deck: Deck, section_plan: dict[str, Any] | None = None) -> dict[int, str]:
    result: dict[int, str] = {}
    for context in _section_contexts(deck, section_plan=section_plan):
        for page in context.pages:
            result[page.slide_id] = context.title
    return result
