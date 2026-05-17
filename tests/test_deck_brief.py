import json

from slidenote.deck_brief import build_deck_brief, deck_brief_for_prompt, render_deck_brief_markdown
from slidenote.models import Deck, SlidePage, TextBlock


def test_deck_brief_generates_report_and_uses_cache(tmp_path, monkeypatch):
    deck = Deck(
        source_path="lecture.pdf",
        source_type="pdf",
        pages=[
            SlidePage(slide_id=1, title="Replication", text_blocks=[TextBlock(id="s1_t1", type="paragraph", content="Replica")]),
            SlidePage(slide_id=2, title="Quorum", text_blocks=[TextBlock(id="s2_t1", type="paragraph", content="Quorum")]),
        ],
    )
    calls = []

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def generate_with_usage(self, prompt, system_prompt=None):
            calls.append((prompt, system_prompt))

            class Result:
                text = json.dumps(
                    {
                        "course_title": "Replication and Quorum",
                        "one_sentence_summary": "The deck explains replicated data and quorum reads/writes.",
                        "core_questions": ["How do replicas stay useful?"],
                        "chapter_outline": [{"title": "Replication", "summary": "Replica basics", "slide_ids": [1, 2]}],
                        "key_concepts": [{"term": "Quorum", "definition": "Intersecting read/write sets", "first_slide_id": 2}],
                        "concept_dependencies": [{"source": "Replication", "target": "Quorum", "reason": "Quorum assumes replicas."}],
                        "page_roles": [
                            {"slide_id": 1, "role": "definition", "reason": "Introduces replicas"},
                            {"slide_id": 2, "role": "definition", "reason": "Defines quorum"},
                        ],
                        "cross_page_links": [{"from_slide_id": 1, "to_slide_id": 2, "reason": "Quorum builds on replicas"}],
                        "writing_guidance": ["Keep page details, use the map only for transitions."],
                    }
                )
                usage = {"input_tokens": 11, "output_tokens": 13, "total_tokens": 24}

            return Result()

    monkeypatch.setattr("slidenote.deck_brief.LLMClient", FakeClient)

    report = build_deck_brief(deck, tmp_path, provider="openai", api_key="test", cache_dir=tmp_path / "cache")

    assert report["brief"]["course_title"] == "Replication and Quorum"
    assert report["summary"]["llm_call"] is True
    assert report["summary"]["page_roles_total"] == 2
    assert calls and "build_deck_brief" in calls[0][0]
    assert "Deck Brief: Replication and Quorum" in render_deck_brief_markdown(report)

    prompt_brief = deck_brief_for_prompt(report, slide_ids={2})
    assert prompt_brief is not None
    assert prompt_brief["page_roles"] == [{"slide_id": 2, "role": "definition", "reason": "Defines quorum"}]
    assert prompt_brief["cross_page_links"][0]["to_slide_id"] == 2

    class FailingClient:
        def __init__(self, **kwargs):
            raise AssertionError("cache hit should not instantiate an LLM client")

    monkeypatch.setattr("slidenote.deck_brief.LLMClient", FailingClient)
    cached = build_deck_brief(deck, tmp_path, provider="openai", cache_dir=tmp_path / "cache")

    assert cached["summary"]["llm_call"] is False
    assert cached["summary"]["local_cache_hits"] == 1
    assert cached["brief"]["key_concepts"][0]["term"] == "Quorum"


def test_deck_brief_invalid_json_falls_back_without_failing(tmp_path, monkeypatch):
    deck = Deck(
        source_path="lecture.pdf",
        source_type="pdf",
        pages=[SlidePage(slide_id=1, text_blocks=[TextBlock(id="s1_t1", type="paragraph", content="A")])],
    )

    class BadClient:
        def __init__(self, **kwargs):
            pass

        def generate_with_usage(self, prompt, system_prompt=None):
            class Result:
                text = "not json"
                usage = {}

            return Result()

    monkeypatch.setattr("slidenote.deck_brief.LLMClient", BadClient)

    report = build_deck_brief(deck, tmp_path, provider="openai", api_key="test", cache_dir=tmp_path / "cache")

    assert report["brief"]["page_roles"] == []
    assert "deck_brief_invalid_json" in report["warnings"]
    assert report["summary"]["llm_call"] is True
