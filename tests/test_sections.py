from slidenote.models import Deck, SlidePage, TextBlock
from slidenote.sections import build_local_section_plan, build_section_plan


def test_local_section_plan_uses_outline_titles(tmp_path):
    deck = Deck(
        source_path="lecture.pdf",
        source_type="pdf",
        pages=[
            SlidePage(slide_id=1, title="Contents", text_blocks=[TextBlock(id="s1_t1", type="paragraph", content="Contents\nReplication\nQuorum")]),
            SlidePage(slide_id=2, title="Replication", text_blocks=[TextBlock(id="s2_t1", type="paragraph", content="Replica")]),
            SlidePage(
                slide_id=3,
                title="Details",
                text_blocks=[
                    TextBlock(id="s3_t1", type="paragraph", content="Details"),
                    TextBlock(id="s3_t2", type="paragraph", content="This page continues the replication explanation."),
                    TextBlock(id="s3_t3", type="paragraph", content="It should stay inside the previous section."),
                    TextBlock(id="s3_t4", type="paragraph", content="Extra content prevents title-page detection."),
                ],
            ),
            SlidePage(slide_id=4, title="Quorum", text_blocks=[TextBlock(id="s4_t1", type="paragraph", content="Quorum")]),
        ],
    )

    plan = build_local_section_plan(deck)

    assert [section["start_slide_id"] for section in plan["sections"]] == [1, 2, 4]
    assert plan["sections"][1]["slide_ids"] == [2, 3]
    assert [item["title"] for item in plan["outline_items"]] == ["Replication", "Quorum"]


def test_local_section_plan_names_first_section_from_outline_when_title_missing(tmp_path):
    deck = Deck(
        source_path="lecture.pdf",
        source_type="pdf",
        pages=[
            SlidePage(
                slide_id=1,
                title="Distributed Systems",
                text_blocks=[
                    TextBlock(
                        id="s1_t1",
                        type="paragraph",
                        content="目录\nContents\n1\n复制与一致性基础\n2\n数据为中心的一致性模型",
                    )
                ],
            ),
            SlidePage(slide_id=2, title="为什么要复制数据？", text_blocks=[TextBlock(id="s2_t1", type="paragraph", content="Replica")]),
            SlidePage(
                slide_id=3,
                title="数据为中心的一致性模型",
                text_blocks=[TextBlock(id="s3_t1", type="paragraph", content="Consistency")],
            ),
        ],
    )

    plan = build_local_section_plan(deck)

    assert [section["start_slide_id"] for section in plan["sections"]] == [1, 3]
    assert plan["sections"][0]["title"] == "复制与一致性基础"
    assert plan["sections"][1]["title"] == "数据为中心的一致性模型"


def test_llm_section_plan_is_cached_and_normalized(tmp_path, monkeypatch):
    deck = Deck(
        source_path="lecture.pdf",
        source_type="pdf",
        pages=[
            SlidePage(slide_id=1, title="Intro", text_blocks=[TextBlock(id="s1_t1", type="paragraph", content="Intro")]),
            SlidePage(slide_id=2, title="Replication", text_blocks=[TextBlock(id="s2_t1", type="paragraph", content="Replica")]),
            SlidePage(slide_id=3, title="Quorum", text_blocks=[TextBlock(id="s3_t1", type="paragraph", content="Quorum")]),
            SlidePage(slide_id=4, title="Summary", text_blocks=[TextBlock(id="s4_t1", type="paragraph", content="Summary")]),
        ],
    )
    calls = []

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def generate_with_usage(self, prompt, system_prompt=None):
            calls.append(prompt)

            class Result:
                text = '{"sections":[{"title":"Intro","start_slide_id":1,"reason":"opening"},{"title":"Quorum","start_slide_id":3,"reason":"new topic"}]}'
                usage = {"input_tokens": 10, "output_tokens": 8, "total_tokens": 18}

            return Result()

    monkeypatch.setattr("slidenote.sections.LLMClient", FakeClient)
    first = build_section_plan(deck, tmp_path, mode="llm", use_llm=True, provider="openai", api_key="test", cache_dir=tmp_path / "cache")

    assert first["method"] == "llm"
    assert [section["slide_ids"] for section in first["sections"]] == [[1, 2], [3, 4]]
    assert first["summary"]["llm_call"] is True

    class FailingClient:
        def __init__(self, **kwargs):
            raise AssertionError("cache hit should not instantiate client")

    monkeypatch.setattr("slidenote.sections.LLMClient", FailingClient)
    second = build_section_plan(deck, tmp_path, mode="llm", use_llm=True, provider="openai", cache_dir=tmp_path / "cache")

    assert second["summary"]["local_cache_hits"] == 1
    assert second["summary"]["llm_call"] is False
    assert len(calls) == 1
