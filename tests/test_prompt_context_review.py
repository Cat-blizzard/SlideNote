import pytest

from slidenote.models import SlidePage
from slidenote.notes.contexts import NoteContext
from slidenote.notes.prompt_templates import _llm_teaching_enrichment_prompt, _llm_weave_prompt


@pytest.mark.parametrize("language", ["zh", "en"])
def test_document_generation_leaves_h1_to_composition(language):
    page = SlidePage(slide_id=1, title="Search")
    context = NoteContext(id="document", kind="document", title="Search", pages=[page])
    common = dict(
        context=context,
        page_markdown_by_slide={1: "Search examines candidate values."},
        source_display="hidden",
        note_context="document",
        note_profile="lecture-notes",
        note_depth="detailed",
        note_language=language,
        term_policy="bilingual",
    )
    woven = _llm_weave_prompt(**common, note_style="article", weave_dedup="normal")
    enriched = _llm_teaching_enrichment_prompt(**common, woven_markdown="Search examines candidates.")

    for prompt in (woven, enriched):
        assert "keep exactly one H1 title" not in prompt
        assert "保留且只保留一个 H1 课程标题" not in prompt
        assert "H2" in prompt
        assert "do not emit H1" in prompt or "不要输出 H1" in prompt
    assert "全文正文使用 H2" in woven
    assert "正文小标题只能使用 H3" not in woven


def test_section_weaving_keeps_system_outer_heading():
    page = SlidePage(slide_id=1, title="Search")
    prompt = _llm_weave_prompt(
        context=NoteContext(id="section", kind="section", title="Search", pages=[page]),
        page_markdown_by_slide={1: "Search examines candidate values."},
        source_display="hidden",
        note_context="section",
        note_profile="lecture-notes",
        note_depth="detailed",
        note_language="zh",
        term_policy="bilingual",
        note_style="article",
        weave_dedup="normal",
    )

    assert "正文小标题只能使用 H3" in prompt
    assert "全文正文使用 H2" not in prompt
