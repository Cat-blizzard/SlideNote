from __future__ import annotations

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
