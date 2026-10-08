
import re
from typing import Any

from .structure import LECTURE_NOTE_STRUCTURE_SLOTS


_GLOBAL_HEADINGS_ZH = ("本讲目标", "本讲总结", "章节自测")
_GLOBAL_HEADINGS_EN = ("Learning Objectives", "Lecture Summary", "Review Questions")


def ensure_lecture_document_frame(
    markdown: str,
    deck_brief: dict[str, Any] | None,
    note_language: str,
) -> str:
    """Add a small, source-derived document frame without rewriting chapters.

    The operation is idempotent. It never modifies chapter prose, code blocks,
    images, or source markers; it only adds missing document-level sections.
    """

    text = markdown.strip()
    if not text:
        return markdown

    headings = _h2_titles(text)
    objective_title, summary_title, review_title = (
        _GLOBAL_HEADINGS_EN if note_language == "en" else _GLOBAL_HEADINGS_ZH
    )
    chapters = [title for title in headings if not _is_global_heading(title, note_language)]
    brief = _brief_payload(deck_brief)
    outline = _outline_entries(brief)
    topic_names = _topic_names(chapters, outline, brief)

    if not chapters:
        topic_title = topic_names[0] if topic_names else ("Core Content" if note_language == "en" else "核心内容")
        text = _insert_before_first_body(text, f"## {topic_title}\n\n")
        chapters = [topic_title]

    if not _has_global_h2(text, "orientation"):
        objectives = _objective_items(brief, topic_names, note_language)
        section = f"## {objective_title}\n\n" + "\n".join(f"- {item}" for item in objectives) + "\n\n"
        text = _insert_after_h1(text, section)

    if not _has_global_h2(text, "summary"):
        summaries = _summary_items(outline, topic_names, note_language)
        text = text.rstrip() + f"\n\n## {summary_title}\n\n" + "\n".join(f"- {item}" for item in summaries)

    if not _has_global_h2(text, "self_test"):
        questions = _review_items(brief, topic_names, note_language)
        text = text.rstrip() + f"\n\n## {review_title}\n\n" + "\n".join(
            f"{index}. {item}" for index, item in enumerate(questions, start=1)
        )

    return text.rstrip() + "\n"


def _brief_payload(report: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(report, dict):
        return {}
    brief = report.get("brief")
    return brief if isinstance(brief, dict) else report


def _outline_entries(brief: dict[str, Any]) -> list[dict[str, str]]:
    raw = brief.get("chapter_outline")
    if not isinstance(raw, list):
        return []
    result: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        summary = str(item.get("summary") or "").strip()
        if title:
            result.append({"title": title, "summary": summary})
    return result


def _topic_names(chapters: list[str], outline: list[dict[str, str]], brief: dict[str, Any]) -> list[str]:
    names = [_clean_numbering(title) for title in chapters if _usable_topic(title)]
    names.extend(item["title"] for item in outline if _usable_topic(item["title"]))
    if not names:
        names.extend(
            str(item.get("term") or "").strip()
            for item in brief.get("key_concepts") or []
            if isinstance(item, dict) and str(item.get("term") or "").strip()
        )
    return _unique(names, limit=10)


def _objective_items(brief: dict[str, Any], topics: list[str], note_language: str) -> list[str]:
    questions = _clean_strings(brief.get("core_questions"), limit=6)
    if questions:
        return questions
    if note_language == "en":
        return [f"Explain the central idea, conditions, and use of {topic}." for topic in topics[:6]] or [
            "Explain the lecture's central concepts and how they connect."
        ]
    return [f"理解“{topic}”的核心思想、适用条件与作用。" for topic in topics[:6]] or [
        "理解本讲核心概念及其前后关系。"
    ]


def _summary_items(outline: list[dict[str, str]], topics: list[str], note_language: str) -> list[str]:
    items: list[str] = []
    for entry in outline:
        if not entry["summary"] or not _usable_topic(entry["title"]):
            continue
        items.append(f"**{entry['title']}**：{entry['summary']}")
        if len(items) >= 8:
            break
    if items:
        return items
    if note_language == "en":
        return [f"Review the core conclusion of **{topic}**." for topic in topics[:8]] or [
            "Review the lecture's central line of reasoning."
        ]
    return [f"回顾 **{topic}** 的核心结论及其适用条件。" for topic in topics[:8]] or [
        "回顾本讲的核心知识链与关键条件。"
    ]


def _review_items(brief: dict[str, Any], topics: list[str], note_language: str) -> list[str]:
    questions = [_as_question(item, note_language) for item in _clean_strings(brief.get("core_questions"), limit=6)]
    if questions:
        return questions
    if note_language == "en":
        return [f"What problem does {topic} solve, and what conditions does it require?" for topic in topics[:6]] or [
            "Can you reconstruct the lecture's main reasoning without looking at the notes?"
        ]
    return [f"“{topic}”解决了什么问题，又依赖哪些条件？" for topic in topics[:6]] or [
        "你能不看笔记复述本讲的主要知识链吗？"
    ]


def _insert_after_h1(markdown: str, section: str) -> str:
    match = re.search(r"(?m)^#\s+.+?\s*$", markdown)
    if not match:
        return section + markdown.lstrip()
    return markdown[: match.end()].rstrip() + "\n\n" + section + markdown[match.end() :].lstrip("\r\n")


def _insert_before_first_body(markdown: str, heading: str) -> str:
    match = re.search(r"(?m)^#\s+.+?\s*$", markdown)
    if not match:
        return heading + markdown.lstrip()
    return markdown[: match.end()].rstrip() + "\n\n" + heading + markdown[match.end() :].lstrip("\r\n")


def _h2_titles(markdown: str) -> list[str]:
    return [match.group(1).strip() for match in re.finditer(r"(?m)^##\s+(.+?)\s*$", markdown)]


def _has_global_h2(markdown: str, slot: str) -> bool:
    aliases = {_normalize(alias) for alias in LECTURE_NOTE_STRUCTURE_SLOTS[slot]}
    return any(_normalize(_clean_numbering(title)) in aliases for title in _h2_titles(markdown))


def _is_global_heading(title: str, note_language: str) -> bool:
    del note_language
    aliases = {
        _normalize(alias)
        for slot in ("orientation", "summary", "self_test")
        for alias in LECTURE_NOTE_STRUCTURE_SLOTS[slot]
    }
    return _normalize(_clean_numbering(title)) in aliases


def _usable_topic(title: str) -> bool:
    normalized = _normalize(_clean_numbering(title))
    banned = {"", "welcome", "welcome!", "introduction", "引言", "欢迎", "目录", "contents", "总结", "summing up"}
    return normalized not in banned and not normalized.startswith("第 ")


def _clean_numbering(title: str) -> str:
    return re.sub(r"^\s*(?:\d+|[一二三四五六七八九十百]+)[.、\s-]+", "", title).strip()


def _normalize(value: str) -> str:
    return re.sub(r"[\s：:—\-_/（）()]+", " ", value).strip().lower()


def _clean_strings(value: Any, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    return _unique([str(item).strip() for item in value if isinstance(item, str) and item.strip()], limit=limit)


def _unique(items: list[str], limit: int) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        key = _normalize(item)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(item)
        if len(result) >= limit:
            break
    return result


def _as_question(text: str, note_language: str) -> str:
    if text.endswith(("?", "？")):
        return text
    return text + ("?" if note_language == "en" else "？")