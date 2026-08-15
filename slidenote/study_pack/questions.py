from slidenote.models import Deck
from slidenote.utils import round_score
from typing import Any
import re
from .common import (
    _as_int,
    _clean_inline,
    _dict_list,
    _string_list,
)

def _add_unique(items: list[dict[str, Any]], item: dict[str, Any], seen: set[str]) -> bool:
    key = re.sub(r"\s+", "", str(item.get("point") or item.get("explanation") or "")).lower()[:80]
    if not key or key in seen:
        return False
    seen.add(key)
    items.append(item)
    return True

def _answer_text(question: dict[str, Any], qtype: str) -> str:
    if qtype == "choice":
        options = _string_list(question.get("options"), limit=8)
        answer = min(max(_as_int(question.get("answer"), 0), 0), max(len(options) - 1, 0))
        return f"{chr(65 + answer)}. {options[answer] if options else ''}".strip()
    if qtype == "true_false":
        return "正确" if bool(question.get("answer")) else "错误"
    return _clean_inline(question.get("answer")) or "见解析"

def _choice_distractor_score(choice_questions: list[dict[str, Any]]) -> float:
    if not choice_questions:
        return 1.0
    scores = []
    bad_tokens = ["装饰", "无关", "不需要", "只需要记住名称", "只需背", "decoration", "unrelated"]
    for question in choice_questions:
        options = _string_list(question.get("options"), limit=8)
        if len(options) < 4:
            scores.append(0.25)
            continue
        unique_ratio = len({option.lower() for option in options}) / max(1, len(options))
        length_score = min(1.0, sum(1 for option in options if len(option) >= 10) / len(options))
        bad_penalty = min(0.6, sum(1 for option in options for token in bad_tokens if token.lower() in option.lower()) * 0.2)
        scores.append(max(0.0, 0.45 * unique_ratio + 0.55 * length_score - bad_penalty))
    return sum(scores) / len(scores)

def _default_points(qtype: str) -> int:
    return {"choice": 2, "true_false": 1, "short": 6, "essay": 10, "comprehensive": 15}.get(qtype, 5)

def _infer_importance(text: str, role: str | None = None) -> str:
    role_text = (role or "").lower()
    text_lower = text.lower()
    if role_text in {"definition", "formula", "condition", "concept"} or any(token in text for token in ("定义", "定理", "公式", "性质", "结论", "必须", "核心")):
        return "must"
    if role_text in {"table_conclusion", "figure_explanation", "example", "code_example"} or any(token in text for token in ("例", "步骤", "算法", "方法", "流程", "图", "表")):
        return "key"
    if any(token in text_lower for token in ("高频", "常见", "易错", "frequent", "common")):
        return "frequent"
    return "key"

def _is_applied_question(question_text: str) -> bool:
    lowered = question_text.lower()
    tokens = ["为什么", "如何", "比较", "解释", "推导", "场景", "条件", "作用", "易错", "why", "how", "compare", "explain", "apply"]
    return any(token in lowered for token in tokens)

def _is_figure_question(text: str) -> bool:
    lowered = text.lower()
    return any(token in lowered for token in ["图", "表", "公式", "流程", "截图", "figure", "table", "formula", "diagram"])

def _local_choice_options(item: dict[str, Any], items: list[dict[str, Any]]) -> list[str]:
    point = _clean_inline(item.get("point")) or "该知识点"
    explanation = _clean_inline(item.get("explanation")) or point
    distractors: list[str] = []
    for other in items:
        other_point = _clean_inline(other.get("point"))
        other_explanation = _clean_inline(other.get("explanation"))
        if not other_point or other_point == point:
            continue
        distractors.append(f"把「{other_point}」的作用误当成「{point}」的主要含义。")
        if other_explanation:
            distractors.append(f"只记住相邻结论“{_trim_text(other_explanation, 52)}”，但忽略它和「{point}」的适用条件。")
        if len(distractors) >= 3:
            break
    fallback = [
        f"只背「{point}」这个名称，但不能说明它解决的问题和限制。",
        f"把「{point}」理解成任何场景都成立的结论，忽略材料给出的条件。",
        f"只记住最终结论，却不能解释「{point}」与前后概念的关系。",
    ]
    for option in fallback:
        if len(distractors) >= 3:
            break
        distractors.append(option)
    return [explanation, *distractors[:3]]

def _local_logic_chains(deck: Deck, section_plan: dict[str, Any] | None, deck_brief: dict[str, Any] | None) -> list[dict[str, Any]]:
    brief = deck_brief.get("brief") if isinstance(deck_brief, dict) and isinstance(deck_brief.get("brief"), dict) else {}
    chains = _dict_list(brief.get("concept_dependencies"), limit=8)
    if chains:
        return [
            {
                "title": f"{_clean_inline(item.get('source'))} -> {_clean_inline(item.get('target'))}",
                "steps": [_clean_inline(item.get("reason")) or "前一个概念为后一个概念提供理解基础。"],
            }
            for item in chains
        ]
    sections = _dict_list((section_plan or {}).get("sections"), limit=8)
    if sections:
        return [
            {
                "title": _clean_inline(section.get("title")) or f"第 {index} 节",
                "steps": [f"复习页码范围：P{section.get('start_slide_id') or '?'} 起。", _clean_inline(section.get("reason")) or "按章节顺序复习。"],
            }
            for index, section in enumerate(sections, start=1)
        ]
    titles = [_clean_inline(page.title) for page in deck.pages if _clean_inline(page.title)]
    return [{"title": "按材料顺序复习", "steps": [f"先掌握：{title}" for title in titles[:6]] or ["先读 notes.md，再用自测题检查理解。"]}]

def _local_methods(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    methods = []
    for item in items:
        text = f"{item.get('point')} {item.get('explanation')}"
        if any(token in text for token in ("公式", "计算", "步骤", "算法", "推导", "example", "例")):
            methods.append(
                {
                    "name": _clean_inline(item.get("point")) or "解题方法",
                    "detail": _clean_inline(item.get("explanation")),
                    "example": "复习时尝试重新写出步骤，并解释每一步为什么成立。",
                }
            )
        if len(methods) >= 8:
            break
    return methods

def _local_pitfall(point: str, role: str | None = None) -> str:
    if role and "formula" in role:
        return "不要只背公式，要能说明符号含义、适用条件和计算对象。"
    if any(token in point for token in ("图", "流程", "结构")):
        return "不要只看图名，要能沿箭头或结构关系讲出因果链。"
    return "不要只背关键词，要能说明它解决的问题和使用场景。"

def _local_questions(items: list[dict[str, Any]], question_count: int) -> list[dict[str, Any]]:
    questions: list[dict[str, Any]] = []
    if not items:
        return questions
    for index in range(1, max(1, question_count) + 1):
        item = items[(index - 1) % len(items)]
        point = _clean_inline(item.get("point")) or "知识点"
        explanation = _clean_inline(item.get("explanation")) or point
        source_refs = _string_list(item.get("source_refs"), limit=8)
        image_refs = _normalize_image_refs(item.get("image_refs"))
        if index % 4 == 1:
            options = _local_choice_options(item, items)
            questions.append(
                {
                    "id": f"q{index}",
                    "type": "choice",
                    "points": 2,
                    "question": f"关于「{point}」，哪一项最符合材料中的含义？",
                    "options": options,
                    "answer": 0,
                    "explanation": explanation,
                    "pitfall": _clean_inline(item.get("pitfall")),
                    "source_refs": source_refs,
                    "image_refs": image_refs,
                }
            )
        elif index % 4 == 2:
            questions.append(
                {
                    "id": f"q{index}",
                    "type": "true_false",
                    "points": 1,
                    "question": f"判断：「{point}」只要背下名称即可，不需要理解它解决的问题或使用场景。",
                    "answer": False,
                    "explanation": f"错误。复习时应说明它的含义、作用和易错点：{explanation}",
                    "pitfall": "把概念当成孤立名词，是短期备考最常见的失分方式。",
                    "source_refs": source_refs,
                    "image_refs": image_refs,
                }
            )
        elif index % 4 == 3:
            questions.append(
                {
                    "id": f"q{index}",
                    "type": "short",
                    "points": 6,
                    "question": f"请用自己的话解释「{point}」，并说明它为什么重要。",
                    "answer": explanation,
                    "explanation": explanation,
                    "pitfall": _clean_inline(item.get("pitfall")),
                    "source_refs": source_refs,
                    "image_refs": image_refs,
                }
            )
        else:
            questions.append(
                {
                    "id": f"q{index}",
                    "type": "short",
                    "points": 6,
                    "question": f"围绕「{point}」列出一个容易混淆或容易漏写的点。",
                    "answer": _clean_inline(item.get("pitfall")) or explanation,
                    "explanation": explanation,
                    "pitfall": _clean_inline(item.get("pitfall")),
                    "source_refs": source_refs,
                    "image_refs": image_refs,
                }
            )
    return questions[:question_count]

def _local_why(importance: str) -> str:
    return {
        "must": "这类内容通常支撑定义、推导、公式或综合题。",
        "key": "这类内容常用于解释过程、比较概念或连接例子。",
        "frequent": "这类内容适合通过判断题和选择题检查是否混淆。",
        "background": "这类内容帮助理解上下文，复习时保持基本印象即可。",
    }.get(importance, "这类内容是理解本节材料的组成部分。")

def _normalize_answer(value: Any, qtype: str) -> Any:
    if qtype == "choice":
        return _as_int(value, 0)
    if qtype == "true_false":
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        return text in {"true", "1", "yes", "y", "正确", "对"}
    return _clean_inline(value)

def _normalize_image_refs(value: Any) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for item in _dict_list(value, limit=8):
        path = _clean_inline(item.get("path"))
        if not path:
            continue
        refs.append(
            {
                "id": _clean_inline(item.get("id")),
                "title": _clean_inline(item.get("title")) or "题目图",
                "path": path,
                "source_ref": _clean_inline(item.get("source_ref")),
            }
        )
    return refs

def _normalize_importance(value: Any) -> str:
    text = str(value or "").strip().lower()
    aliases = {
        "必考": "must",
        "must": "must",
        "重点": "key",
        "key": "key",
        "高频": "frequent",
        "freq": "frequent",
        "frequent": "frequent",
        "了解": "background",
        "background": "background",
        "info": "background",
    }
    return aliases.get(text, "key")

def _normalize_question_type(value: Any) -> str:
    text = str(value or "").strip().lower()
    aliases = {
        "choice": "choice",
        "multiple_choice": "choice",
        "选择题": "choice",
        "tf": "true_false",
        "true_false": "true_false",
        "判断题": "true_false",
        "short": "short",
        "short_answer": "short",
        "简答题": "short",
        "essay": "essay",
        "论述题": "essay",
        "comprehensive": "comprehensive",
        "综合题": "comprehensive",
    }
    return aliases.get(text, "short")

def _point_from_text(text: str) -> str:
    clean = _clean_inline(text)
    if not clean:
        return "知识点"
    for sep in ("：", ":", "。", ".", "；", ";", "\n"):
        if sep in clean:
            head = clean.split(sep, 1)[0].strip()
            if 2 <= len(head) <= 36:
                return head
    return clean[:36].rstrip()

def _question_quality_flags(
    *,
    choice_score: float,
    explanation_score: float,
    source_ref_score: float,
    question_mix_score: float,
    figure_question_score: float,
    mechanical_definition_score: float,
    has_figures: bool,
) -> list[str]:
    flags: list[str] = []
    if choice_score < 0.55:
        flags.append("choice_distractors_need_same_concept_cluster")
    if explanation_score < 0.65:
        flags.append("questions_need_explanations_and_pitfalls")
    if source_ref_score < 0.75:
        flags.append("questions_need_source_refs")
    if question_mix_score < 0.5:
        flags.append("question_types_too_narrow")
    if has_figures and figure_question_score < 0.35:
        flags.append("figure_table_questions_need_inline_refs")
    if mechanical_definition_score > 0.65:
        flags.append("questions_too_definition_like")
    return flags

def _trim_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    head = text[: int(limit * 0.7)]
    tail = text[-int(limit * 0.3) :]
    return head.rstrip() + "\n\n...[notes truncated for prompt budget]...\n\n" + tail.lstrip()

def build_question_quality_report(study_pack_report: dict[str, Any] | None) -> dict[str, Any]:
    exam = study_pack_report.get("exam") if isinstance(study_pack_report, dict) and isinstance(study_pack_report.get("exam"), dict) else {}
    questions = _dict_list(exam.get("questions"), limit=300)
    figure_table_notes = _dict_list((study_pack_report or {}).get("figure_table_notes"), limit=300) if isinstance(study_pack_report, dict) else []
    if not questions:
        return {
            "schema_version": 1,
            "overall_score": 0.0,
            "choice_distractor_score": 0.0,
            "explanation_score": 0.0,
            "source_ref_score": 0.0,
            "question_mix_score": 0.0,
            "figure_question_score": 1.0 if not figure_table_notes else 0.0,
            "mechanical_definition_score": 0.0,
            "flags": ["no_questions"],
        }
    choice_questions = [question for question in questions if _normalize_question_type(question.get("type")) == "choice"]
    source_ref_hits = sum(1 for question in questions if _string_list(question.get("source_refs"), limit=8))
    explanation_hits = sum(1 for question in questions if _clean_inline(question.get("explanation")) and _clean_inline(question.get("pitfall")))
    applied_hits = sum(1 for question in questions if _is_applied_question(str(question.get("question") or "")))
    figure_hits = sum(1 for question in questions if _dict_list(question.get("image_refs"), limit=4))
    type_count = len({_normalize_question_type(question.get("type")) for question in questions})
    choice_score = _choice_distractor_score(choice_questions)
    explanation_score = explanation_hits / max(1, len(questions))
    source_ref_score = source_ref_hits / max(1, len(questions))
    question_mix_score = min(1.0, type_count / 4)
    figure_question_score = 1.0 if not figure_table_notes else min(1.0, figure_hits / max(1, min(len(figure_table_notes), len(questions))))
    mechanical_definition_score = 1.0 - min(1.0, applied_hits / max(1, len(questions)))
    overall = (
        0.24 * choice_score
        + 0.22 * explanation_score
        + 0.18 * source_ref_score
        + 0.16 * question_mix_score
        + 0.12 * figure_question_score
        + 0.08 * (1.0 - mechanical_definition_score)
    )
    flags = _question_quality_flags(
        choice_score=choice_score,
        explanation_score=explanation_score,
        source_ref_score=source_ref_score,
        question_mix_score=question_mix_score,
        figure_question_score=figure_question_score,
        mechanical_definition_score=mechanical_definition_score,
        has_figures=bool(figure_table_notes),
    )
    return {
        "schema_version": 1,
        "overall_score": round_score(overall),
        "choice_distractor_score": round_score(choice_score),
        "explanation_score": round_score(explanation_score),
        "source_ref_score": round_score(source_ref_score),
        "question_mix_score": round_score(question_mix_score),
        "figure_question_score": round_score(figure_question_score),
        "mechanical_definition_score": round_score(mechanical_definition_score),
        "questions_total": len(questions),
        "choice_questions_total": len(choice_questions),
        "flags": flags,
    }
