from __future__ import annotations

import re
from typing import Any

from slidenote.llm_cache import utc_now_iso
from slidenote.models import Deck, ImageAsset, SlidePage, TableBlock, TextBlock
from slidenote.table_understanding import table_preview


def enrich_deck_with_semantic_layout(deck: Deck) -> dict[str, Any]:
    pages: list[dict[str, Any]] = []
    blocks_total = 0
    groups_total = 0
    relations_total = 0
    must_explain_total = 0

    for page in deck.pages:
        result = analyze_page_semantic_layout(deck, page)
        page.semantic_blocks = result["blocks"]
        page.semantic_groups = result["groups"]
        page.semantic_relations = result["relations"]
        blocks_total += len(page.semantic_blocks)
        groups_total += len(page.semantic_groups)
        relations_total += len(page.semantic_relations)
        must_explain_total += sum(1 for block in page.semantic_blocks if block.get("must_explain"))
        pages.append(
            {
                "slide_id": page.slide_id,
                "title": page.title,
                "page_modality": page.page_modality,
                "blocks": page.semantic_blocks,
                "groups": page.semantic_groups,
                "relations": page.semantic_relations,
            }
        )

    return {
        "schema_version": 1,
        "generated_at": utc_now_iso(),
        "source_path": deck.source_path,
        "source_type": deck.source_type,
        "method": "local_rules_v1",
        "llm_enhancement": "reserved_for_multimodal_v2",
        "summary": {
            "pages_total": len(deck.pages),
            "blocks_total": blocks_total,
            "groups_total": groups_total,
            "relations_total": relations_total,
            "must_explain_blocks": must_explain_total,
        },
        "pages": pages,
    }


def analyze_page_semantic_layout(deck: Deck, page: SlidePage) -> dict[str, Any]:
    blocks: list[dict[str, Any]] = []
    for block in page.text_blocks:
        blocks.append(_text_block_record(deck, page, block))
    for table in page.tables:
        blocks.append(_table_block_record(deck, page, table))
    for image in page.images:
        if image.ignored:
            continue
        blocks.append(_image_block_record(deck, page, image))
    blocks = sorted(blocks, key=lambda block: (_layout_order(block), str(block["id"])))
    groups = _semantic_groups(page, blocks)
    relations = _semantic_relations(blocks, groups)
    return {"blocks": blocks, "groups": groups, "relations": relations}


def semantic_layout_for_prompt(page: SlidePage, limit: int = 6) -> dict[str, Any] | None:
    if not page.semantic_groups and not page.semantic_blocks:
        return None
    return {
        "blocks": [
            {
                "id": block.get("id"),
                "block_type": block.get("block_type"),
                "learning_role": block.get("learning_role"),
                "must_explain": block.get("must_explain"),
                "group_id": block.get("group_id"),
                "crop_policy": block.get("crop_policy"),
                "preview": block.get("preview"),
            }
            for block in page.semantic_blocks[: max(0, limit * 3)]
        ],
        "groups": [
            {
                "group_id": group.get("group_id"),
                "scene_type": group.get("scene_type"),
                "learning_goal": group.get("learning_goal"),
                "block_ids": group.get("block_ids"),
                "must_explain": group.get("must_explain"),
                "crop_policy": group.get("crop_policy"),
            }
            for group in page.semantic_groups[:limit]
        ],
        "relations": page.semantic_relations[: limit * 2],
    }


def semantic_context_for_page(page: SlidePage, limit: int = 900) -> str:
    if not page.semantic_groups:
        return ""
    pieces: list[str] = []
    for group in page.semantic_groups[:4]:
        pieces.append(
            f"{group.get('group_id')}({group.get('scene_type')}, {group.get('crop_policy')}): "
            f"{group.get('learning_goal')} -> {', '.join(group.get('block_ids') or [])}"
        )
    for relation in page.semantic_relations[:6]:
        pieces.append(
            f"relation {relation.get('from')} {relation.get('relation')} {relation.get('to')}: {relation.get('reason')}"
        )
    text = "\n".join(piece for piece in pieces if piece.strip())
    if len(text) > limit:
        return text[: limit - 1] + "…"
    return text


def _text_block_record(deck: Deck, page: SlidePage, block: TextBlock) -> dict[str, Any]:
    block_type = _classify_text_block(block)
    learning_role = _learning_role_for_block(block_type, block.content)
    must_explain = learning_role not in {"structural", "decorative"}
    bbox = _normalize_bbox(deck.source_type, block.bbox, (page.page_width, page.page_height))
    return {
        "id": block.id,
        "kind": "text",
        "source_element_ids": [block.id],
        "block_type": block_type,
        "learning_role": learning_role,
        "must_explain": must_explain,
        "importance_score": _importance_score(block_type, block.content),
        "bbox": bbox,
        "layout_order": _order_from_bbox(bbox),
        "crop_policy": _crop_policy_for_block(block_type),
        "preview": _preview(block.content),
        "group_id": None,
    }


def _table_block_record(deck: Deck, page: SlidePage, table: TableBlock) -> dict[str, Any]:
    bbox = _normalize_bbox(deck.source_type, table.bbox, (page.page_width, page.page_height))
    return {
        "id": table.id,
        "kind": "table",
        "source_element_ids": [table.id],
        "block_type": "table",
        "learning_role": "table_conclusion",
        "must_explain": True,
        "importance_score": 0.82,
        "bbox": bbox,
        "layout_order": _order_from_bbox(bbox),
        "crop_policy": "text_summary",
        "preview": table_preview(table, limit=220),
        "group_id": None,
    }


def _image_block_record(deck: Deck, page: SlidePage, image: ImageAsset) -> dict[str, Any]:
    bbox = _normalize_bbox(deck.source_type, image.crop_bbox or image.bbox, (page.page_width, page.page_height))
    block_type = "figure" if image.role in {"figure_crop", "composite_figure"} else image.role or "image"
    return {
        "id": image.id,
        "kind": "image",
        "source_element_ids": [image.id, *image.source_element_ids],
        "block_type": block_type,
        "learning_role": "visual_evidence",
        "must_explain": image.role in {"figure_crop", "composite_figure"} or (image.importance_score or 0.0) >= 0.55,
        "importance_score": image.importance_score if image.importance_score is not None else 0.55,
        "bbox": bbox,
        "layout_order": image.layout_order if image.layout_order is not None else _order_from_bbox(bbox),
        "crop_policy": "use_existing_image",
        "preview": _preview(" ".join(part for part in [image.caption, image.figure_explanation, image.visual_summary, image.ocr_text] if part) or image.path),
        "group_id": None,
    }


def _semantic_groups(page: SlidePage, blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    key_blocks = [block for block in blocks if block.get("must_explain")]
    if not key_blocks:
        return []
    clusters = _cluster_key_blocks(key_blocks)
    groups: list[dict[str, Any]] = []
    for index, cluster in enumerate(clusters, start=1):
        group_id = f"p{page.slide_id}_sg{index}"
        scene_type = _scene_type(cluster)
        crop_policy = _group_crop_policy(cluster, scene_type)
        learning_goal = _learning_goal(cluster, scene_type)
        for block in cluster:
            block["group_id"] = group_id
        groups.append(
            {
                "group_id": group_id,
                "slide_id": page.slide_id,
                "scene_type": scene_type,
                "learning_goal": learning_goal,
                "block_ids": [str(block["id"]) for block in cluster],
                "source_element_ids": _unique_ids(source_id for block in cluster for source_id in block.get("source_element_ids", [])),
                "must_explain": any(block.get("must_explain") for block in cluster),
                "crop_policy": crop_policy,
                "importance_score": round(max(float(block.get("importance_score") or 0.0) for block in cluster), 3),
            }
        )
    return groups


def _cluster_key_blocks(blocks: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    if _has_code_causal_scene(blocks):
        return [blocks]
    sorted_blocks = sorted(blocks, key=lambda block: (_layout_order(block), str(block["id"])))
    clusters: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    previous_bottom: float | None = None
    for block in sorted_blocks:
        bbox = block.get("bbox")
        top = float(bbox[1]) if isinstance(bbox, list) and len(bbox) == 4 else None
        bottom = float(bbox[3]) if isinstance(bbox, list) and len(bbox) == 4 else None
        if current and top is not None and previous_bottom is not None and top - previous_bottom > 0.16:
            clusters.append(current)
            current = []
        current.append(block)
        if bottom is not None:
            previous_bottom = max(previous_bottom or 0.0, bottom)
    if current:
        clusters.append(current)
    return clusters


def _semantic_relations(blocks: list[dict[str, Any]], groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    del groups
    relations: list[dict[str, Any]] = []
    code_blocks = [block for block in blocks if block.get("block_type") == "code"]
    output_blocks = [block for block in blocks if block.get("block_type") == "output"]
    cause_blocks = [block for block in blocks if block.get("learning_role") == "cause"]
    fix_blocks = [block for block in blocks if block.get("learning_role") == "fix"]
    for code in code_blocks[:2]:
        for output in output_blocks[:2]:
            relations.append(_relation(code, output, "demonstrates", "代码块对应运行输入输出现象"))
        for cause in cause_blocks[:2]:
            relations.append(_relation(code, cause, "explained_by", "解释块说明代码行为背后的原因"))
        for fix in fix_blocks[:2]:
            relations.append(_relation(fix, code, "fixes", "修复提示作用于代码示例"))
    for cause in cause_blocks[:2]:
        for fix in fix_blocks[:2]:
            relations.append(_relation(cause, fix, "leads_to_fix", "原因解释导向修复方法"))
    return relations[:10]


def _relation(source: dict[str, Any], target: dict[str, Any], relation: str, reason: str) -> dict[str, Any]:
    return {
        "from": source.get("id"),
        "to": target.get("id"),
        "relation": relation,
        "reason": reason,
        "confidence": 0.72,
    }


def _classify_text_block(block: TextBlock) -> str:
    text = block.content.strip()
    lowered = text.lower()
    if block.type == "title":
        return "title"
    if _has_explanatory_language(text) and _contains_fix_signal(text):
        return "fix"
    if _has_explanatory_language(text) and _contains_cause_signal(text):
        return "cause_explanation"
    if _looks_like_code(text):
        return "code"
    if _looks_like_output(text):
        return "output"
    if _contains_fix_signal(text):
        return "fix"
    if _contains_cause_signal(text):
        return "cause_explanation"
    if _contains_visual_annotation(text):
        return "annotation"
    if "include" in lowered and len(text) < 80:
        return "code"
    if block.type in {"heading", "bullet"} and len(text) <= 80:
        return "concept_label"
    return "explanation"


def _looks_like_code(text: str) -> bool:
    signals = [
        "#include",
        "using namespace",
        "main()",
        "cout",
        "cin",
        "getline",
        "std::",
        "return ",
        "char ",
        "string ",
        "int ",
    ]
    lowered = text.lower()
    if any(signal in lowered for signal in signals):
        return True
    code_chars = sum(text.count(char) for char in "{};<>=")
    return code_chars >= 4 and bool(re.search(r"\b(if|for|while|void|int|char|string|cout|cin)\b", lowered))


def _looks_like_output(text: str) -> bool:
    lowered = text.lower()
    if re.search(r"\benter\s+(your|student)", lowered):
        return True
    return any(signal in text for signal in ["Data Entered", "Student Number", "Student Name", "Hello John", "输入", "输出"]) and ":" in text


def _contains_cause_signal(text: str) -> bool:
    return bool(re.search(r"因为|由于|导致|所以|因此|异常|留在|依然|被接下来|流提取|换行符|空白字符|缓冲|before|after", text, re.IGNORECASE))


def _contains_fix_signal(text: str) -> bool:
    return bool(re.search(r"cin\.ignore|ignore\(|需要|必须|清空|清除|丢弃|解决|修复|避免|之前|之后", text, re.IGNORECASE))


def _contains_visual_annotation(text: str) -> bool:
    return bool(re.search(r"×|√|->|=>|注意|提示|关键|红色|蓝框", text, re.IGNORECASE))


def _has_explanatory_language(text: str) -> bool:
    cjk_count = sum(1 for char in text if "\u4e00" <= char <= "\u9fff")
    return cjk_count >= 4 or bool(re.search(r"\b(because|therefore|need|before|after|fix|causes?)\b", text, re.IGNORECASE))


def _learning_role_for_block(block_type: str, text: str) -> str:
    if block_type == "title":
        return "structural"
    if block_type == "code":
        return "code_example"
    if block_type == "output":
        return "runtime_output"
    if block_type == "fix":
        return "fix"
    if block_type == "cause_explanation":
        return "cause"
    if block_type == "annotation":
        return "causal_annotation" if _contains_cause_signal(text) or _contains_fix_signal(text) else "visual_annotation"
    return "concept"


def _importance_score(block_type: str, text: str) -> float:
    base = {
        "title": 0.2,
        "code": 0.86,
        "output": 0.78,
        "fix": 0.88,
        "cause_explanation": 0.86,
        "annotation": 0.76,
        "concept_label": 0.58,
        "explanation": 0.62,
    }.get(block_type, 0.5)
    if len(text) >= 160:
        base += 0.04
    return round(min(1.0, base), 3)


def _crop_policy_for_block(block_type: str) -> str:
    if block_type in {"code", "output", "fix", "cause_explanation", "annotation"}:
        return "text_or_group_scene"
    if block_type == "title":
        return "skip_crop"
    return "text_only"


def _has_code_causal_scene(blocks: list[dict[str, Any]]) -> bool:
    types = {str(block.get("block_type")) for block in blocks}
    roles = {str(block.get("learning_role")) for block in blocks}
    return "code" in types and bool({"runtime_output", "cause", "fix", "causal_annotation"} & roles)


def _scene_type(blocks: list[dict[str, Any]]) -> str:
    types = {str(block.get("block_type")) for block in blocks}
    roles = {str(block.get("learning_role")) for block in blocks}
    if "code" in types and "fix" in roles:
        return "code_causal_explanation"
    if "code" in types and "runtime_output" in roles:
        return "code_example_with_output"
    if "table" in types:
        return "table_explanation"
    if "figure" in types or "image" in types:
        return "visual_explanation"
    return "concept_explanation"


def _group_crop_policy(blocks: list[dict[str, Any]], scene_type: str) -> str:
    if scene_type.startswith("code_"):
        return "prefer_structured_text_then_group_image"
    if any(block.get("kind") == "image" for block in blocks):
        return "group_image_near_explanation"
    return "no_individual_crop"


def _learning_goal(blocks: list[dict[str, Any]], scene_type: str) -> str:
    previews = [str(block.get("preview") or "") for block in blocks]
    text = " ".join(previews)
    if scene_type == "code_causal_explanation":
        if re.search(r"getline|cin|换行符|空白字符|ignore", text, re.IGNORECASE):
            return "讲清 cin 提取运算符与 getline 混用时的换行符残留问题、现象和修复方法。"
        return "讲清代码示例的运行现象、原因和修复方法。"
    if scene_type == "code_example_with_output":
        return "把代码与运行输出对应起来，说明示例验证了什么行为。"
    if scene_type == "table_explanation":
        return "提炼表格结论，而不是逐格复述表格内容。"
    if scene_type == "visual_explanation":
        return "将图示与邻近概念合并讲解，避免把图单独堆放。"
    return _preview(text, 140) or "讲解本组核心概念。"


def _normalize_bbox(source_type: str, bbox: list[float] | None, page_size: tuple[float | None, float | None] | None) -> list[float] | None:
    if not bbox or len(bbox) != 4:
        return None
    if all(-0.001 <= float(value) <= 1.001 for value in bbox):
        return _clamp_bbox(bbox)
    width, height = page_size or (None, None)
    if not width or not height:
        return None
    x1, y1, third, fourth = [float(value) for value in bbox]
    if source_type == "pptx":
        x2, y2 = x1 + third, y1 + fourth
    else:
        x2, y2 = third, fourth
    return _clamp_bbox([x1 / width, y1 / height, x2 / width, y2 / height])


def _page_size_for_bbox(deck: Deck, page: SlidePage | None) -> tuple[float | None, float | None] | None:
    if page is not None:
        return page.page_width, page.page_height
    return None


def _clamp_bbox(bbox: list[float]) -> list[float]:
    x1, y1, x2, y2 = [max(0.0, min(1.0, float(value))) for value in bbox]
    if x2 < x1:
        x1, x2 = x2, x1
    if y2 < y1:
        y1, y2 = y2, y1
    return [round(x1, 4), round(y1, 4), round(x2, 4), round(y2, 4)]


def _order_from_bbox(bbox: list[float] | None) -> float:
    if not bbox:
        return 9999.0
    return round(float(bbox[1]) * 1000.0 + float(bbox[0]), 4)


def _layout_order(block: dict[str, Any]) -> float:
    try:
        return float(block.get("layout_order"))
    except (TypeError, ValueError):
        return 9999.0


def _unique_ids(values) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value)
        if text and text not in seen:
            result.append(text)
            seen.add(text)
    return result


def _preview(text: str, limit: int = 180) -> str:
    value = re.sub(r"\s+", " ", text).strip()
    if len(value) <= limit:
        return value
    return value[: limit - 1] + "…"
