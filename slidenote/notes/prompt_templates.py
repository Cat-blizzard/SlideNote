from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from slidenote.content_guard import learning_items_for_page
from slidenote.models import Deck, SlidePage

from .assembly import NoteContext
from .prompt_payload import (
    _nearby_page_payloads,
    _page_payload_for_prompt,
    _prompt_brief_hash,
    _prompt_deck_brief,
    _prompt_slide_scope,
)
from .prompt_rules import (
    _language_prompt_rule,
    _note_depth_rule,
    _note_profile_prompt_rule,
    _source_prompt_rule,
    _term_policy_prompt_rule,
)


def _llm_page_prompt(page: SlidePage, supports_image_input: bool = False) -> str:
    context = NoteContext(id=f"p{page.slide_id}", kind="page", title=page.title or f"\u7b2c {page.slide_id} \u9875", pages=[page])
    return _llm_context_prompt(
        context,
        supports_image_input=supports_image_input,
        asset_map={},
        source_display="hidden",
        note_context="page",
        note_style="article",
        note_profile="auto",
        note_depth="detailed",
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
    note_depth: str,
    note_language: str,
    term_policy: str,
    screenshot_policy: str,
    figure_placement: str,
    source_type: str,
    deck_brief: dict[str, Any] | None = None,
    content_guard: dict[str, Any] | None = None,
    note_profile: str = "auto",
) -> str:
    prompt_brief = _prompt_deck_brief(deck_brief, [page.slide_id for page in context.pages])
    payload = {
        "context_id": context.id,
        "context_kind": context.kind,
        "context_title": context.title,
        "figure_placement": figure_placement,
        "pages": [
            _page_payload_for_prompt(
                page,
                asset_map,
                supports_image_input,
                screenshot_policy,
                source_type=source_type,
                content_guard=content_guard,
            )
            for page in context.pages
        ],
    }
    if content_guard:
        payload["content_guard"] = {
            "required_confidence_threshold": content_guard.get("required_confidence_threshold"),
            "rule": "learning_items marked must_explain=true and confidence>=threshold must be explained in visible prose, not only hidden source markers.",
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
    style_depth_rule = (
        "\u91cd\u8981\uff1aarticle \u4e0d\u662f\u6458\u8981\u6a21\u5f0f\uff0c\u53ea\u6539\u53d8\u7ec4\u7ec7\u65b9\u5f0f\uff0c\u4e0d\u964d\u4f4e\u8bb2\u89e3\u6df1\u5ea6\uff1b\u5f53 note_depth=detailed \u65f6\uff0c\u9ed8\u8ba4\u5199\u6210\u8be6\u7ec6\u8bb2\u4e49\u5f0f\u5b66\u4e60\u7b14\u8bb0\uff0c\u4e0d\u8981\u628a\u6982\u5ff5\u3001\u516c\u5f0f\u3001\u4f8b\u5b50\u3001\u6761\u4ef6\u6216\u56fe\u8868\u7ed3\u8bba\u538b\u6210\u4e00\u53e5\u8bdd\u3002"
        if note_style == "article"
        else "\u91cd\u8981\uff1afaithful \u4f18\u5148\u4fdd\u7559 PPT \u987a\u5e8f\uff1b\u8bb2\u89e3\u8be6\u7ec6\u7a0b\u5ea6\u4ecd\u7531 note_depth \u63a7\u5236\u3002"
    )
    structural_rule = (
        "\u5c01\u9762\u3001\u76ee\u5f55\u3001\u7ae0\u8282\u5bfc\u822a\u3001\u8bb2\u5e08\u4fe1\u606f\u3001\u8054\u7cfb\u65b9\u5f0f\u548c\u7eaf\u7248\u5f0f\u5143\u7d20\u4e0d\u8981\u5c55\u5f00\u6210\u6b63\u6587\uff1b"
        "\u8fd9\u4e9b\u5143\u7d20\u9700\u8986\u76d6\u65f6\uff0c\u53ea\u4fdd\u7559\u9690\u85cf\u6765\u6e90\u6807\u8bb0\uff0c\u6216\u628a\u5fc5\u8981\u80cc\u666f\u81ea\u7136\u5e76\u5165\u540e\u7eed\u77e5\u8bc6\u6bb5\u843d\u3002"
        "\u4e0d\u5f97\u628a\u542b\u6709\u72ec\u7acb\u77e5\u8bc6\u589e\u91cf\u7684\u5143\u7d20\u4ec5\u4f5c\u4e3a source marker \u5904\u7406\uff1b"
        "\u53ea\u5141\u8bb8\u7ed3\u6784\u6027\u3001\u91cd\u590d\u6027\u3001\u88c5\u9970\u6027\u548c\u8054\u7cfb\u65b9\u5f0f\u7c7b\u5143\u7d20\u4ec5\u4fdd\u7559\u6765\u6e90\u6807\u8bb0\u3002"
    )
    table_rule = (
        "\u5982\u679c\u8868\u683c JSON \u91cc\u6709 table_summary\u3001table_conclusion \u6216 key_rows\uff0c"
        "\u4f18\u5148\u628a\u8fd9\u4e9b\u8868\u683c\u7ed3\u8bba\u548c\u5173\u952e\u884c\u81ea\u7136\u878d\u5165\u77e5\u8bc6\u8bb2\u89e3\uff1b"
        "\u4e0d\u8981\u628a rows \u9010\u5355\u5143\u683c\u7ffb\u8bd1\u6210\u6e05\u5355\uff0c\u9664\u975e\u8868\u683c\u672c\u8eab\u5c31\u662f\u9700\u4fdd\u7559\u7684\u5bf9\u7167\u8868\u3002"
    )
    semantic_rule = (
        "\u5982\u679c JSON \u91cc\u6709 semantic_layout.groups/relations\uff0c\u8bf7\u6309\u7ec4\u7ea7\u201c\u6559\u5b66\u573a\u666f\u201d\u8bb2\u89e3\uff1a"
        "\u628a\u540c\u4e00 group \u91cc\u7684\u4ee3\u7801\u3001\u8fd0\u884c\u7ed3\u679c\u3001\u84dd\u6846\u89e3\u91ca\u3001\u7ea2\u5b57\u6ce8\u91ca\u548c\u7bad\u5934\u56e0\u679c\u5408\u5e76\u6210\u4e00\u6bb5\u81ea\u7136\u8bb2\u89e3\uff1b"
        "\u4e0d\u8981\u628a\u5c0f\u5757\u5206\u522b\u5199\u6210\u5b64\u7acb\u56fe\u7247\u6216 OCR \u6e05\u5355\u3002"
    )
    text_style_rule = (
        "\u5982\u679c text_blocks \u4e2d\u6709 style_runs/color\uff0c\u8fd9\u4e9b\u662f\u5df2\u63d0\u53d6\u51fa\u6765\u7684\u6587\u5b57\u6837\u5f0f\uff0c"
        "\u4e0d\u8981\u628a\u5b83\u4eec\u5f53\u6210\u56fe\u7247\uff1b\u9700\u8981\u4fdd\u7559\u539f\u59cb\u5f3a\u8c03\u65f6\uff0c\u53ef\u4f7f\u7528 "
        "`<span style=\"color:#C00000\">...</span>` \u8fd9\u7c7b Markdown \u5185\u5d4c HTML \u8868\u793a\u7ea2\u5b57\uff0c\u9ed1\u5b57\u7528\u9ed8\u8ba4\u6b63\u6587\u6837\u5f0f\u5373\u53ef\u3002"
    )
    language_rule = _language_prompt_rule(note_language)
    term_rule = _term_policy_prompt_rule(note_language, term_policy)
    profile_rule = _note_profile_prompt_rule(note_profile)
    depth_rule = _note_depth_rule(note_depth)
    image_rule = (
        "\u56fe\u7247\u5fc5\u987b\u7528\u771f\u6b63\u7684 Markdown \u56fe\u7247\u8bed\u6cd5\u63d2\u5165\uff0c\u4e0d\u80fd\u653e\u8fdb\u53cd\u5f15\u53f7\u3002"
        "\u56fe\u7247 alt \u6587\u672c\u4e0d\u80fd\u4e3a\u7a7a\uff0c\u5e94\u7528\u7b80\u77ed\u6587\u5b57\u8bf4\u660e\u56fe\u7247\u4e3b\u9898\u3002"
        "\u5982\u679c JSON \u91cc\u6709 ocr_text\u3001visual_summary\u3001page_ocr_text \u6216 page_visual_summary\uff0c\u8981\u628a\u5b83\u4eec\u548c\u76f8\u5173\u6982\u5ff5\u5408\u5e76\u8bb2\u89e3\u3002"
        "\u5982\u679c\u56fe\u7247 role \u662f composite_figure\uff0c\u5b83\u4ee3\u8868\u7531\u591a\u4e2a\u5d4c\u5165\u5c0f\u56fe\u7ec4\u6210\u7684\u6574\u4f53\u6d41\u7a0b\u56fe\u6216\u7ed3\u6784\u56fe\uff1b\u8bf7\u56f4\u7ed5\u6574\u4f53\u56fe\u89e3\u91ca\uff0c\u4e0d\u8981\u5206\u522b\u63cf\u8ff0\u5176\u5b50\u56fe\u7247\u3002"
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
        f"{profile_rule + chr(10) if profile_rule else ''}"
        f"{style_rule}\n"
        f"{style_depth_rule}\n"
        f"{structural_rule}\n"
        f"{table_rule}\n"
        f"{semantic_rule}\n"
        f"{text_style_rule}\n"
        f"{language_rule}\n"
        f"{term_rule}\n"
        f"{depth_rule}\n"
        "\u786c\u6027\u8981\u6c42\uff1a\n"
        "1. \u5148\u5224\u65ad\u54ea\u4e9b\u662f\u5b66\u751f\u771f\u6b63\u9700\u8981\u7406\u89e3\u7684\u77e5\u8bc6\u70b9\uff0c\u518d\u8bb2\u6e05\u5b83\u4eec\u7684\u542b\u4e49\u3001\u56e0\u679c\u3001\u4f8b\u5b50\u3001\u516c\u5f0f\u548c\u56fe\u8868\u7ed3\u8bba\u3002\n"
        "   如果 JSON 中有 learning_items，must_explain=true 且 confidence>=required_confidence_threshold 的元素必须进入可见正文；结构性、重复性或装饰性元素只保留 source marker 即可。\n"
        "2. \u5408\u5e76\u91cd\u590d bullet\u3001\u8868\u683c\u58f3\u3001OCR \u91cd\u590d\u6587\u672c\u548c\u9875\u9762\u5b57\u6bb5\uff1b\u8868\u683c\u8981\u5148\u8bb2 table_conclusion/key_rows \u80cc\u540e\u7684\u610f\u4e49\uff0c\u4e0d\u8981\u628a PPT \u5b57\u6bb5\u987a\u5e8f\u6539\u5199\u6210\u4e00\u4e32\u7ffb\u8bd1\u3002\n"
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
    content_guard: dict[str, Any] | None = None,
    note_profile: str = "auto",
) -> str:
    page = context.pages[0]
    prompt_brief = _prompt_deck_brief(deck_brief, _prompt_slide_scope(deck, page.slide_id, page_neighborhood))
    payload = {
        "task": "page_lecture",
        "context_id": context.id,
        "source_file": Path(deck.source_path).stem,
        "section_title": section_title,
        "figure_placement": figure_placement,
        "current_page": _page_payload_for_prompt(page, asset_map, supports_image_input, screenshot_policy, source_type=source_type, content_guard=content_guard),
        "nearby_pages": _nearby_page_payloads(deck, page.slide_id, page_neighborhood),
    }
    if content_guard:
        payload["content_guard"] = {
            "required_confidence_threshold": content_guard.get("required_confidence_threshold"),
            "rule": "must_explain learning_items at or above the threshold require visible explanation.",
        }
    if prompt_brief:
        payload["deck_brief"] = prompt_brief
    depth_rule = _note_depth_rule(note_depth)
    profile_rule = _note_profile_prompt_rule(note_profile)
    source_rule = _source_prompt_rule(source_display)
    language_rule = _language_prompt_rule(note_language)
    term_rule = _term_policy_prompt_rule(note_language, term_policy)
    style_rule = (
        "\u8bf7\u628a current_page \u63d0\u70bc\u6210\u50cf\u5b66\u751f\u8bfe\u540e\u7b14\u8bb0\u4e00\u6837\u7684\u77e5\u8bc6\u5355\u5143\uff1a\u76f4\u63a5\u8bb2\u6e05\u6982\u5ff5\u3001\u56e0\u679c\u3001\u6761\u4ef6\u3001\u4f8b\u5b50\u3001\u516c\u5f0f\u548c\u56fe\u8868\u7ed3\u8bba\uff0c\u5408\u5e76\u91cd\u590d bullet/table/OCR\uff0c\u4e0d\u8981\u9010\u5b57\u6bb5\u7ffb\u8bd1 PPT\u3002"
        if note_style == "article"
        else "\u8bf7\u5c3d\u91cf\u4fdd\u7559 current_page \u539f\u59cb\u6761\u76ee\u987a\u5e8f\uff0c\u4f46\u4ecd\u8981\u5199\u6210\u53ef\u4ee5\u76f4\u63a5\u9605\u8bfb\u7684\u77e5\u8bc6\u70b9\u8bb2\u89e3\uff0c\u800c\u4e0d\u662f\u590d\u5236\u6e05\u5355\u6216\u63cf\u8ff0\u9875\u9762\u6784\u6210\u3002"
    )
    style_depth_rule = (
        "\u91cd\u8981\uff1aarticle \u4e0d\u662f\u6458\u8981\u6a21\u5f0f\uff0c\u53ea\u6539\u53d8\u7ec4\u7ec7\u65b9\u5f0f\uff0c\u4e0d\u964d\u4f4e\u8bb2\u89e3\u6df1\u5ea6\uff1b\u5f53 note_depth=detailed \u65f6\uff0c\u9ed8\u8ba4\u5199\u6210\u8be6\u7ec6\u8bb2\u4e49\u5f0f\u5b66\u4e60\u7b14\u8bb0\uff0c\u4e0d\u8981\u628a\u6982\u5ff5\u3001\u516c\u5f0f\u3001\u4f8b\u5b50\u3001\u6761\u4ef6\u6216\u56fe\u8868\u7ed3\u8bba\u538b\u6210\u4e00\u53e5\u8bdd\u3002"
        if note_style == "article"
        else "\u91cd\u8981\uff1afaithful \u4f18\u5148\u4fdd\u7559 PPT \u987a\u5e8f\uff1b\u8bb2\u89e3\u8be6\u7ec6\u7a0b\u5ea6\u4ecd\u7531 note_depth \u63a7\u5236\u3002"
    )
    structural_rule = (
        "\u5982\u679c current_page \u4e3b\u8981\u662f\u5c01\u9762\u3001\u76ee\u5f55\u3001\u7ae0\u8282\u5bfc\u822a\u3001\u8bb2\u5e08/\u8054\u7cfb\u65b9\u5f0f\u6216\u5176\u4ed6\u975e\u5b66\u4e60\u5185\u5bb9\uff0c"
        "\u4e0d\u8981\u5199\u6210\u6b63\u6587\u89e3\u8bf4\uff1b\u53ea\u4fdd\u7559\u8be5\u9875\u6240\u6709\u5143\u7d20\u7684\u6765\u6e90\u6807\u8bb0\uff0c\u4f9b\u6700\u7ec8\u7b14\u8bb0\u6eaf\u6e90\u4f7f\u7528\u3002"
        "\u4e0d\u5f97\u628a\u542b\u6709\u72ec\u7acb\u77e5\u8bc6\u589e\u91cf\u7684\u5143\u7d20\u4ec5\u4f5c\u4e3a source marker \u5904\u7406\uff1b"
        "\u53ea\u5141\u8bb8\u7ed3\u6784\u6027\u3001\u91cd\u590d\u6027\u3001\u88c5\u9970\u6027\u548c\u8054\u7cfb\u65b9\u5f0f\u7c7b\u5143\u7d20\u4ec5\u4fdd\u7559\u6765\u6e90\u6807\u8bb0\u3002"
    )
    table_rule = (
        "\u8868\u683c\u5df2\u7ecf\u9884\u5904\u7406\u51fa table_summary/table_conclusion/key_rows \u65f6\uff0c"
        "\u8981\u4ee5\u8fd9\u4e9b\u7ed3\u8bba\u4e3a\u4e3b\u6765\u8bb2\u8868\u683c\u7684\u77e5\u8bc6\u542b\u4e49\uff1b"
        "\u907f\u514d\u628a rows \u91cc\u7684\u6bcf\u4e2a\u5355\u5143\u683c\u673a\u68b0\u6539\u5199\u6210\u6b63\u6587\u6e05\u5355\u3002"
    )
    semantic_rule = (
        "\u82e5 current_page.semantic_layout \u7ed9\u51fa scene_type/relations\uff0c\u4f18\u5148\u6309 group \u8bb2\u77e5\u8bc6\uff1a"
        "\u4ee3\u7801\u3001\u8f93\u51fa\u6846\u3001\u56e0\u679c\u89e3\u91ca\u548c\u4fee\u590d\u63d0\u793a\u5e94\u5408\u5e76\u4e3a\u540c\u4e00\u6559\u5b66\u573a\u666f\uff0c\u4e0d\u8981\u9010\u4e2a\u5c0f\u5757\u63cf\u8ff0\u7248\u9762\u3002"
    )
    text_style_rule = (
        "\u82e5 current_page.text_blocks \u542b style_runs/color\uff0c\u8fd9\u662f\u5df2\u62bd\u53d6\u7684\u5f69\u8272\u6587\u5b57\uff0c\u4e0d\u662f\u56fe\u7247\uff1b"
        "\u9700\u8981\u4fdd\u7559\u7ea2\u5b57\u5f3a\u8c03\u65f6\u7528 `<span style=\"color:#C00000\">...</span>`\uff0c\u9ed1\u5b57\u7528\u666e\u901a\u6b63\u6587\u3002"
    )
    return (
        "\u8bf7\u53ea\u8bb2\u89e3 JSON \u4e2d\u7684 current_page \u5305\u542b\u7684\u77e5\u8bc6\u5185\u5bb9\uff0c\u4e0d\u8981\u66ff\u5176\u4ed6\u9875\u9762\u5199\u6b63\u6587\u3002\n"
        "nearby_pages \u53ea\u7528\u4e8e\u7406\u89e3\u524d\u540e\u903b\u8f91\u548c\u51cf\u5c11\u91cd\u590d\uff0c\u4e0d\u80fd\u628a\u90bb\u8fd1\u9875\u5185\u5bb9\u5f53\u4f5c current_page \u7684\u5185\u5bb9\u5c55\u5f00\u3002\n"
        "If deck_brief is present, it is only a navigation map: current_page is the only source for the body; do not omit current_page elements because of the brief; do not pull later-page content into this page.\n"
        f"{profile_rule + chr(10) if profile_rule else ''}"
        f"{style_rule}\n"
        f"{style_depth_rule}\n"
        f"{structural_rule}\n"
        f"{table_rule}\n"
        f"{semantic_rule}\n"
        f"{text_style_rule}\n"
        f"{language_rule}\n"
        f"{term_rule}\n"
        f"{depth_rule}\n"
        "\u786c\u6027\u8981\u6c42\uff1a\n"
        "1. \u91cd\u8981\u5b9a\u4e49\u3001\u6761\u4ef6\u3001\u4f8b\u5b50\u3001\u516c\u5f0f\u3001\u8868\u683c\u7ed3\u8bba\u3001\u56fe\u7247\u3001OCR \u548c\u89c6\u89c9\u6458\u8981\u82e5\u652f\u6491\u5f53\u524d\u77e5\u8bc6\u70b9\uff0c\u8981\u878d\u5165\u8bb2\u89e3\uff1b\u7ed3\u6784\u6027\u6216\u91cd\u590d\u5143\u7d20\u53ea\u9700\u6eaf\u6e90\u6807\u8bb0\u3002\n"
        "   current_page.learning_items 中 must_explain=true 且 confidence>=content_guard.required_confidence_threshold 的元素，必须有可见正文解释，不能只出现在 HTML source marker 里。\n"
        "2. \u4e25\u7981\u4f7f\u7528\u201c\u8fd9\u4e00\u9875\u201d\u3001\u201c\u672c\u9875\u201d\u3001\u201c\u5e7b\u706f\u7247\u201d\u3001\u201c\u4e0a\u4e00\u9875\u201d\u3001\u201c\u4e0b\u4e00\u9875\u201d\u3001\u201c\u8fd9\u5f20\u5e7b\u706f\u7247\u201d\u3001\u201c\u6b64\u9875\u201d\u3001\u201c\u8fd9\u9875\u201d\u7b49 PPT \u7ed3\u6784\u8868\u8ff0\uff1b\u76f4\u63a5\u8bb2\u77e5\u8bc6\uff0c\u4e0d\u8981\u63cf\u8ff0\u201c\u5e7b\u706f\u7247\u4e0a\u5c55\u793a\u4e86\u4ec0\u4e48\u201d\u6216\u201c\u8fd9\u4e00\u9875\u5728\u4e0a\u4e00\u9875\u7684\u57fa\u7840\u4e0a\u201d\u3002\n"
        "3. current_page.ordered_elements \u5df2\u6309\u7248\u9762\u987a\u5e8f\u7ed9\u51fa\uff1b\u56fe\u7247\u82e5\u5e26 anchor_element_ids\uff0c\u8981\u653e\u5728\u5bf9\u5e94\u6982\u5ff5\u9644\u8fd1\uff0c\u5e76\u7528 figure_explanation \u89e3\u91ca\u5b83\u8865\u5145\u4e86\u4ec0\u4e48\uff0c\u907f\u514d\u91cd\u590d\u6b63\u6587\u5b9a\u4e49\u3002\n"
        "4. \u5bf9 role=composite_figure \u7684\u56fe\u7247\uff0c\u628a\u5b83\u5f53\u4f5c\u5b8c\u6574\u6d41\u7a0b\u56fe\u6216\u7ed3\u6784\u56fe\u63d2\u5165\u548c\u8bb2\u89e3\uff1b\u4e0d\u8981\u518d\u628a role=composite_child \u7684\u5c0f\u56fe\u5355\u72ec\u5199\u6210\u6b63\u6587\u3002\n"
        "5. \u5bf9\u6ca1\u6709\u89c6\u89c9\u6458\u8981\u7684\u56fe\u7247\uff0c\u53ea\u63d2\u5165\u56fe\u7247\uff0c\u4e0d\u731c\u6d4b\u3001\u4e0d\u9053\u6b49\u3001\u4e0d\u8bf4\u65e0\u6cd5\u89e3\u6790\u3002\n"
        f"6. {source_rule}\n"
        "7. \u56fe\u7247 alt \u6587\u672c\u4e0d\u80fd\u4e3a\u7a7a\uff0c\u5e94\u7528\u7b80\u77ed\u6587\u5b57\u8bf4\u660e\u56fe\u7247\u4e3b\u9898\u3002\n"
        "8. \u53ea\u80fd\u4f7f\u7528 ### \u6216\u66f4\u4f4e\u7ea7\u6807\u9898\uff0c\u4e25\u7981\u8f93\u51fa\u201c\u597d\u7684\uff0c\u8fd9\u662f\u201d\u201c\u6839\u636e JSON\u201d\u201c\u8bfe\u7a0b\u7b14\u8bb0\u5df2\u4e25\u683c\u9075\u5faa\u201d\u7b49\u5143\u53d9\u8ff0\u3002\n\n"
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
    content_guard: dict[str, Any] | None = None,
    note_profile: str = "auto",
) -> str:
    page_notes = [
        {
            "slide_id": page.slide_id,
            "title": page.title,
            "page_note_markdown": page_markdown_by_slide.get(page.slide_id, ""),
            "learning_items": learning_items_for_page(content_guard, page.slide_id) if content_guard else [],
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
    if content_guard:
        payload["content_guard"] = {
            "required_confidence_threshold": content_guard.get("required_confidence_threshold"),
            "rule": "Do not drop visible explanations for required learning_items while weaving.",
        }
    source_rule = _source_prompt_rule(source_display)
    depth_rule = _note_depth_rule(note_depth)
    profile_rule = _note_profile_prompt_rule(note_profile)
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
        f"{profile_rule + chr(10) if profile_rule else ''}"
        f"{style_rule}\n"
        f"{language_rule}\n"
        f"{term_rule}\n"
        f"{depth_rule}\n"
        f"\u53bb\u91cd\u7b56\u7565\uff1a{dedup_rule}\n"
        "\u786c\u6027\u8981\u6c42\uff1a\n"
        "1. \u4fdd\u7559 page_notes \u4e2d\u5df2\u7ecf\u5199\u51fa\u7684\u56fe\u7247 Markdown \u548c\u9690\u85cf\u6765\u6e90\u6807\u8bb0\u3002\n"
        "2. \u5bf9\u6b63\u6587\u53ef\u4ee5\u6309\u6982\u5ff5\u8c03\u6574\u987a\u5e8f\u3001\u5408\u5e76\u6bb5\u843d\u3001\u5220\u53bb\u7ed3\u6784\u6027\u8bf4\u660e\uff1b\u4e0d\u8981\u4e3a\u4e86\u4fdd\u7559\u6bcf\u4e2a source marker \u800c\u9010\u5b57\u6bb5\u590d\u8ff0\u3002page_notes.learning_items 中 must_explain=true 且达到阈值的内容不能在 weave 时被删掉。\n"
        "3. \u5916\u5c42\u7ae0\u8282\u6807\u9898\u7531\u7cfb\u7edf\u7edf\u4e00\u6dfb\u52a0\uff1b\u6b63\u6587\u5c0f\u6807\u9898\u53ea\u80fd\u4f7f\u7528 ### \u6216\u66f4\u4f4e\u7ea7\u6807\u9898\uff0c\u4e0d\u8981\u8f93\u51fa\u5168\u6587 H1\uff0c\u4e5f\u4e0d\u8981\u7528\u201c\u8bfe\u7a0b\u7b14\u8bb0\u201d\u5f53\u6807\u9898\u3002\n"
        f"4. {source_rule}\n"
        "5. \u56fe\u7247 alt \u6587\u672c\u4e0d\u80fd\u4e3a\u7a7a\uff1b\u4e25\u7981\u8f93\u51fa\u201c\u597d\u7684\uff0c\u8fd9\u662f\u201d\u201c\u6839\u636e JSON\u201d\u201c\u7b14\u8bb0\u5df2\u4e25\u683c\u9075\u5faa\u201d\u201c\u65e0\u6cd5\u89c6\u89c9\u89e3\u6790\u201d\u7b49\u5143\u53d9\u8ff0\u3002\n"
        "6. \u4e25\u7981\u4f7f\u7528\u201c\u8fd9\u4e00\u9875\u201d\u201c\u672c\u9875\u201d\u201c\u5e7b\u706f\u7247\u201d\u201c\u4e0a\u4e00\u9875\u201d\u201c\u4e0b\u4e00\u9875\u201d\u201c\u6b64\u9875\u201d\u7b49 PPT \u9875\u9762\u7ed3\u6784\u8868\u8ff0\uff1b\u76f4\u63a5\u8bb2\u77e5\u8bc6\uff0c\u81ea\u7136\u8fc7\u6e21\u3002\n\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )


def _llm_teaching_enrichment_prompt(
    context,
    woven_markdown: str,
    page_markdown_by_slide: dict[int, str],
    source_display: str,
    note_context: str,
    note_profile: str,
    note_depth: str,
    note_language: str,
    term_policy: str,
    deck_brief: dict[str, Any] | None = None,
    content_guard: dict[str, Any] | None = None,
) -> str:
    page_notes = [
        {
            "slide_id": page.slide_id,
            "title": page.title,
            "page_note_markdown": page_markdown_by_slide.get(page.slide_id, ""),
            "learning_items": learning_items_for_page(content_guard, page.slide_id) if content_guard else [],
        }
        for page in context.pages
    ]
    payload = {
        "task": "teaching_enrichment",
        "context_id": context.id,
        "context_kind": note_context,
        "context_title": context.title,
        "slide_ids": [page.slide_id for page in context.pages],
        "current_markdown": woven_markdown,
        "page_notes": page_notes,
    }
    prompt_brief = _prompt_deck_brief(deck_brief, [page.slide_id for page in context.pages])
    if prompt_brief:
        payload["deck_brief"] = prompt_brief
    if content_guard:
        payload["content_guard"] = {
            "required_confidence_threshold": content_guard.get("required_confidence_threshold"),
            "rule": "Keep required learning_items visible; this pass enriches teaching quality and must not delete required explanations.",
        }
    source_rule = _source_prompt_rule(source_display)
    language_rule = _language_prompt_rule(note_language)
    term_rule = _term_policy_prompt_rule(note_language, term_policy)
    depth_rule = _note_depth_rule(note_depth)
    profile_rule = _note_profile_prompt_rule(note_profile)
    return (
        "\u8bf7\u628a current_markdown \u4fee\u8ba2\u6210\u66f4\u50cf\u8001\u5e08\u91cd\u65b0\u8bb2\u4e00\u904d\u7684 Markdown \u8bb2\u4e49\u5c0f\u8282\u3002\n"
        "\u4f60\u4e0d\u662f\u5728\u603b\u7ed3\u5e7b\u706f\u7247\uff0c\u800c\u662f\u5728\u505a\u6559\u5b66\u91cd\u6784\uff1a\u5148\u7406\u89e3\u672c\u8282\u5171\u540c\u89e3\u51b3\u4ec0\u4e48\u95ee\u9898\uff0c\u518d\u7528\u5b66\u751f\u80fd\u8ddf\u4e0a\u7684\u987a\u5e8f\u8bb2\u6e05\u695a\u3002\n"
        f"{profile_rule + chr(10) if profile_rule else ''}"
        f"{language_rule}\n"
        f"{term_rule}\n"
        f"{depth_rule}\n"
        "\u53ef\u4f7f\u7528\u7684\u8bb2\u4e49\u7ed3\u6784\uff1a### \u672c\u8282\u6838\u5fc3\u95ee\u9898\u3001### \u80cc\u666f\u4e0e\u76f4\u89c9\u3001### \u8be6\u7ec6\u8bb2\u89e3\u3001### \u56fe\u8868/\u516c\u5f0f\u89e3\u8bfb\u3001### \u6613\u9519\u70b9\u3001### \u672c\u8282\u5c0f\u7ed3\u3001### \u81ea\u6d4b\u95ee\u9898\u3002\u5982\u67d0\u9879\u786e\u5b9e\u4e0d\u9002\u7528\uff0c\u53ef\u5408\u5e76\u5230\u76f8\u90bb\u5c0f\u8282\u3002\n"
        "\u786c\u6027\u8981\u6c42\uff1a\n"
        "1. \u4fdd\u7559 current_markdown \u4e2d\u5df2\u6709\u7684 Markdown \u56fe\u7247\u94fe\u63a5\u548c HTML source marker\uff1b\u65b0\u589e\u6216\u79fb\u52a8\u6bb5\u843d\u65f6\u4e5f\u8981\u4fdd\u7559\u5bf9\u5e94 source marker\u3002\n"
        "2. \u4e0d\u8981\u628a\u7ae0\u8282\u6539\u56de\u201c\u7b2c 1 \u9875\u8bb2 A\u3001\u7b2c 2 \u9875\u8bb2 B\u201d\u7684\u6e05\u5355\uff1bcoverage \u53ea\u662f\u6700\u540e\u8d28\u68c0\uff0c\u4e0d\u662f\u6b63\u6587\u6a21\u677f\u3002\n"
        "3. \u5bf9\u6838\u5fc3\u6982\u5ff5\uff0c\u89e3\u91ca\u5b83\u662f\u4ec0\u4e48\u3001\u4e3a\u4ec0\u4e48\u91cd\u8981\u3001\u5982\u4f55\u8fd0\u4f5c\u3001\u4e0e\u524d\u540e\u5185\u5bb9\u7684\u5173\u7cfb\u3002\n"
        "4. \u5bf9\u516c\u5f0f\u3001\u56fe\u8868\u3001\u6d41\u7a0b\u56fe\u3001\u622a\u56fe\uff0c\u4e0d\u8981\u53ea\u5199\u201c\u56fe\u4e2d\u5c55\u793a\u4e86\u201d\uff0c\u8981\u8bf4\u660e\u5b83\u652f\u6491\u4e86\u54ea\u4e2a\u6982\u5ff5\u6216\u63a8\u7406\u6b65\u9aa4\u3002\n"
        "5. \u53ef\u4ee5\u8865\u5145\u5fc5\u8981\u7684\u901a\u7528\u80cc\u666f\u3001\u76f4\u89c9\u89e3\u91ca\u3001\u7b80\u77ed\u4f8b\u5b50\u6216\u7c7b\u6bd4\uff0c\u4f46\u4e0d\u5f97\u65b0\u589e\u8bfe\u4ef6\u6ca1\u6709\u4f9d\u636e\u7684\u5177\u4f53\u6570\u5b57\u3001\u5b9e\u9a8c\u7ed3\u679c\u3001\u4f5c\u8005\u89c2\u70b9\u6216\u7ed3\u8bba\u3002\u901a\u7528\u80cc\u666f\u8981\u7528\u201c\u4e3a\u4e86\u5e2e\u52a9\u7406\u89e3\u201d\u8fd9\u7c7b\u8bed\u6c14\u6807\u660e\u3002\n"
        f"6. {source_rule}\n\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )


def _llm_repair_prompt(
    markdown: str,
    missing_items: list[dict[str, Any]],
    source_display: str,
    stage: str,
    note_language: str,
    term_policy: str,
) -> str:
    payload = {
        "task": "repair_required_learning_coverage",
        "stage": stage,
        "current_markdown": markdown,
        "missing_learning_items": missing_items,
    }
    source_rule = _source_prompt_rule(source_display)
    language_rule = _language_prompt_rule(note_language)
    term_rule = _term_policy_prompt_rule(note_language, term_policy)
    return (
        "请修订 current_markdown，使 missing_learning_items 中的关键学习内容自然进入可见正文。\n"
        "只输出修订后的完整 Markdown，不要解释你的修改，不要输出 JSON，不要追加“遗漏列表”。\n"
        "保持学习笔记风格：把缺失内容融入已有段落或相邻段落，必要时可增加一小段自然讲解；不要改成逐字段清单。\n"
        "必须保留已有的 Markdown 图片链接和 HTML source marker；新增讲解时也要保留或补上对应 source marker。\n"
        f"{source_rule}\n"
        f"{language_rule}\n"
        f"{term_rule}\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )
