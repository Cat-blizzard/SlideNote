from __future__ import annotations

import json
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any

from slidenote.deck_brief import deck_brief_for_prompt
from slidenote.figure_grounding import ordered_page_elements
from slidenote.llm_cache import sha256_text, stable_json
from slidenote.models import Deck, SlidePage

from .assembly import _asset_display_path, _should_render_screenshot, _source_marker


def _llm_page_prompt(page: SlidePage, supports_image_input: bool = False) -> str:
    from . import NoteContext
    context = NoteContext(id=f"p{page.slide_id}", kind="page", title=page.title or f"\u7b2c {page.slide_id} \u9875", pages=[page])
    return _llm_context_prompt(
        context,
        supports_image_input=supports_image_input,
        asset_map={},
        source_display="hidden",
        note_context="page",
        note_style="article",
        note_language="zh",
        term_policy="bilingual",
        screenshot_policy="fallback",
        figure_placement="inline",
        source_type="pdf",
    )


def _llm_context_prompt(
    context,
    supports_image_input: bool,
    asset_map: dict[str, str],
    source_display: str,
    note_context: str,
    note_style: str,
    note_language: str,
    term_policy: str,
    screenshot_policy: str,
    figure_placement: str,
    source_type: str,
    deck_brief: dict[str, Any] | None = None,
) -> str:
    prompt_brief = _prompt_deck_brief(deck_brief, [page.slide_id for page in context.pages])
    payload = {
        "context_id": context.id,
        "context_kind": context.kind,
        "context_title": context.title,
        "figure_placement": figure_placement,
        "pages": [
            _page_payload_for_prompt(page, asset_map, supports_image_input, screenshot_policy, source_type=source_type)
            for page in context.pages
        ],
    }
    if prompt_brief:
        payload["deck_brief"] = prompt_brief
    source_rule = {
        "hidden": (
            "\u5728\u6bcf\u4e2a\u4e3b\u8981\u6bb5\u843d\u6216\u56fe\u7247\u540e\u5199 HTML \u9690\u85cf\u6765\u6e90\u6ce8\u91ca\uff0c\u683c\u5f0f\u4e25\u683c\u4e3a "
            "`<!-- slidenote-source: p4:s4_t1,s4_t2 -->`\uff1b\u4e0d\u8981\u5728\u53ef\u89c1\u6b63\u6587\u91cc\u5199\u5143\u7d20 ID\u3002"
        ),
        "footnote": (
            "\u6bcf\u4e2a\u4e3b\u8981\u6bb5\u843d\u672b\u5c3e\u53ea\u663e\u793a\u7b80\u6d01\u9875\u7801\uff0c\u4f8b\u5982 `\uff08PPT \u7b2c 4 \u9875\uff09`\uff0c\u5e76\u7ee7\u7eed\u9644\u52a0 "
            "`<!-- slidenote-source: p4:s4_t1,s4_t2 -->` \u9690\u85cf\u6765\u6e90\u6ce8\u91ca\u3002"
        ),
        "inline": "\u53ef\u4ee5\u5728\u6b63\u6587\u4e2d\u663e\u793a\u8be6\u7ec6\u6765\u6e90\u9875\u7801\u548c\u5143\u7d20 ID\uff0c\u540c\u65f6\u4e5f\u8981\u4fdd\u7559\u9690\u85cf\u6765\u6e90\u6ce8\u91ca\u3002",
    }[source_display]
    context_rule = {
        "document": "\u8fd9\u662f\u6574\u4efd\u6750\u6599\u7684\u4e0a\u4e0b\u6587\uff0c\u8bf7\u5199\u6210\u4e00\u7bc7\u8fde\u7eed\u7b14\u8bb0\u3002",
        "section": "\u8fd9\u662f\u540c\u4e00\u7ae0\u8282\u6216\u5c0f\u8282\u7684\u4e00\u7ec4\u9875\u9762\uff0c\u8bf7\u5199\u6210\u4e00\u4e2a\u8fde\u8d2f\u5c0f\u8282\uff0c\u4e0d\u8981\u9010\u9875\u673a\u68b0\u7ffb\u8bd1\u3002",
        "page": "\u8fd9\u662f\u5355\u9875\u8c03\u8bd5\u4e0a\u4e0b\u6587\uff0c\u8bf7\u5c3d\u91cf\u5b8c\u6574\u8986\u76d6\u8be5\u9875\u5143\u7d20\uff0c\u4f46\u4ecd\u8981\u4fdd\u6301\u81ea\u7136\u884c\u6587\u3002",
        "auto": "\u8fd9\u662f\u81ea\u52a8\u9009\u62e9\u7684\u4e0a\u4e0b\u6587\uff0c\u8bf7\u6839\u636e\u5185\u5bb9\u5199\u6210\u8fde\u8d2f\u7b14\u8bb0\u3002",
    }[note_context]
    style_rule = (
        "\u91c7\u7528\u5b66\u4e60\u7b14\u8bb0\u98ce\u683c\uff1a\u56f4\u7ed5\u6982\u5ff5\u3001\u539f\u56e0\u3001\u6761\u4ef6\u3001\u4f8b\u5b50\u548c\u63a8\u7406\u7ec4\u7ec7\u6b63\u6587\uff1b\u628a bullet\u3001table\u3001OCR \u548c\u91cd\u590d\u6587\u672c\u5408\u5e76\u6210\u53ef\u5b66\u4e60\u7684\u77e5\u8bc6\u5355\u5143\uff1b\u9690\u85cf source marker \u53ef\u4ee5\u627f\u62c5\u6eaf\u6e90\u8986\u76d6\uff0c\u4e0d\u8981\u4e3a\u4e86\u8986\u76d6\u7387\u9010\u5b57\u6bb5\u590d\u8ff0 PPT\u3002"
        if note_style == "article"
        else "\u91c7\u7528\u4fdd\u771f\u98ce\u683c\uff1a\u5c3d\u91cf\u4fdd\u6301\u539f\u59cb\u6761\u76ee\u987a\u5e8f\u548c\u5173\u952e\u7ec6\u8282\uff0c\u4f46\u4ecd\u8981\u5199\u6210\u53ef\u8bfb\u7684\u8bb2\u89e3\uff0c\u4e0d\u8981\u8f93\u51fa\u5143\u53d9\u8ff0\u6216 PPT \u9875\u9762\u6784\u6210\u8bf4\u660e\u3002"
    )
    structural_rule = (
        "\u5c01\u9762\u3001\u76ee\u5f55\u3001\u7ae0\u8282\u5bfc\u822a\u3001\u8bb2\u5e08\u4fe1\u606f\u3001\u8054\u7cfb\u65b9\u5f0f\u548c\u7eaf\u7248\u5f0f\u5143\u7d20\u4e0d\u8981\u5c55\u5f00\u6210\u6b63\u6587\uff1b"
        "\u8fd9\u4e9b\u5143\u7d20\u9700\u8986\u76d6\u65f6\uff0c\u53ea\u4fdd\u7559\u9690\u85cf\u6765\u6e90\u6807\u8bb0\uff0c\u6216\u628a\u5fc5\u8981\u80cc\u666f\u81ea\u7136\u5e76\u5165\u540e\u7eed\u77e5\u8bc6\u6bb5\u843d\u3002"
    )
    language_rule = _language_prompt_rule(note_language)
    term_rule = _term_policy_prompt_rule(note_language, term_policy)
    image_rule = (
        "\u56fe\u7247\u5fc5\u987b\u7528\u771f\u6b63\u7684 Markdown \u56fe\u7247\u8bed\u6cd5\u63d2\u5165\uff0c\u4e0d\u80fd\u653e\u8fdb\u53cd\u5f15\u53f7\u3002"
        "\u56fe\u7247 alt \u6587\u672c\u4e0d\u80fd\u4e3a\u7a7a\uff0c\u5e94\u7528\u7b80\u77ed\u6587\u5b57\u8bf4\u660e\u56fe\u7247\u4e3b\u9898\u3002"
        "\u5982\u679c JSON \u91cc\u6709 ocr_text\u3001visual_summary\u3001page_ocr_text \u6216 page_visual_summary\uff0c\u8981\u628a\u5b83\u4eec\u548c\u76f8\u5173\u6982\u5ff5\u5408\u5e76\u8bb2\u89e3\u3002"
        "\u5982\u679c JSON \u91cc\u6709 ordered_elements\u3001anchor_element_ids \u6216 figure_explanation\uff0c\u8bf7\u6309\u7248\u9762\u987a\u5e8f\u628a\u56fe\u7247\u653e\u5728\u951a\u70b9\u6bb5\u843d\u9644\u8fd1\uff0c\u89e3\u91ca\u8fd9\u5f20\u56fe\u8865\u5145\u4e86\u4ec0\u4e48\uff0c\u907f\u514d\u628a\u56fe\u7247\u5168\u90e8\u5806\u5230\u9875\u5c3e\u3002"
        "\u5982\u679c\u6ca1\u6709\u89c6\u89c9\u6458\u8981\uff0c\u4e0d\u8981\u5199\u201c\u672a\u63d0\u4f9b\u56fe\u7247\u50cf\u7d20\u201d\u201c\u65e0\u6cd5\u89c6\u89c9\u89e3\u6790\u201d\u201c\u5efa\u8bae\u67e5\u770b\u539f PPT\u201d\u7b49\u8bf4\u660e\uff1b\u53ea\u63d2\u5165\u56fe\u7247\uff0c\u4e0d\u731c\u6d4b\u56fe\u7247\u5185\u5bb9\u3002"
    )
    banned_rule = (
        "\u4e25\u7981\u8f93\u51fa\u4e0e\u8bfe\u7a0b\u5185\u5bb9\u65e0\u5173\u7684\u5143\u53d9\u8ff0\uff0c\u4f8b\u5982\u201c\u597d\u7684\uff0c\u8fd9\u662f\u2026\u2026\u201d\u201c\u4ee5\u4e0b\u662f\u6839\u636e JSON\u2026\u2026\u201d\u201c\u7b14\u8bb0\u5df2\u4e25\u683c\u9075\u5faa\u2026\u2026\u201d"
        "\u201c\u8986\u76d6\u4e86\u6240\u6709\u6587\u672c\u5757\u2026\u2026\u201d\u7b49\u3002\u4e0d\u8981\u51fa\u73b0\u201c\u8bfe\u7a0b\u7b14\u8bb0\u201d\u4f5c\u4e3a\u7ae0\u8282\u6807\u9898\uff1b\u5168\u6587 H1 \u5df2\u7531\u7cfb\u7edf\u751f\u6210\uff0c\u4f60\u53ea\u80fd\u4f7f\u7528 ## \u6216\u66f4\u4f4e\u7ea7\u6807\u9898\u3002"
        "\u4e25\u7981\u4f7f\u7528\u201c\u8fd9\u4e00\u9875\u201d\u201c\u672c\u9875\u201d\u201c\u5e7b\u706f\u7247\u201d\u201c\u4e0a\u4e00\u9875\u201d\u201c\u4e0b\u4e00\u9875\u201d\u201c\u6b64\u9875\u201d\u201c\u8fd9\u9875\u201d\u7b49\u63cf\u8ff0 PPT \u9875\u9762\u7ed3\u6784\u7684\u8868\u8ff0\uff0c\u76f4\u63a5\u8bb2\u89e3\u77e5\u8bc6\u5373\u53ef\u3002"
    )
    return (
        "\u8bf7\u628a\u4e0b\u9762\u7684\u8bfe\u7a0b\u6750\u6599 JSON \u6539\u5199\u6210\u53ef\u4ee5\u76f4\u63a5\u9605\u8bfb\u7684 Markdown \u7b14\u8bb0\u3002\n"
        "If deck_brief is present, use it only as a global navigation map; it must not replace page evidence.\n"
        f"{context_rule}\n"
        f"{style_rule}\n"
        f"{structural_rule}\n"
        f"{language_rule}\n"
        f"{term_rule}\n"
        "\u786c\u6027\u8981\u6c42\uff1a\n"
        "1. \u5148\u5224\u65ad\u54ea\u4e9b\u662f\u5b66\u751f\u771f\u6b63\u9700\u8981\u7406\u89e3\u7684\u77e5\u8bc6\u70b9\uff0c\u518d\u8bb2\u6e05\u5b83\u4eec\u7684\u542b\u4e49\u3001\u56e0\u679c\u3001\u4f8b\u5b50\u3001\u516c\u5f0f\u548c\u56fe\u8868\u7ed3\u8bba\u3002\n"
        "2. \u5408\u5e76\u91cd\u590d bullet\u3001\u8868\u683c\u58f3\u3001OCR \u91cd\u590d\u6587\u672c\u548c\u9875\u9762\u5b57\u6bb5\uff1b\u4e0d\u8981\u628a PPT \u5b57\u6bb5\u987a\u5e8f\u6539\u5199\u6210\u4e00\u4e32\u7ffb\u8bd1\u3002\n"
        f"3. {source_rule}\n"
        f"4. {image_rule}\n"
        f"5. {banned_rule}\n"
        "6. \u5916\u5c42\u7ae0\u8282\u6807\u9898\u7531\u7cfb\u7edf\u7edf\u4e00\u6dfb\u52a0\uff1b\u5982\u679c\u9700\u8981\u5c0f\u6807\u9898\uff0c\u53ea\u4f7f\u7528 ### \u6216\u66f4\u4f4e\u7ea7\u6807\u9898\u3002\n\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )


def _llm_page_lecture_prompt(
    deck: Deck,
    context,
    supports_image_input: bool,
    asset_map: dict[str, str],
    source_display: str,
    note_style: str,
    note_depth: str,
    note_language: str,
    term_policy: str,
    page_neighborhood: int,
    section_title: str | None,
    screenshot_policy: str,
    figure_placement: str,
    source_type: str,
    deck_brief: dict[str, Any] | None = None,
) -> str:
    page = context.pages[0]
    prompt_brief = _prompt_deck_brief(deck_brief, _prompt_slide_scope(deck, page.slide_id, page_neighborhood))
    payload = {
        "task": "page_lecture",
        "context_id": context.id,
        "source_file": Path(deck.source_path).stem,
        "section_title": section_title,
        "figure_placement": figure_placement,
        "current_page": _page_payload_for_prompt(page, asset_map, supports_image_input, screenshot_policy, source_type=source_type),
        "nearby_pages": _nearby_page_payloads(deck, page.slide_id, page_neighborhood),
    }
    if prompt_brief:
        payload["deck_brief"] = prompt_brief
    depth_rule = _note_depth_rule(note_depth)
    source_rule = _source_prompt_rule(source_display)
    language_rule = _language_prompt_rule(note_language)
    term_rule = _term_policy_prompt_rule(note_language, term_policy)
    style_rule = (
        "\u8bf7\u628a current_page \u63d0\u70bc\u6210\u50cf\u5b66\u751f\u8bfe\u540e\u7b14\u8bb0\u4e00\u6837\u7684\u77e5\u8bc6\u5355\u5143\uff1a\u76f4\u63a5\u8bb2\u6e05\u6982\u5ff5\u3001\u56e0\u679c\u3001\u6761\u4ef6\u3001\u4f8b\u5b50\u3001\u516c\u5f0f\u548c\u56fe\u8868\u7ed3\u8bba\uff0c\u5408\u5e76\u91cd\u590d bullet/table/OCR\uff0c\u4e0d\u8981\u9010\u5b57\u6bb5\u7ffb\u8bd1 PPT\u3002"
        if note_style == "article"
        else "\u8bf7\u5c3d\u91cf\u4fdd\u7559 current_page \u539f\u59cb\u6761\u76ee\u987a\u5e8f\uff0c\u4f46\u4ecd\u8981\u5199\u6210\u53ef\u4ee5\u76f4\u63a5\u9605\u8bfb\u7684\u77e5\u8bc6\u70b9\u8bb2\u89e3\uff0c\u800c\u4e0d\u662f\u590d\u5236\u6e05\u5355\u6216\u63cf\u8ff0\u9875\u9762\u6784\u6210\u3002"
    )
    structural_rule = (
        "\u5982\u679c current_page \u4e3b\u8981\u662f\u5c01\u9762\u3001\u76ee\u5f55\u3001\u7ae0\u8282\u5bfc\u822a\u3001\u8bb2\u5e08/\u8054\u7cfb\u65b9\u5f0f\u6216\u5176\u4ed6\u975e\u5b66\u4e60\u5185\u5bb9\uff0c"
        "\u4e0d\u8981\u5199\u6210\u6b63\u6587\u89e3\u8bf4\uff1b\u53ea\u4fdd\u7559\u8be5\u9875\u6240\u6709\u5143\u7d20\u7684\u6765\u6e90\u6807\u8bb0\uff0c\u4f9b\u6700\u7ec8\u7b14\u8bb0\u6eaf\u6e90\u4f7f\u7528\u3002"
    )
    return (
        "\u8bf7\u53ea\u8bb2\u89e3 JSON \u4e2d\u7684 current_page \u5305\u542b\u7684\u77e5\u8bc6\u5185\u5bb9\uff0c\u4e0d\u8981\u66ff\u5176\u4ed6\u9875\u9762\u5199\u6b63\u6587\u3002\n"
        "nearby_pages \u53ea\u7528\u4e8e\u7406\u89e3\u524d\u540e\u903b\u8f91\u548c\u51cf\u5c11\u91cd\u590d\uff0c\u4e0d\u80fd\u628a\u90bb\u8fd1\u9875\u5185\u5bb9\u5f53\u4f5c current_page \u7684\u5185\u5bb9\u5c55\u5f00\u3002\n"
        "If deck_brief is present, it is only a navigation map: current_page is the only source for the body; do not omit current_page elements because of the brief; do not pull later-page content into this page.\n"
        f"{style_rule}\n"
        f"{structural_rule}\n"
        f"{language_rule}\n"
        f"{term_rule}\n"
        f"{depth_rule}\n"
        "\u786c\u6027\u8981\u6c42\uff1a\n"
        "1. \u91cd\u8981\u5b9a\u4e49\u3001\u6761\u4ef6\u3001\u4f8b\u5b50\u3001\u516c\u5f0f\u3001\u8868\u683c\u3001\u56fe\u7247\u3001OCR \u548c\u89c6\u89c9\u6458\u8981\u82e5\u652f\u6491\u5f53\u524d\u77e5\u8bc6\u70b9\uff0c\u8981\u878d\u5165\u8bb2\u89e3\uff1b\u7ed3\u6784\u6027\u6216\u91cd\u590d\u5143\u7d20\u53ea\u9700\u6eaf\u6e90\u6807\u8bb0\u3002\n"
        "2. \u4e25\u7981\u4f7f\u7528\u201c\u8fd9\u4e00\u9875\u201d\u3001\u201c\u672c\u9875\u201d\u3001\u201c\u5e7b\u706f\u7247\u201d\u3001\u201c\u4e0a\u4e00\u9875\u201d\u3001\u201c\u4e0b\u4e00\u9875\u201d\u3001\u201c\u8fd9\u5f20\u5e7b\u706f\u7247\u201d\u3001\u201c\u6b64\u9875\u201d\u3001\u201c\u8fd9\u9875\u201d\u7b49 PPT \u7ed3\u6784\u8868\u8ff0\uff1b\u76f4\u63a5\u8bb2\u77e5\u8bc6\uff0c\u4e0d\u8981\u63cf\u8ff0\u201c\u5e7b\u706f\u7247\u4e0a\u5c55\u793a\u4e86\u4ec0\u4e48\u201d\u6216\u201c\u8fd9\u4e00\u9875\u5728\u4e0a\u4e00\u9875\u7684\u57fa\u7840\u4e0a\u201d\u3002\n"
        "3. current_page.ordered_elements \u5df2\u6309\u7248\u9762\u987a\u5e8f\u7ed9\u51fa\uff1b\u56fe\u7247\u82e5\u5e26 anchor_element_ids\uff0c\u8981\u653e\u5728\u5bf9\u5e94\u6982\u5ff5\u9644\u8fd1\uff0c\u5e76\u7528 figure_explanation \u89e3\u91ca\u5b83\u8865\u5145\u4e86\u4ec0\u4e48\uff0c\u907f\u514d\u91cd\u590d\u6b63\u6587\u5b9a\u4e49\u3002\n"
        "4. \u5bf9\u6ca1\u6709\u89c6\u89c9\u6458\u8981\u7684\u56fe\u7247\uff0c\u53ea\u63d2\u5165\u56fe\u7247\uff0c\u4e0d\u731c\u6d4b\u3001\u4e0d\u9053\u6b49\u3001\u4e0d\u8bf4\u65e0\u6cd5\u89e3\u6790\u3002\n"
        f"5. {source_rule}\n"
        "6. \u56fe\u7247 alt \u6587\u672c\u4e0d\u80fd\u4e3a\u7a7a\uff0c\u5e94\u7528\u7b80\u77ed\u6587\u5b57\u8bf4\u660e\u56fe\u7247\u4e3b\u9898\u3002\n"
        "7. \u53ea\u80fd\u4f7f\u7528 ### \u6216\u66f4\u4f4e\u7ea7\u6807\u9898\uff0c\u4e25\u7981\u8f93\u51fa\u201c\u597d\u7684\uff0c\u8fd9\u662f\u201d\u201c\u6839\u636e JSON\u201d\u201c\u8bfe\u7a0b\u7b14\u8bb0\u5df2\u4e25\u683c\u9075\u5faa\u201d\u7b49\u5143\u53d9\u8ff0\u3002\n\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )


def _llm_weave_prompt(
    context,
    page_markdown_by_slide: dict[int, str],
    source_display: str,
    note_context: str,
    note_style: str,
    note_depth: str,
    note_language: str,
    term_policy: str,
    weave_dedup: str,
    deck_brief: dict[str, Any] | None = None,
) -> str:
    page_notes = [
        {
            "slide_id": page.slide_id,
            "title": page.title,
            "page_note_markdown": page_markdown_by_slide.get(page.slide_id, ""),
        }
        for page in context.pages
    ]
    payload = {
        "task": "weave_page_lectures",
        "context_id": context.id,
        "context_kind": note_context,
        "context_title": context.title,
        "slide_ids": [page.slide_id for page in context.pages],
        "page_notes": page_notes,
    }
    prompt_brief = _prompt_deck_brief(deck_brief, [page.slide_id for page in context.pages])
    if prompt_brief:
        payload["deck_brief"] = prompt_brief
    source_rule = _source_prompt_rule(source_display)
    depth_rule = _note_depth_rule(note_depth)
    language_rule = _language_prompt_rule(note_language)
    term_rule = _term_policy_prompt_rule(note_language, term_policy)
    style_rule = (
        "\u7b14\u8bb0\u4f18\u5148\uff1a\u6309\u6982\u5ff5\u548c\u63a8\u7406\u94fe\u7ec4\u7ec7\u5c0f\u8282\uff0c\u53ef\u4ee5\u6253\u6563 page_notes \u7684\u9010\u9875\u7ed3\u6784\uff1b\u5c01\u9762\u3001\u76ee\u5f55\u3001\u7ae0\u8282\u5bfc\u822a\u548c\u91cd\u590d\u5b57\u6bb5\u53ea\u4fdd\u7559\u9690\u85cf\u6765\u6e90\u6807\u8bb0\u5373\u53ef\u3002"
        if note_style == "article"
        else "\u4fdd\u771f\u4f18\u5148\uff1a\u5c3d\u91cf\u6cbf\u7740 page_notes \u7684\u987a\u5e8f\u7ec4\u7ec7\u5185\u5bb9\uff0c\u4f46\u4ecd\u8981\u5408\u5e76\u91cd\u590d\u6bb5\u843d\u5e76\u5220\u53bb\u7eaf\u7ed3\u6784\u6027\u8bf4\u660e\u3002"
    )
    dedup_rule = {
        "soft": "\u53ea\u5408\u5e76\u660e\u663e\u91cd\u590d\u7684\u53e5\u5b50\u548c\u5b8c\u5168\u76f8\u540c\u7684\u5b9a\u4e49\uff1b\u5b81\u53ef\u7565\u957f\uff0c\u4e5f\u4e0d\u8981\u5220\u6389 page_notes \u4e2d\u7684\u5173\u952e\u89e3\u91ca\u3002",
        "normal": "\u5408\u5e76\u91cd\u590d\u5b9a\u4e49\u548c\u76f8\u8fd1\u4f8b\u5b50\uff0c\u4f46\u4fdd\u7559\u6bcf\u9875\u7684\u5173\u952e\u77e5\u8bc6\u70b9\u3001\u56fe\u8868\u89e3\u91ca\u548c\u63a8\u7406\u6b65\u9aa4\u3002",
        "aggressive": "\u53ef\u4ee5\u66f4\u4e3b\u52a8\u5730\u538b\u7f29\u91cd\u590d\u5185\u5bb9\uff0c\u4f46\u4e0d\u5f97\u5220\u9664\u72ec\u6709\u7684\u5b9a\u4e49\u3001\u6761\u4ef6\u3001\u516c\u5f0f\u3001\u4f8b\u5b50\u548c\u56fe\u8868\u89e3\u91ca\u3002",
    }[weave_dedup]
    return (
        "\u8bf7\u628a\u4e0b\u9762\u8fd9\u4e9b\u9010\u9875\u8bb2\u89e3\u7f16\u7ec7\u6210\u4e00\u4e2a\u8fde\u8d2f\u7684 Markdown \u5b66\u4e60\u5c0f\u8282\u3002\n"
        "\u6ce8\u610f\uff1a\u4f60\u7684\u4efb\u52a1\u662f\u63d0\u70bc\u77e5\u8bc6\u7ebf\u7d22\u3001\u53bb\u91cd\u3001\u8865\u8fc7\u6e21\uff0c\u800c\u4e0d\u662f\u628a page_notes \u7684\u9010\u9875\u7ed3\u6784\u539f\u6837\u62fc\u63a5\uff0c\u4e5f\u4e0d\u662f\u91cd\u65b0\u6982\u62ec PPT\u3002\n"
        "If deck_brief is present, use it only for transitions and global structure; never compress page_notes into a deck_brief summary.\n"
        f"{style_rule}\n"
        f"{language_rule}\n"
        f"{term_rule}\n"
        f"{depth_rule}\n"
        f"\u53bb\u91cd\u7b56\u7565\uff1a{dedup_rule}\n"
        "\u786c\u6027\u8981\u6c42\uff1a\n"
        "1. \u4fdd\u7559 page_notes \u4e2d\u5df2\u7ecf\u5199\u51fa\u7684\u56fe\u7247 Markdown \u548c\u9690\u85cf\u6765\u6e90\u6807\u8bb0\u3002\n"
        "2. \u5bf9\u6b63\u6587\u53ef\u4ee5\u6309\u6982\u5ff5\u8c03\u6574\u987a\u5e8f\u3001\u5408\u5e76\u6bb5\u843d\u3001\u5220\u53bb\u7ed3\u6784\u6027\u8bf4\u660e\uff1b\u4e0d\u8981\u4e3a\u4e86\u4fdd\u7559\u6bcf\u4e2a source marker \u800c\u9010\u5b57\u6bb5\u590d\u8ff0\u3002\n"
        "3. \u5916\u5c42\u7ae0\u8282\u6807\u9898\u7531\u7cfb\u7edf\u7edf\u4e00\u6dfb\u52a0\uff1b\u6b63\u6587\u5c0f\u6807\u9898\u53ea\u80fd\u4f7f\u7528 ### \u6216\u66f4\u4f4e\u7ea7\u6807\u9898\uff0c\u4e0d\u8981\u8f93\u51fa\u5168\u6587 H1\uff0c\u4e5f\u4e0d\u8981\u7528\u201c\u8bfe\u7a0b\u7b14\u8bb0\u201d\u5f53\u6807\u9898\u3002\n"
        f"4. {source_rule}\n"
        "5. \u56fe\u7247 alt \u6587\u672c\u4e0d\u80fd\u4e3a\u7a7a\uff1b\u4e25\u7981\u8f93\u51fa\u201c\u597d\u7684\uff0c\u8fd9\u662f\u201d\u201c\u6839\u636e JSON\u201d\u201c\u7b14\u8bb0\u5df2\u4e25\u683c\u9075\u5faa\u201d\u201c\u65e0\u6cd5\u89c6\u89c9\u89e3\u6790\u201d\u7b49\u5143\u53d9\u8ff0\u3002\n"
        "6. \u4e25\u7981\u4f7f\u7528\u201c\u8fd9\u4e00\u9875\u201d\u201c\u672c\u9875\u201d\u201c\u5e7b\u706f\u7247\u201d\u201c\u4e0a\u4e00\u9875\u201d\u201c\u4e0b\u4e00\u9875\u201d\u201c\u6b64\u9875\u201d\u7b49 PPT \u9875\u9762\u7ed3\u6784\u8868\u8ff0\uff1b\u76f4\u63a5\u8bb2\u77e5\u8bc6\uff0c\u81ea\u7136\u8fc7\u6e21\u3002\n\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )


def _source_prompt_rule(source_display: str) -> str:
    return {
        "hidden": (
            "\u5728\u6bcf\u4e2a\u4e3b\u8981\u6bb5\u843d\u6216\u56fe\u7247\u540e\u5199 HTML \u9690\u85cf\u6765\u6e90\u6ce8\u91ca\uff0c\u683c\u5f0f\u4e25\u683c\u4e3a "
            "`<!-- slidenote-source: p4:s4_t1,s4_t2 -->`\uff1b\u4e0d\u8981\u5728\u53ef\u89c1\u6b63\u6587\u91cc\u5199\u5143\u7d20 ID\u3002"
        ),
        "footnote": (
            "\u6bcf\u4e2a\u4e3b\u8981\u6bb5\u843d\u672b\u5c3e\u53ea\u663e\u793a\u7b80\u6d01\u9875\u7801\uff0c\u4f8b\u5982 `\uff08PPT \u7b2c 4 \u9875\uff09`\uff0c\u5e76\u7ee7\u7eed\u9644\u52a0 "
            "`<!-- slidenote-source: p4:s4_t1,s4_t2 -->` \u9690\u85cf\u6765\u6e90\u6ce8\u91ca\u3002"
        ),
        "inline": "\u53ef\u4ee5\u5728\u6b63\u6587\u4e2d\u663e\u793a\u8be6\u7ec6\u6765\u6e90\u9875\u7801\u548c\u5143\u7d20 ID\uff0c\u540c\u65f6\u4e5f\u8981\u4fdd\u7559\u9690\u85cf\u6765\u6e90\u6ce8\u91ca\u3002",
    }[source_display]


def _language_prompt_rule(note_language: str) -> str:
    return {
        "zh": "\u8f93\u51fa\u8bed\u8a00\uff1a\u6b63\u6587\u3001\u6807\u9898\u548c\u89e3\u91ca\u5fc5\u987b\u4f7f\u7528\u7b80\u4f53\u4e2d\u6587\uff1b\u516c\u5f0f\u3001\u4ee3\u7801\u3001\u53d8\u91cf\u540d\u3001\u7f29\u5199\u548c\u539f\u6587\u4e13\u6709\u540d\u8bcd\u4e0d\u8981\u786c\u7ffb\u8bd1\u3002",
        "en": "Output language: write headings, prose, and explanations in English; preserve formulas, code, variable names, acronyms, and proper nouns.",
        "auto": "\u8f93\u51fa\u8bed\u8a00\uff1a\u6839\u636e\u8bfe\u7a0b\u6750\u6599\u7684\u4e3b\u4f53\u8bed\u8a00\u81ea\u52a8\u9009\u62e9\uff0c\u5e76\u4fdd\u6301\u5168\u6587\u4e00\u81f4\uff1b\u82e5\u6750\u6599\u660e\u663e\u6df7\u5408\uff0c\u4f18\u5148\u9009\u62e9\u66f4\u9002\u5408\u5b66\u4e60\u8005\u8fde\u7eed\u9605\u8bfb\u7684\u4e00\u79cd\u8bed\u8a00\u3002",
    }[note_language]


def _term_policy_prompt_rule(note_language: str, term_policy: str) -> str:
    if term_policy == "preserve":
        if note_language == "en":
            return "Term policy: preserve source academic terms, acronyms, protocol names, algorithm names, API names, and code symbols; explain them when useful instead of replacing them."
        return "\u672f\u8bed\u7b56\u7565\uff1a\u4fdd\u7559\u539f\u59cb\u5b66\u672f\u672f\u8bed\u3001\u82f1\u6587\u7f29\u5199\u3001\u534f\u8bae\u540d\u3001\u7b97\u6cd5\u540d\u3001API \u540d\u79f0\u548c\u4ee3\u7801\u7b26\u53f7\uff1b\u53ef\u4ee5\u89e3\u91ca\u542b\u4e49\uff0c\u4f46\u4e0d\u8981\u4e3a\u4e86\u7ffb\u8bd1\u800c\u66ff\u6362\u5b83\u4eec\u3002"
    if term_policy == "translate":
        if note_language == "en":
            return "Term policy: translate terms into natural English when it is safe; keep code, formulas, variables, APIs, protocol acronyms, and established names unchanged."
        return "\u672f\u8bed\u7b56\u7565\uff1a\u5728\u4e0d\u7834\u574f\u4e13\u4e1a\u51c6\u786e\u6027\u7684\u524d\u63d0\u4e0b\u5c3d\u91cf\u7ffb\u8bd1\u672f\u8bed\uff1b\u4ee3\u7801\u3001\u516c\u5f0f\u3001\u53d8\u91cf\u3001API\u3001\u534f\u8bae\u7f29\u5199\u548c\u516c\u8ba4\u82f1\u6587\u540d\u4fdd\u6301\u539f\u6837\u3002"
    if note_language == "zh":
        return "\u672f\u8bed\u7b56\u7565\uff1a\u5173\u952e\u5b66\u672f\u672f\u8bed\u9996\u6b21\u51fa\u73b0\u65f6\u5c3d\u91cf\u5199\u6210\u201c\u4e2d\u6587\u8bd1\u540d\uff08English term/acronym\uff09\u201d\uff0c\u540e\u6587\u53ef\u7528\u4e2d\u6587\u540d\u6216\u5e38\u7528\u7f29\u5199\uff1b\u4e0d\u8981\u7ffb\u8bd1\u4ee3\u7801\u3001API\u3001\u53d8\u91cf\u3001\u516c\u5f0f\u548c\u534f\u8bae\u7f29\u5199\u3002"
    if note_language == "en":
        return "Term policy: preserve English academic terms and acronyms; when a source term is Chinese and useful for traceability, first mention it as English term (\u4e2d\u6587\u539f\u6587)."
    return "\u672f\u8bed\u7b56\u7565\uff1a\u5173\u952e\u672f\u8bed\u9996\u6b21\u51fa\u73b0\u65f6\u5c3d\u91cf\u4fdd\u7559\u539f\u6587\u672f\u8bed\u5e76\u7ed9\u51fa\u5b66\u4e60\u8005\u76ee\u6807\u8bed\u8a00\u89e3\u91ca\uff1b\u4ee3\u7801\u3001API\u3001\u53d8\u91cf\u3001\u516c\u5f0f\u548c\u901a\u7528\u7f29\u5199\u4fdd\u6301\u539f\u6837\u3002"


def _note_depth_rule(note_depth: str) -> str:
    return {
        "concise": "\u8be6\u7ec6\u7a0b\u5ea6\uff1a\u7cbe\u70bc\u4f46\u4e0d\u80fd\u6f0f\u6389\u5173\u952e\u6982\u5ff5\u548c\u56fe\u8868\u7ed3\u8bba\u3002",
        "balanced": "\u8be6\u7ec6\u7a0b\u5ea6\uff1a\u4e2d\u7b49\u504f\u8be6\u7ec6\uff0c\u65e2\u8981\u987a\u7545\uff0c\u4e5f\u8981\u8bb2\u6e05\u4e3b\u8981\u6982\u5ff5\u3001\u4f8b\u5b50\u548c\u56fe\u8868\u542b\u4e49\u3002",
        "detailed": "\u8be6\u7ec6\u7a0b\u5ea6\uff1a\u5c3d\u91cf\u63a5\u8fd1\u5b66\u751f\u5355\u72ec\u95ee\u201c\u8bf7\u4f60\u8bb2\u8bb2\u8fd9\u4e00\u9875\u201d\u65f6\u7684\u6df1\u8bb2\u6548\u679c\uff0c\u89e3\u91ca\u672f\u8bed\u3001\u56e0\u679c\u5173\u7cfb\u3001\u4f8b\u5b50\u548c\u56fe\u8868\u7ec6\u8282\u3002",
    }[note_depth]


def _page_payload_for_prompt(
    page: SlidePage,
    asset_map: dict[str, str],
    supports_image_input: bool,
    screenshot_policy: str,
    source_type: str,
) -> dict[str, Any]:
    page_payload = asdict(page)
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
    page_payload["ordered_elements"] = ordered_page_elements(prompt_deck, page, asset_map=asset_map)
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
    if page.page_visual_summary:
        parts.append(page.page_visual_summary)
    text = re.sub(r"\s+", " ", " ".join(parts)).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "\u2026"


def _section_title_by_slide(deck: Deck, section_plan: dict[str, Any] | None = None) -> dict[int, str]:
    from .assembly import _section_contexts
    result: dict[int, str] = {}
    for context in _section_contexts(deck, section_plan=section_plan):
        for page in context.pages:
            result[page.slide_id] = context.title
    return result
