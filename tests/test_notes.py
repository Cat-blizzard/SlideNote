from pathlib import Path

from slidenote.coverage import analyze_coverage
from slidenote.models import Deck, ImageAsset, SlidePage, TableBlock, TextBlock
from slidenote.notes import generate_notes, generate_notes_result


def test_local_notes_include_all_element_ids():
    deck = Deck(
        source_path="lecture.pptx",
        source_type="pptx",
        pages=[
            SlidePage(
                slide_id=1,
                title="Transport",
                text_blocks=[
                    TextBlock(id="s1_t1", type="title", content="Transport"),
                    TextBlock(id="s1_t2", type="bullet", content="TCP\nUDP"),
                ],
                tables=[TableBlock(id="s1_tbl1", rows=[["Protocol", "Feature"], ["TCP", "Reliable"]])],
                images=[ImageAsset(id="s1_img1", path="images/slide1_img1.png")],
            )
        ],
    )

    notes = generate_notes(deck, Path("out"))
    report = analyze_coverage(deck, notes)

    assert "本页主题是“Transport”。" in notes
    assert "【对应 PPT" not in notes
    assert "<!-- slidenote-source: p1:s1_t1" in notes
    assert report["missing"] == 0


def test_local_notes_include_ocr_and_visual_fields():
    deck = Deck(
        source_path="lecture.pptx",
        source_type="pptx",
        pages=[
            SlidePage(
                slide_id=2,
                page_screenshot="screenshots/slide2.png",
                page_ocr_text="TCP 三次握手\nSYN / ACK",
                page_visual_summary="截图展示了客户端与服务端之间的握手流程",
                images=[
                    ImageAsset(
                        id="s2_img1",
                        path="images/slide2_img1.png",
                        ocr_text="client -> server: SYN",
                        visual_summary="图中用箭头表示连接建立顺序",
                    )
                ],
            )
        ],
    )

    notes = generate_notes(deck, Path("out"))

    assert "页截图视觉解析" in notes
    assert "截图展示了客户端与服务端之间的握手流程" in notes
    assert "页截图 OCR 文字" in notes
    assert "TCP 三次握手" in notes
    assert "图片视觉解析" in notes
    assert "图中用箭头表示连接建立顺序" in notes
    assert "图片 OCR 文字" in notes
    assert "client -> server: SYN" in notes
    assert "s2_img1" in notes


def test_local_notes_skip_ignored_images_in_coverage():
    deck = Deck(
        source_path="lecture.pdf",
        source_type="pdf",
        pages=[
            SlidePage(
                slide_id=1,
                images=[
                    ImageAsset(id="s1_img1", path="images/icon.png", ignored=True, role="decorative", ignore_reason="tiny_area"),
                    ImageAsset(id="s1_img2", path="images/diagram.png"),
                ],
            )
        ],
    )

    notes = generate_notes(deck, Path("out"))
    report = analyze_coverage(deck, notes)

    assert "s1_img1" not in notes
    assert "s1_img2" in notes
    assert report["total"] == 1
    assert report["missing"] == 0


def test_local_notes_bundle_assets_and_use_renderable_image_links(tmp_path):
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    (image_dir / "diagram.png").write_bytes(b"fake")
    screenshots_dir = tmp_path / "screenshots"
    screenshots_dir.mkdir()
    (screenshots_dir / "slide1.png").write_bytes(b"fake")
    deck = Deck(
        source_path="lecture.pdf",
        source_type="pdf",
        pages=[
            SlidePage(
                slide_id=1,
                page_screenshot="screenshots/slide1.png",
                images=[ImageAsset(id="s1_img1", path="images/diagram.png")],
            )
        ],
    )

    result = generate_notes_result(deck, tmp_path)

    assert "`![" not in result.markdown
    assert "![第 1 页截图]" not in result.markdown
    assert "![第 1 页图片](notes.assets/images/diagram.png)" in result.markdown
    assert not (tmp_path / "notes.assets" / "screenshots" / "slide1.png").exists()
    assert (tmp_path / "notes.assets" / "images" / "diagram.png").exists()
    assert result.asset_warnings == []


def test_screenshot_policy_fallback_keeps_screenshot_when_no_local_image(tmp_path):
    screenshots_dir = tmp_path / "screenshots"
    screenshots_dir.mkdir()
    (screenshots_dir / "slide1.png").write_bytes(b"fake")
    deck = Deck(
        source_path="lecture.pdf",
        source_type="pdf",
        pages=[SlidePage(slide_id=1, page_screenshot="screenshots/slide1.png")],
    )

    result = generate_notes_result(deck, tmp_path)

    assert "![第 1 页截图](notes.assets/screenshots/slide1.png)" in result.markdown
    assert (tmp_path / "notes.assets" / "screenshots" / "slide1.png").exists()


def test_source_display_footnote_keeps_clean_page_reference():
    deck = Deck(
        source_path="lecture.pdf",
        source_type="pdf",
        pages=[SlidePage(slide_id=4, text_blocks=[TextBlock(id="s4_t1", type="paragraph", content="复制提高可靠性")])],
    )

    notes = generate_notes(deck, Path("out"), source_display="footnote")

    assert "（PPT 第 4 页）" in notes
    assert "<!-- slidenote-source: p4:s4_t1 -->" in notes
    assert "【对应 PPT" not in notes


def test_llm_generation_uses_local_cache(tmp_path, monkeypatch):
    deck = Deck(
        source_path="lecture.pptx",
        source_type="pptx",
        pages=[
            SlidePage(
                slide_id=1,
                text_blocks=[TextBlock(id="s1_t1", type="paragraph", content="TCP")],
            )
        ],
    )

    class FakeClient:
        supports_image_input = False

        def __init__(self, **kwargs):
            pass

        def generate_with_usage(self, prompt):
            class Result:
                text = "好的，这是根据您提供的 JSON 生成的课程笔记。\n\n# 课程笔记：Transport\n\nTCP 是传输层协议。\n【对应 PPT：第 1 页，文本块 s1_t1】\n\n`![图](notes.assets/images/a.png)`"
                usage = {"input_tokens": 10, "output_tokens": 8, "total_tokens": 18}

            return Result()

    monkeypatch.setattr("slidenote.notes.LLMClient", FakeClient)

    first = generate_notes_result(
        deck,
        tmp_path,
        use_llm=True,
        provider="openai",
        api_key="test-key",
        cache_dir=tmp_path / "cache",
        note_strategy="direct",
    )
    assert first.llm_usage["pages"][0]["cache_status"] == "miss"
    assert first.llm_usage["summary"]["llm_calls"] == 1
    assert "好的，这是" not in first.markdown
    assert "# 课程笔记" not in first.markdown
    assert "【对应 PPT" not in first.markdown
    assert "<!-- slidenote-source: p1:s1_t1 -->" in first.markdown
    assert "`![" not in first.markdown

    class FailingClient:
        def __init__(self, **kwargs):
            raise AssertionError("cache hit should not instantiate an LLM client")

    monkeypatch.setattr("slidenote.notes.LLMClient", FailingClient)

    second = generate_notes_result(
        deck,
        tmp_path,
        use_llm=True,
        provider="openai",
        cache_dir=tmp_path / "cache",
        note_strategy="direct",
    )
    assert second.llm_usage["pages"][0]["cache_status"] == "local_hit"
    assert second.llm_usage["summary"]["llm_calls"] == 0


def test_llm_prompt_uses_page_visual_summary():
    from slidenote.notes import _llm_page_prompt

    page = SlidePage(slide_id=3, page_visual_summary="图中展示 TCP 三次握手流程。")

    prompt = _llm_page_prompt(page)

    assert "page_visual_summary" in prompt
    assert "三次握手" in prompt


def test_llm_auto_context_uses_document_for_short_deck(tmp_path, monkeypatch):
    deck = Deck(
        source_path="short.pdf",
        source_type="pdf",
        pages=[
            SlidePage(slide_id=1, text_blocks=[TextBlock(id="s1_t1", type="paragraph", content="A")]),
            SlidePage(slide_id=2, text_blocks=[TextBlock(id="s2_t1", type="paragraph", content="B")]),
        ],
    )
    prompts = []

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def generate_with_usage(self, prompt):
            prompts.append(prompt)

            class Result:
                text = "## 短材料\n\nA 与 B 构成同一节内容。<!-- slidenote-source: p1:s1_t1 -->"
                usage = {}

            return Result()

    monkeypatch.setattr("slidenote.notes.LLMClient", FakeClient)

    result = generate_notes_result(deck, tmp_path, use_llm=True, provider="openai", api_key="test", note_strategy="direct", note_context="auto")

    assert len(prompts) == 1
    assert '"context_kind": "document"' in prompts[0]
    assert result.llm_usage["summary"]["contexts_total"] == 1


def test_llm_auto_context_uses_sections_for_large_deck(tmp_path, monkeypatch):
    deck = Deck(
        source_path="large.pdf",
        source_type="pdf",
        pages=[
            SlidePage(slide_id=i, text_blocks=[TextBlock(id=f"s{i}_t1", type="paragraph", content="A" * 50)])
            for i in range(1, 18)
        ],
    )
    prompts = []

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def generate_with_usage(self, prompt):
            prompts.append(prompt)

            class Result:
                text = "## 分组内容\n\n正文。"
                usage = {}

            return Result()

    monkeypatch.setattr("slidenote.notes.LLMClient", FakeClient)

    result = generate_notes_result(deck, tmp_path, use_llm=True, provider="openai", api_key="test", note_strategy="direct", note_context="auto")

    assert len(prompts) >= 2
    assert '"context_kind": "section"' in prompts[0]
    assert result.llm_usage["summary"]["contexts_total"] == len(prompts)


def test_llm_context_uses_supplied_section_plan(tmp_path, monkeypatch):
    deck = Deck(
        source_path="large.pdf",
        source_type="pdf",
        pages=[
            SlidePage(slide_id=i, text_blocks=[TextBlock(id=f"s{i}_t1", type="paragraph", content=f"Page {i}")])
            for i in range(1, 5)
        ],
    )
    section_plan = {
        "sections": [
            {"section_id": "sec_intro", "title": "Intro", "slide_ids": [1, 2]},
            {"section_id": "sec_apply", "title": "Apply", "slide_ids": [3, 4]},
        ]
    }
    prompts = []

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def generate_with_usage(self, prompt):
            prompts.append(prompt)

            class Result:
                text = "## Section\n\n正文。"
                usage = {}

            return Result()

    monkeypatch.setattr("slidenote.notes.LLMClient", FakeClient)

    result = generate_notes_result(
        deck,
        tmp_path,
        use_llm=True,
        provider="openai",
        api_key="test",
        note_strategy="direct",
        note_context="section",
        section_plan=section_plan,
    )

    assert len(prompts) == 2
    assert '"context_id": "sec_intro"' in prompts[0]
    assert '"slide_id": 1' in prompts[0]
    assert '"slide_id": 3' in prompts[1]
    assert result.llm_usage["summary"]["contexts_total"] == 2


def test_lecture_weave_generates_page_notes_then_weaves_sections(tmp_path, monkeypatch):
    deck = Deck(
        source_path="lecture.pdf",
        source_type="pdf",
        pages=[
            SlidePage(slide_id=1, title="Replication", text_blocks=[TextBlock(id="s1_t1", type="paragraph", content="Replica")]),
            SlidePage(slide_id=2, title="Quorum", text_blocks=[TextBlock(id="s2_t1", type="paragraph", content="Quorum")]),
        ],
    )
    prompts = []

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def generate_with_usage(self, prompt):
            prompts.append(prompt)

            class Result:
                usage = {"input_tokens": 3, "output_tokens": 4, "total_tokens": 7}

            result = Result()
            if '"task": "page_lecture"' in prompt and '"context_id": "p1"' in prompt:
                result.text = "## Replication\n\n副本用于提高可用性。<!-- slidenote-source: p1:s1_t1 -->"
            elif '"task": "page_lecture"' in prompt and '"context_id": "p2"' in prompt:
                result.text = "## Quorum\n\nQuorum 解释读写交集。<!-- slidenote-source: p2:s2_t1 -->"
            else:
                result.text = (
                    "好的，这是根据 JSON 生成的课程笔记。\n\n"
                    "# 课程笔记：副本与 Quorum\n\n"
                    "## 副本与 Quorum\n\n"
                    "副本先解决可用性问题。<!-- slidenote-source: p1:s1_t1 -->\n\n"
                    "### 读写交集\n\n"
                    "Quorum 进一步说明读写交集。<!-- slidenote-source: p2:s2_t1 -->\n\n"
                    "![](notes.assets/images/quorum.png)\n\n"
                    "## 生成信息\n\n"
                    "- LLM provider：fake"
                )
            return result

    monkeypatch.setattr("slidenote.notes.LLMClient", FakeClient)

    result = generate_notes_result(
        deck,
        tmp_path,
        use_llm=True,
        provider="openai",
        api_key="test",
        note_strategy="lecture-weave",
        note_depth="detailed",
        note_context="document",
    )

    assert len(prompts) == 3
    assert '"task": "page_lecture"' in prompts[0]
    assert '"task": "page_lecture"' in prompts[1]
    assert '"task": "weave_page_lectures"' in prompts[2]
    assert all("输出语言" in prompt for prompt in prompts)
    assert all("术语策略" in prompt for prompt in prompts)
    assert result.page_notes is not None
    assert result.weave_report is not None
    assert result.page_notes_markdown is not None
    assert result.llm_usage["summary"]["page_note_calls"] == 2
    assert result.llm_usage["summary"]["weave_calls"] == 1
    assert result.llm_usage["request"]["note_language"] == "zh"
    assert result.llm_usage["request"]["term_policy"] == "bilingual"
    assert result.page_notes["request"]["note_language"] == "zh"
    assert result.page_notes["request"]["term_policy"] == "bilingual"
    assert result.weave_report["request"]["note_language"] == "zh"
    assert result.weave_report["request"]["term_policy"] == "bilingual"
    assert "好的，这是" not in result.markdown
    assert "# 课程笔记" not in result.markdown
    assert "## 生成信息" not in result.markdown
    assert "### 读写交集" in result.markdown
    assert "![](notes.assets" not in result.markdown
    assert "![图示](notes.assets/images/quorum.png)" in result.markdown
    assert sum(1 for line in result.markdown.splitlines() if line.startswith("# ")) == 1
    assert "<!-- slidenote-source: p1:s1_t1 -->" in result.markdown
    assert "<!-- slidenote-source: p2:s2_t1 -->" in result.markdown
    assert analyze_coverage(deck, result.markdown)["missing"] == 0


def test_section_context_notes_use_numbered_outline_headings(tmp_path, monkeypatch):
    deck = Deck(
        source_path="ch06.pdf",
        source_type="pdf",
        pages=[
            SlidePage(slide_id=1, title="复制与一致性基础", text_blocks=[TextBlock(id="s1_t1", type="paragraph", content="Replica")]),
            SlidePage(slide_id=2, title="数据为中心的一致性模型", text_blocks=[TextBlock(id="s2_t1", type="paragraph", content="Consistency")]),
        ],
    )
    section_plan = {
        "sections": [
            {"section_id": "sec1", "title": "复制与一致性基础", "slide_ids": [1]},
            {"section_id": "sec2", "title": "数据为中心的一致性模型", "slide_ids": [2]},
        ]
    }

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def generate_with_usage(self, prompt):
            class Result:
                usage = {}

            result = Result()
            if '"context_id": "sec1"' in prompt:
                result.text = "## 复制与一致性基础\n\n### 为什么复制\n\n复制提高可靠性。<!-- slidenote-source: p1:s1_t1 -->"
            else:
                result.text = "## 数据为中心的一致性模型\n\n一致性模型描述读写可见性。<!-- slidenote-source: p2:s2_t1 -->"
            return result

    monkeypatch.setattr("slidenote.notes.LLMClient", FakeClient)

    result = generate_notes_result(
        deck,
        tmp_path,
        use_llm=True,
        provider="openai",
        api_key="test",
        note_strategy="direct",
        note_context="section",
        section_plan=section_plan,
    )

    assert "## 一、复制与一致性基础" in result.markdown
    assert "## 二、数据为中心的一致性模型" in result.markdown
    assert "### 为什么复制" in result.markdown
    assert result.markdown.count("## 复制与一致性基础") == 0
    assert analyze_coverage(deck, result.markdown)["missing"] == 0


def test_lecture_weave_cache_and_refresh_are_split_by_page_and_weave(tmp_path, monkeypatch):
    deck = Deck(
        source_path="lecture.pdf",
        source_type="pdf",
        pages=[
            SlidePage(slide_id=1, text_blocks=[TextBlock(id="s1_t1", type="paragraph", content="A")]),
            SlidePage(slide_id=2, text_blocks=[TextBlock(id="s2_t1", type="paragraph", content="B")]),
        ],
    )
    cache_dir = tmp_path / "cache"

    class FirstClient:
        def __init__(self, **kwargs):
            pass

        def generate_with_usage(self, prompt):
            class Result:
                usage = {}

            result = Result()
            if '"task": "page_lecture"' in prompt and '"context_id": "p1"' in prompt:
                result.text = "A detail. <!-- slidenote-source: p1:s1_t1 -->"
            elif '"task": "page_lecture"' in prompt and '"context_id": "p2"' in prompt:
                result.text = "B detail. <!-- slidenote-source: p2:s2_t1 -->"
            else:
                result.text = "A detail. <!-- slidenote-source: p1:s1_t1 -->\n\nB detail. <!-- slidenote-source: p2:s2_t1 -->"
            return result

    monkeypatch.setattr("slidenote.notes.LLMClient", FirstClient)
    generate_notes_result(
        deck,
        tmp_path,
        use_llm=True,
        provider="openai",
        api_key="test",
        note_strategy="lecture-weave",
        note_context="document",
        cache_dir=cache_dir,
    )

    class FailingClient:
        def __init__(self, **kwargs):
            raise AssertionError("cache hit should not call the LLM")

    monkeypatch.setattr("slidenote.notes.LLMClient", FailingClient)
    cached = generate_notes_result(
        deck,
        tmp_path,
        use_llm=True,
        provider="openai",
        api_key="test",
        note_strategy="lecture-weave",
        note_context="document",
        cache_dir=cache_dir,
    )
    assert cached.llm_usage["summary"]["llm_calls"] == 0

    calls = []

    class RefreshClient(FirstClient):
        def generate_with_usage(self, prompt):
            calls.append(prompt)
            return super().generate_with_usage(prompt)

    monkeypatch.setattr("slidenote.notes.LLMClient", RefreshClient)
    refreshed = generate_notes_result(
        deck,
        tmp_path,
        use_llm=True,
        provider="openai",
        api_key="test",
        note_strategy="lecture-weave",
        note_context="document",
        cache_dir=cache_dir,
        refresh_slide_ids={2},
    )

    assert len(calls) == 2
    assert refreshed.llm_usage["summary"]["page_note_calls"] == 1
    assert refreshed.llm_usage["summary"]["weave_calls"] == 1
