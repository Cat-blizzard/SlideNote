from __future__ import annotations

import re

NOTE_PROFILES = {"auto", "lecture-notes", "study-guide"}
TEACHING_ENRICHMENT_MODES = {"auto", "off", "force"}


def resolve_note_depth(note_profile: str, note_depth: str | None) -> str:
    if note_depth:
        return note_depth
    if note_profile == "lecture-notes":
        return "very-detailed"
    return "detailed"


def should_run_teaching_enrichment(note_profile: str, teaching_enrichment: str, note_strategy: str) -> bool:
    if note_strategy != "lecture-weave":
        return False
    if teaching_enrichment == "off":
        return False
    if teaching_enrichment == "force":
        return True
    return note_profile in {"lecture-notes", "study-guide"}


def needs_teaching_enrichment(markdown: str, page_count: int) -> bool:
    """Skip the extra model pass when the woven draft already has teaching signals."""
    visible = re.sub(r"<!--.*?-->", "", markdown, flags=re.DOTALL)
    body_chars = sum(char.isalnum() for char in visible)
    if body_chars < max(100, 60 * page_count):
        return True
    teaching_signals = (
        r"例如|例子|比如|类比|for example|e\.g\.|analogy",
        r"易错|误解|陷阱|常见错误|pitfall|misconception|common mistake",
        r"自测|思考题|练习题|检查自己|self[- ]?check|quiz|review question",
    )
    return not all(re.search(pattern, visible, flags=re.IGNORECASE) for pattern in teaching_signals)
