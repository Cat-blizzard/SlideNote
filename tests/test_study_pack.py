import json

from slidenote.models import Deck, SlidePage, TableBlock, TextBlock
from slidenote.study_pack import build_study_pack, render_exam_html, render_exam_markdown, render_review_markdown


def test_local_study_pack_generates_review_and_exam(tmp_path):
    deck = Deck(
        source_path="lecture.pdf",
        source_type="pdf",
        pages=[
            SlidePage(
                slide_id=1,
                title="Transport",
                text_blocks=[
                    TextBlock(id="s1_t1", type="title", content="Transport"),
                    TextBlock(id="s1_t2", type="bullet", content="TCP provides reliable ordered delivery."),
                ],
                tables=[TableBlock(id="s1_tbl1", rows=[["Protocol", "Property"], ["UDP", "Best effort"]], table_conclusion="TCP and UDP trade reliability for cost.")],
            )
        ],
    )

    report = build_study_pack(deck, "## Transport\n\nTCP provides reliable ordered delivery.", tmp_path, review_mode="local", exam_mode="local", question_count=4)

    assert report is not None
    assert report["generator"] == "local"
    assert report["summary"]["review_items_total"] >= 2
    assert report["summary"]["questions_total"] == 4
    assert "Transport" in render_review_markdown(report)
    assert "答案与解析" in render_exam_markdown(report)
    assert "一键批改" in render_exam_html(report)


def test_llm_study_pack_generates_report_and_uses_cache(tmp_path, monkeypatch):
    deck = Deck(
        source_path="lecture.pdf",
        source_type="pdf",
        pages=[SlidePage(slide_id=1, title="Replication", text_blocks=[TextBlock(id="s1_t1", type="paragraph", content="Replica consistency")])],
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
                        "review": {
                            "title": "Replication",
                            "summary": "Replica consistency review.",
                            "logic_chains": [{"title": "Replica -> Consistency", "steps": ["Replicas create divergence.", "Consistency protocols constrain divergence."]}],
                            "checklist": [
                                {
                                    "section": "Replication",
                                    "importance": "must",
                                    "point": "Replica consistency",
                                    "explanation": "Replicas must remain useful under updates.",
                                    "why": "It is the central reliability question.",
                                    "pitfall": "Do not confuse availability with consistency.",
                                    "source_refs": ["P1"],
                                }
                            ],
                            "methods": [],
                        },
                        "exam": {
                            "title": "Replication",
                            "subtitle": "Self test",
                            "questions": [
                                {
                                    "id": "q1",
                                    "type": "choice",
                                    "points": 2,
                                    "question": "What is the key issue?",
                                    "options": ["Consistency", "Decoration"],
                                    "answer": 0,
                                    "explanation": "Consistency is the key issue.",
                                    "pitfall": "Do not ignore updates.",
                                    "source_refs": ["P1"],
                                }
                            ],
                        },
                    }
                )
                usage = {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30}

            return Result()

    monkeypatch.setattr("slidenote.study_pack.LLMClient", FakeClient)

    report = build_study_pack(
        deck,
        "## Replication\n\nReplica consistency",
        tmp_path,
        review_mode="llm",
        exam_mode="llm",
        question_count=1,
        provider="openai",
        api_key="test",
        cache_dir=tmp_path / "cache",
    )

    assert report is not None
    assert report["generator"] == "llm"
    assert report["summary"]["llm_call"] is True
    assert report["review"]["checklist"][0]["importance"] == "must"
    assert report["exam"]["questions"][0]["answer"] == 0
    assert calls and "build_exam_review_pack" in calls[0][0]

    class FailingClient:
        def __init__(self, **kwargs):
            raise AssertionError("cache hit should not instantiate an LLM client")

    monkeypatch.setattr("slidenote.study_pack.LLMClient", FailingClient)
    cached = build_study_pack(
        deck,
        "## Replication\n\nReplica consistency",
        tmp_path,
        review_mode="llm",
        exam_mode="llm",
        question_count=1,
        provider="openai",
        cache_dir=tmp_path / "cache",
    )

    assert cached is not None
    assert cached["summary"]["llm_call"] is False
    assert cached["summary"]["local_cache_hits"] == 1
    assert cached["review"]["checklist"][0]["point"] == "Replica consistency"
