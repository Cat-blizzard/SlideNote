import json

from slidenote.models import Deck, ImageAsset, SlidePage, TableBlock, TextBlock
from slidenote.study_pack import (
    build_study_pack,
    render_exam_html,
    render_exam_markdown,
    render_final_exam_answers_markdown,
    render_final_exam_markdown,
    render_review_markdown,
    render_wrong_answer_review_prompt,
)


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
                images=[ImageAsset(id="s1_img1", path="images/transport.png", caption="Transport diagram", visual_summary="The diagram contrasts reliable and best-effort transport.")],
            )
        ],
    )

    report = build_study_pack(deck, "## Transport\n\nTCP provides reliable ordered delivery.", tmp_path, review_mode="local", exam_mode="local", question_count=4)

    assert report is not None
    assert report["generator"] == "local"
    assert report["summary"]["review_items_total"] >= 2
    assert report["summary"]["questions_total"] == 4
    assert report["summary"]["question_quality_score"] is not None
    assert report["section_study_pack"]["sections"]
    assert report["exam_review_pack"]["question_quality"]["overall_score"] >= 0
    assert report["final_exam"]["mode"] == "mock_final"
    assert report["wrong_answer_review"]["prompt_template"]
    assert "Transport" in render_review_markdown(report)
    assert "答案与解析" in render_exam_markdown(report)
    assert "期末模拟卷" in render_final_exam_markdown(report)
    assert "答案与评分提示" in render_final_exam_answers_markdown(report)
    assert "错题复盘" in render_wrong_answer_review_prompt(report)
    assert "一键批改" in render_exam_html(report)
    assert "错题复盘" in render_exam_html(report)


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


def test_clean_inline_keeps_math_comparisons_but_strips_html_tags():
    from slidenote.study_pack.common import _clean_inline

    assert _clean_inline("if a < b and c > d then x") == "if a < b and c > d then x"
    assert _clean_inline("a<b and c>d") == "a<b and c>d"
    assert _clean_inline('<span class="x">TCP</span> <br/>三次握手') == "TCP 三次握手"


def test_normalize_exam_handles_malformed_choice_and_true_false_strings():
    from slidenote.study_pack import _normalize_exam

    raw = {
        "questions": [
            {"type": "choice", "question": "只有一个选项", "options": ["A"], "answer": "见解析", "explanation": "解释"},
            {"type": "choice", "question": "越界答案", "options": ["A", "B"], "answer": 9},
            {"type": "true_false", "question": "判断", "answer": "正确"},
            {"type": "true_false", "question": "判断2", "answer": "false"},
        ]
    }

    questions = _normalize_exam(raw, {"questions": []}, question_count=10)["questions"]

    assert questions[0]["type"] == "short"
    assert questions[0]["points"] == 6
    assert questions[0]["answer"] == "见解析"
    assert questions[1]["answer"] == 1
    assert questions[2]["answer"] is True
    assert questions[3]["answer"] is False


def test_local_questions_vary_correct_choice_and_true_false_answers():
    from slidenote.study_pack.questions import _local_questions

    items = [
        {"point": f"概念{index}", "explanation": f"概念{index}的解释内容，用于区分不同知识点。", "source_refs": [f"P{index}"]}
        for index in range(1, 9)
    ]

    questions = _local_questions(items, question_count=32)
    choice_answers = {question["answer"] for question in questions if question["type"] == "choice"}
    tf_answers = {question["answer"] for question in questions if question["type"] == "true_false"}

    assert len(choice_answers) > 1
    assert tf_answers == {True, False}
    for question in questions:
        if question["type"] == "choice":
            assert not any("只背" in option for option in question["options"])


def test_collect_study_items_respects_limit():
    from slidenote.study_pack import _collect_study_items

    deck = Deck(
        source_path="lecture.pdf",
        source_type="pdf",
        pages=[
            SlidePage(
                slide_id=index,
                title=f"Topic {index}",
                text_blocks=[TextBlock(id=f"s{index}_t1", type="paragraph", content=f"Protocol{index} guarantees property number {index} for every message.")],
                tables=[TableBlock(id=f"s{index}_tbl1", rows=[["k", "v"], ["a", str(index)]])],
            )
            for index in range(1, 11)
        ],
    )
    guard = {"items": [{"element_id": f"s{index}_t1", "slide_id": index, "must_explain": True, "confidence": 0.9} for index in range(1, 11)]}

    items = _collect_study_items(deck, "", guard, limit=5)

    assert len(items) == 5
    assert [item["source_refs"] for item in items] == [["P1"], ["P2"], ["P3"], ["P4"], ["P5"]]
