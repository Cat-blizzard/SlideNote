"""Structure contract, document frame and finalize wiring tests (ported from the
reference-note baseline work)."""

from pathlib import Path

from slidenote.coverage import analyze_coverage
from slidenote.models import Deck, SlidePage, TextBlock
from slidenote.notes import NoteOptions, generate_notes_result


def test_human_note_structure_contract_matches_two_course_prototypes():
    from slidenote.notes.structure import assess_lecture_note_structure

    prototype_notes = [
        (
            "# Algorithms\n\n## 本讲目标\n\n理解线性搜索与二分搜索的适用条件，并比较它们的时间复杂度。\n\n"
            "## 搜索算法\n\n### 核心思路\n\n线性搜索逐项检查，二分搜索依赖有序数据并在每一步排除一半候选范围。\n\n"
            "### 运行示例\n\n在有序号码簿中查找姓名时，可以比较从头查找与反复对半缩小范围的过程。\n\n"
            "### 易错点\n\n二分搜索不能直接用于无序数据，排序成本也不能在分析整体任务时被忽略。\n\n"
            "## 本讲总结\n\n算法选择取决于数据前提、输入规模和需要执行查找的次数。\n\n"
            "## 章节自测\n\n为什么二分搜索每次比较后可以安全地丢弃一半候选项？"
        ),
        (
            "# Number Representation\n\n## 本讲目标\n\n理解有限位宽如何表示无符号数和有符号数，以及运算为何会溢出。\n\n"
            "## 二进制补码\n\n### 表示规则\n\n二进制位模式的含义取决于解释规则，二进制补码让加减法共享相同的硬件运算形式。\n\n"
            "### 推导示例\n\n可以用固定四位分别解释同一位模式的无符号值和补码值，观察最高位权重的变化。\n\n"
            "### 常见误解\n\n位模式本身没有天然正负号；忽略位宽会导致对取值范围和溢出的错误判断。\n\n"
            "## 本讲总结\n\n表示范围由位宽和编码约定共同决定，运算结果也必须在同一位宽下解释。\n\n"
            "## 章节自测\n\n为什么同一个四位位模式在无符号解释和补码解释下可能代表不同整数？"
        ),
    ]

    for markdown in prototype_notes:
        assessment = assess_lecture_note_structure(markdown)
        assert assessment["passed"] is True
        assert assessment["score"] == 1.0


def test_document_frame_is_idempotent_and_preserves_course_content():
    from slidenote.notes.document_frame import ensure_lecture_document_frame
    from slidenote.notes.structure import assess_lecture_note_structure

    original = (
        "# Algorithms\n\n"
        "## 一、Linear Search\n\n"
        "### 执行示例\n\n逐项检查数组。 <!-- slidenote-source: p1:s1_t1 -->\n\n"
        "```c\nfor (int i = 0; i < n; i++) {}\n```\n\n"
        "### 易错点\n\n没有排序信息时不能安全丢弃一半候选。\n\n"
        "![搜索过程](notes.assets/images/search.png)\n"
    )
    brief = {
        "brief": {
            "core_questions": ["线性搜索与二分搜索的适用条件有什么不同？"],
            "chapter_outline": [
                {"title": "Linear Search", "summary": "逐项检查候选，直到命中或遍历结束。"}
            ],
        }
    }

    framed = ensure_lecture_document_frame(original, brief, "zh")
    assert ensure_lecture_document_frame(framed, brief, "zh") == framed
    assert framed.count("# Algorithms") == 1
    assert framed.count("## 本讲目标") == 1
    assert framed.count("## 本讲总结") == 1
    assert framed.count("## 章节自测") == 1
    assert "for (int i = 0; i < n; i++) {}" in framed
    assert "![搜索过程](notes.assets/images/search.png)" in framed
    assert "<!-- slidenote-source: p1:s1_t1 -->" in framed
    assert assess_lecture_note_structure(framed)["passed"] is True


def test_structure_contract_rejects_page_listing_and_repeated_global_sections():
    from slidenote.notes.structure import assess_lecture_note_structure

    mechanical = (
        "# Lecture\n\n"
        "## 本讲目标\n\n理解搜索。\n\n"
        "## 第 1 页：搜索\n\n逐项检查候选。\n\n"
        "## 本讲目标\n\n再次理解搜索。\n\n"
        "## 本讲总结\n\n搜索需要明确条件。\n\n"
        "## 章节自测\n\n搜索何时结束？"
    )
    assessment = assess_lecture_note_structure(mechanical)
    assert assessment["passed"] is False
    assert assessment["mechanical_repetition_pass"] is False
    assert assessment["page_headings"] == ["第 1 页：搜索"]
    assert assessment["repeated_global_sections"]["orientation"] == 2


def test_structure_contract_rejects_false_positive_headings_and_repeated_templates():
    from slidenote.notes.structure import assess_lecture_note_structure

    body = "这里是一段明显超过十二个字符的有效正文内容，用于结构契约测试。"
    slide_heading = (
        f"# X\n\n## 本讲目标\n\n{body}\n\n## Slide 1 - Topic\n\n{body}\n\n"
        f"## 本讲总结\n\n{body}\n\n## 章节自测\n\n{body}"
    )
    slide_assessment = assess_lecture_note_structure(slide_heading)
    assert slide_assessment["passed"] is False
    assert slide_assessment["page_headings"] == ["Slide 1 - Topic"]

    example_only = (
        f"# X\n\n## 本讲目标\n\n{body}\n\n## 例子与应用\n\n{body}\n\n"
        f"## 本讲总结\n\n{body}\n\n## 章节自测\n\n{body}"
    )
    example_assessment = assess_lecture_note_structure(example_only)
    assert "topic_sections" in example_assessment["missing_slots"]

    negated_summary = (
        f"# X\n\n## 本讲目标\n\n{body}\n\n## Topic\n\n{body}\n\n"
        f"## 这不是总结\n\n{body}\n\n## 章节自测\n\n{body}"
    )
    negated_assessment = assess_lecture_note_structure(negated_summary)
    assert "summary" in negated_assessment["missing_slots"]

    repeated_template = (
        f"# X\n\n## 本讲目标\n\n{body}\n\n## Topic A\n\n### 核心概念\n\n{body}\n\n"
        f"### 易错点\n\n{body}\n\n## Topic B\n\n### 核心概念\n\n{body}\n\n### 易错点\n\n{body}\n\n"
        f"## 本讲总结\n\n{body}\n\n## 章节自测\n\n{body}"
    )
    repeated_assessment = assess_lecture_note_structure(repeated_template)
    assert repeated_assessment["template_repetition_detected"] is True
    assert repeated_assessment["mechanical_repetition_pass"] is False
    assert repeated_assessment["passed"] is False


def test_structure_repair_rejects_severely_truncated_candidate(tmp_path, monkeypatch):
    from slidenote.notes.contexts import NoteContext
    from slidenote.notes.repair import _body_chars, _repair_note_structure_once

    body = "原始详细内容" * 400
    original = (
        f"# Demo\n\n## 本讲目标\n\n{body}\n\n## Topic\n\n{body}\n\n"
        f"![图](img.png)\n<!-- slidenote-source: p1:s1_t1 -->\n\n"
        f"## 第 1 页：机械标题\n\n{body}\n\n## 本讲总结\n\n{body}\n\n## 章节自测\n\n{body}"
    )
    short = "这是足够十二字符以上的简短占位正文。"
    candidate = (
        f"# Demo\n\n## 本讲目标\n\n{short}\n\n## Topic\n\n{short}\n\n"
        f"![图](img.png)\n<!-- slidenote-source: p1:s1_t1 -->\n\n"
        f"## 本讲总结\n\n{short}\n\n## 章节自测\n\n{short}"
    )

    def fake_generate(**_kwargs):
        return candidate, {"provider_usage": {"finish_reason": "stop"}}

    monkeypatch.setattr("slidenote.notes.repair._generate_cached_llm_text", fake_generate)
    deck = Deck(
        source_path="lecture.pdf",
        source_type="pdf",
        pages=[SlidePage(slide_id=1, title="Topic", text_blocks=[TextBlock(id="s1_t1", type="paragraph", content="Topic")])],
    )
    context = NoteContext(id="final", kind="final", title="final", pages=deck.pages)
    options = NoteOptions(
        use_llm=True,
        provider="openai",
        api_key="test",
        cache_mode="off",
        max_output_tokens=100_000,
        note_profile="lecture-notes",
    )
    repaired, record = _repair_note_structure_once(deck, context, original, tmp_path, None, options)

    assert record is not None
    assert record["accepted"] is False
    assert "body_truncated" in record["rejection_reasons"]
    assert repaired == original
    assert _body_chars(candidate) < _body_chars(original) * 0.01


def test_section_enrichment_prompt_uses_adaptive_not_six_field_structure():
    from slidenote.notes.contexts import NoteContext
    from slidenote.notes.prompt_templates import _llm_teaching_enrichment_prompt

    page = SlidePage(slide_id=1, title="Binary Search")
    context = NoteContext(id="sec1", kind="section", title="Binary Search", pages=[page])
    prompt = _llm_teaching_enrichment_prompt(
        context=context,
        woven_markdown="Binary search halves a sorted range.",
        page_markdown_by_slide={1: "Binary search halves a sorted range."},
        source_display="hidden",
        note_context="section",
        note_profile="lecture-notes",
        note_depth="very-detailed",
        note_language="zh",
        term_policy="bilingual",
    )

    assert "章节结构必须随内容变化" in prompt
    assert "默认不要在每章重复学习目标、本节小结和自测" in prompt
    assert "每个最终章节都必须包含" not in prompt


def test_two_course_benchmarks_receive_deterministic_document_frame_without_extra_repair(tmp_path, monkeypatch):
    from slidenote.notes.structure import assess_lecture_note_structure

    prompts = []

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def generate_with_usage(self, prompt):
            prompts.append(prompt)

            class Result:
                usage = {"input_tokens": 3, "output_tokens": 4, "total_tokens": 7}

            result = Result()
            algorithms = "Binary Search" in prompt or "算法只写成" in prompt
            if '"task": "page_lecture"' in prompt:
                result.text = (
                    "Binary Search 依赖有序输入，并通过对半缩小范围降低查找次数。 "
                    "<!-- slidenote-source: p1:s1_t1 -->"
                    if algorithms
                    else "Two's Complement 用固定位宽解释有符号整数。 <!-- slidenote-source: p1:s1_t1 -->"
                )
            elif '"task": "weave_page_lectures"' in prompt:
                result.text = (
                    "算法只写成逐页摘要，没有形成完整学习结构。 <!-- slidenote-source: p1:s1_t1 -->"
                    if algorithms
                    else "数值表示只有一段简短摘要。 <!-- slidenote-source: p1:s1_t1 -->"
                )
            elif '"task": "teaching_enrichment"' in prompt:
                result.text = (
                    "### 核心概念\n\n二分搜索反复排除一半候选范围。 <!-- slidenote-source: p1:s1_t1 -->"
                    if algorithms
                    else "### 核心概念\n\n补码用于解释固定位宽的有符号整数。 <!-- slidenote-source: p1:s1_t1 -->"
                )
            elif '"task": "repair_human_note_structure"' in prompt:
                topic = "二分搜索" if algorithms else "二进制补码"
                result.text = (
                    f"### 学习目标\n\n理解{topic}解决的问题、适用条件和基本推理过程。 <!-- slidenote-source: p1:s1_t1 -->\n\n"
                    f"### 核心概念与机制\n\n{topic}的定义、前提和运作方式需要作为同一条知识链理解。 <!-- slidenote-source: p1:s1_t1 -->\n\n"
                    f"### 例子与应用\n\n用一个不引入额外事实的最小例子检查{topic}如何作用于输入。 <!-- slidenote-source: p1:s1_t1 -->\n\n"
                    "### 易错点\n\n常见错误是忽略适用前提，只记结论而没有检查推理条件。 <!-- slidenote-source: p1:s1_t1 -->\n\n"
                    f"### 本节小结\n\n{topic}应从问题、机制、条件和结果四个方面连贯掌握。 <!-- slidenote-source: p1:s1_t1 -->\n\n"
                    f"### 自测问题\n\n你能说明{topic}成立所依赖的关键条件吗？ <!-- slidenote-source: p1:s1_t1 -->"
                )
            else:
                result.text = "unexpected"
            return result

    monkeypatch.setattr("slidenote.notes.llm_calls.LLMClient", FakeClient)
    decks = [
        Deck(
            source_path="cs50_algorithms.pdf",
            source_type="pdf",
            pages=[SlidePage(slide_id=1, title="Algorithms", text_blocks=[TextBlock(id="s1_t1", type="paragraph", content="Binary Search requires sorted input and runs in O(log n).")])],
        ),
        Deck(
            source_path="cs61c_number_representation.pptx",
            source_type="pptx",
            pages=[SlidePage(slide_id=1, title="Number Representation", text_blocks=[TextBlock(id="s1_t1", type="paragraph", content="Two's Complement represents signed integers with a fixed bit width.")])],
        ),
    ]

    for index, deck in enumerate(decks):
        result = generate_notes_result(
            deck,
            tmp_path / f"benchmark_{index}",
            use_llm=True,
            provider="openai",
            api_key="test",
            cache_mode="off",
            note_strategy="lecture-weave",
            note_profile="lecture-notes",
            note_context="document",
        )
        assert assess_lecture_note_structure(result.markdown)["passed"] is True
        assert result.llm_usage["summary"]["repair_calls"] == 0
        assert result.markdown.count("## 本讲目标") == 1
        assert result.markdown.count("## 本讲总结") == 1
        assert result.markdown.count("## 章节自测") == 1

    assert sum('"task": "repair_human_note_structure"' in prompt for prompt in prompts) == 0


def test_structure_repair_runs_only_when_contract_fails_and_keeps_safe_candidate(tmp_path, monkeypatch):
    """The structure repair fires on mechanical listing and rejects a regressing candidate."""
    from slidenote.notes.structure import assess_lecture_note_structure

    prompts = []

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def generate_with_usage(self, prompt):
            prompts.append(prompt)

            class Result:
                usage = {"input_tokens": 3, "output_tokens": 4, "total_tokens": 7}

            result = Result()
            if '"task": "page_lecture"' in prompt:
                result.text = "第 1 页讲了搜索。 <!-- slidenote-source: p1:s1_t1 -->"
            elif '"task": "weave_page_lectures"' in prompt:
                result.text = "## 第 1 页：搜索\n\n逐项检查候选。 <!-- slidenote-source: p1:s1_t1 -->"
            elif '"task": "teaching_enrichment"' in prompt:
                result.text = "## 第 1 页：搜索\n\n逐项检查候选。 <!-- slidenote-source: p1:s1_t1 -->"
            elif '"task": "repair_human_note_structure"' in prompt:
                # A candidate that does NOT improve the structure must be rejected.
                result.text = "## 第 1 页：搜索\n\n逐项检查候选。 <!-- slidenote-source: p1:s1_t1 -->"
            else:
                result.text = "unexpected"
            return result

    monkeypatch.setattr("slidenote.notes.llm_calls.LLMClient", FakeClient)
    deck = Deck(
        source_path="lecture.pdf",
        source_type="pdf",
        pages=[SlidePage(slide_id=1, title="搜索", text_blocks=[TextBlock(id="s1_t1", type="paragraph", content="Binary Search")])],
    )
    result = generate_notes_result(
        deck,
        tmp_path,
        use_llm=True,
        provider="openai",
        api_key="test",
        cache_mode="off",
        note_strategy="lecture-weave",
        note_profile="lecture-notes",
        note_context="document",
    )
    assert sum('"task": "repair_human_note_structure"' in prompt for prompt in prompts) == 1
    assert result.llm_usage["summary"]["repair_calls"] == 1
    # The regressing candidate was rejected; the frame still guarantees the slots.
    assert result.markdown.count("## 本讲目标") == 1
    assessment = assess_lecture_note_structure(result.markdown)
    assert assessment["passed"] is False
    assert assessment["page_headings"] == ["第 1 页：搜索"]
    assert "第 1 页：搜索" in result.markdown


def test_quality_report_contains_structure_contract():
    from slidenote.notes.quality import build_note_quality_report

    deck = Deck(source_path="lecture.pdf", source_type="pdf", pages=[SlidePage(slide_id=1, title="T")])
    mechanical = (
        "# Lecture\n\n"
        "## 本讲目标\n\n理解搜索算法的适用条件与基本推理过程。\n\n"
        "## 第 1 页：搜索\n\n逐项检查候选，直到命中或遍历结束为止。\n\n"
        "## 本讲目标\n\n再次理解搜索算法的适用条件与基本推理过程。\n\n"
        "## 本讲总结\n\n搜索需要明确条件，命中或遍历结束都算完成。\n\n"
        "## 章节自测\n\n搜索何时可以安全地提前结束，何时必须遍历到末尾？"
    )
    report = build_note_quality_report(
        deck=deck,
        notes_markdown=mechanical,
        coverage_report=None,
        note_profile="lecture-notes",
        note_context="document",
        note_strategy="lecture-weave",
        note_depth="very-detailed",
    )
    assert report["structure_contract_pass"] is False
    assert report["mechanical_structure_pass"] is False
    assert report["summary"]["missing_structure_slots"] == ["topic_sections"]
    assert any("结构栏目" in suggestion for suggestion in report["suggested_repairs"])
    assert any("逐页标题" in suggestion for suggestion in report["suggested_repairs"])
