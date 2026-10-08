
import re
from collections import Counter
from typing import Any


LECTURE_NOTE_STRUCTURE_SLOTS: dict[str, tuple[str, ...]] = {
    "orientation": (
        "本讲目标",
        "学习目标",
        "本讲要解决的问题",
        "lecture objectives",
        "learning objectives",
        "core questions",
    ),
    "example_application": (
        "例子与应用",
        "示例与应用",
        "运行示例",
        "执行示例",
        "推导示例",
        "代码示例",
        "worked example",
        "example",
        "application",
    ),
    "pitfalls": (
        "易错点",
        "常见误解",
        "注意事项",
        "常见错误",
        "pitfalls",
        "common misconceptions",
        "common mistakes",
    ),
    "summary": (
        "本讲总结",
        "课程总结",
        "总结",
        "summing up",
        "lecture summary",
    ),
    "self_test": (
        "章节自测",
        "本讲自测",
        "综合自测",
        "self-test",
        "self test",
        "review questions",
    ),
}


LECTURE_NOTE_SLOT_LABELS_ZH: dict[str, str] = {
    "orientation": "本讲目标",
    "topic_sections": "按课程主题命名的章节",
    "example_application": "至少一个有材料依据的例子或应用",
    "pitfalls": "至少一处真正必要的易错点",
    "summary": "本讲总结",
    "self_test": "章节自测",
}


_GENERIC_SECTION_ALIASES = {
    alias.lower()
    for slot in LECTURE_NOTE_STRUCTURE_SLOTS
    for alias in LECTURE_NOTE_STRUCTURE_SLOTS[slot]
}

_GLOBAL_STRUCTURE_SLOTS = {"orientation", "summary", "self_test"}

_REPETITIVE_SUBHEADINGS = {
    "学习目标",
    "本节核心问题",
    "核心概念",
    "核心概念与机制",
    "例子与应用",
    "示例与应用",
    "易错点",
    "常见误解",
    "本节小结",
    "小结",
    "自测问题",
    "自测题",
    "learning objectives",
    "core concepts and mechanisms",
    "examples and applications",
    "common misconceptions",
    "section summary",
    "self-test",
}


def assess_lecture_note_structure(markdown: str) -> dict[str, Any]:
    """Assess the structure of a complete lecture note.

    The contract is global rather than a six-field template repeated in every
    chapter: one orientation, topic-named chapters, and one closing summary and
    review section, with examples and pitfalls where the source supports them.
    """

    headings = _heading_sections(markdown)
    matched: dict[str, str] = {}
    empty_matches: dict[str, str] = {}
    for slot, aliases in LECTURE_NOTE_STRUCTURE_SLOTS.items():
        for heading in headings:
            if slot in _GLOBAL_STRUCTURE_SLOTS and heading["level"] != 2:
                continue
            if not _heading_matches(heading["title"], aliases):
                continue
            if _visible_length(heading["body"]) >= 12:
                matched[slot] = heading["title"]
            else:
                empty_matches[slot] = heading["title"]
            break

    topic_sections = [
        heading["title"]
        for heading in headings
        if heading["level"] == 2
        and not _is_generic_document_section(heading["title"])
        and not _is_page_heading(heading["title"])
    ]
    if topic_sections:
        matched["topic_sections"] = topic_sections[0]

    page_headings = [
        heading["title"]
        for heading in headings
        if 2 <= heading["level"] <= 4 and _is_page_heading(heading["title"])
    ]
    repeated = _repetitive_heading_counts(headings)
    template_repetition_detected = len(repeated) >= 2 and sum(repeated.values()) >= 4
    repeated_global = {
        slot: sum(
            1
            for heading in headings
            if heading["level"] == 2 and _heading_matches(heading["title"], aliases)
        )
        for slot, aliases in LECTURE_NOTE_STRUCTURE_SLOTS.items()
        if slot in _GLOBAL_STRUCTURE_SLOTS
    }
    mechanical_repetition_pass = (
        not page_headings
        and not template_repetition_detected
        and all(count <= 1 for count in repeated_global.values())
    )

    required = ["orientation", "topic_sections", "summary", "self_test"]
    recommended = ["example_application", "pitfalls"]
    missing = [slot for slot in required if slot not in matched]
    scored = required + recommended
    score = round(len(matched.keys() & set(scored)) / len(scored), 4)
    return {
        "schema_version": 2,
        "required_slots": required,
        "recommended_slots": recommended,
        "matched_slots": matched,
        "missing_slots": missing,
        "missing_recommended_slots": [slot for slot in recommended if slot not in matched],
        "empty_slot_headings": empty_matches,
        "topic_sections": topic_sections,
        "topic_section_count": len(topic_sections),
        "page_headings": page_headings,
        "repetitive_subheading_counts": repeated,
        "template_repetition_detected": template_repetition_detected,
        "repeated_global_sections": repeated_global,
        "mechanical_repetition_pass": mechanical_repetition_pass,
        "score": score,
        "passed": not missing and mechanical_repetition_pass,
    }


def lecture_note_structure_prompt_rule(note_language: str) -> str:
    if note_language == "en":
        return (
            "Stable document contract: keep exactly one H1 title; place one ## Learning Objectives near the start; "
            "organize the body into H2 chapters named after the actual course topics; end with exactly one "
            "## Lecture Summary and one ## Review Questions. Across the lecture, include at least one source-backed "
            "worked example and one genuinely useful pitfalls section. Inside each chapter, choose only the H3 "
            "headings the material needs (for example prerequisites, mechanism, pseudocode/code, worked example, "
            "complexity, or pitfalls). Never repeat the same template sequence in every chapter."
        )
    return (
        "稳定的全文结构契约：保留且只保留一个 H1 课程标题；开头只设置一次 ## 本讲目标；"
        "正文使用由真实课程主题命名的 H2 章节；结尾只设置一次 ## 本讲总结和一次 ## 章节自测。"
        "全文至少应有一个由材料支持的示例/应用和一处真正有必要的易错点。"
        "每章内部只选择材料确实需要的 H3，例如核心思想、使用前提、伪代码/代码、执行示例、复杂度或易错点；"
        "不要让每章重复同一组六栏标题，也不要为了填模板制造空泛内容。"
    )


def lecture_section_style_prompt_rule(note_language: str) -> str:
    if note_language == "en":
        return (
            "Chapter structure is adaptive, not a form. Use two to five natural H3 headings only when supported by "
            "the material. Preserve exact code and pseudocode. Do not add chapter-level objectives, a chapter summary, "
            "or a self-test by default; those belong to the document frame. Avoid reusing the same generic headings "
            "and sentence openings in every chapter."
        )
    return (
        "章节结构必须随内容变化，而不是套表格：只在材料支持时选用二到五个自然的 H3，"
        "例如“核心思路”“使用前提”“伪代码”“执行过程”“复杂度”“代码要点”或“易错点”。"
        "代码与伪代码必须原样保真。默认不要在每章重复学习目标、本节小结和自测；这些属于全文框架。"
        "不要让不同章节使用完全相同的小标题顺序或相同的开场句。"
    )


def structure_missing_labels(assessment: dict[str, Any]) -> list[str]:
    return [LECTURE_NOTE_SLOT_LABELS_ZH.get(slot, slot) for slot in assessment.get("missing_slots", [])]


def _heading_sections(markdown: str) -> list[dict[str, Any]]:
    matches = list(re.finditer(r"(?m)^(#{1,4})\s+(.+?)\s*$", markdown))
    sections: list[dict[str, Any]] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
        sections.append(
            {
                "level": len(match.group(1)),
                "title": match.group(2).strip(),
                "body": markdown[start:end].strip(),
            }
        )
    return sections


def _heading_matches(title: str, aliases: tuple[str, ...]) -> bool:
    normalized = _normalize_heading(title)
    return any(
        normalized == alias_normalized or normalized.startswith(alias_normalized + " ")
        for alias in aliases
        if (alias_normalized := _normalize_heading(alias))
    )


def _normalize_heading(title: str) -> str:
    title = re.sub(r"^\s*(?:\d+|[一二三四五六七八九十百]+)[.、\s-]+", "", title.strip())
    return re.sub(r"[\s：:—\-_/（）()]+", " ", title).strip().lower()


def _is_generic_document_section(title: str) -> bool:
    return _heading_matches(title, tuple(_GENERIC_SECTION_ALIASES))


def _clean_numbering(title: str) -> str:
    return re.sub(r"^\s*(?:\d+|[一二三四五六七八九十百]+)[.、\s-]+", "", title.strip())


def _is_page_heading(title: str) -> bool:
    raw = _clean_numbering(title)
    suffix = r"(?:\s*[:：—–-]\s*.*|\s+.+)?"
    return bool(re.fullmatch(rf"(?:第\s*)?\d+\s*(?:页|张){suffix}", raw)) or bool(
        re.fullmatch(rf"(?:slide|page|p)\s*\d+{suffix}", raw, flags=re.IGNORECASE)
    )


def _repetitive_heading_counts(headings: list[dict[str, Any]]) -> dict[str, int]:
    counts = Counter(_normalize_heading(heading["title"]) for heading in headings if heading["level"] >= 3)
    repetitive_aliases = {_normalize_heading(alias) for alias in _REPETITIVE_SUBHEADINGS}
    return {
        title: count
        for title, count in sorted(counts.items())
        if count > 1 and title in repetitive_aliases
    }


def _visible_length(body: str) -> int:
    visible = re.sub(r"<!--.*?-->", "", body, flags=re.DOTALL)
    visible = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", visible)
    visible = re.sub(r"[`*_>#|\-\s]", "", visible)
    return len(visible)
