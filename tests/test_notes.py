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
    assert "![第 1 页截图](notes.assets/screenshots/slide1.png)" in result.markdown
    assert "![第 1 页图片](notes.assets/images/diagram.png)" in result.markdown
    assert (tmp_path / "notes.assets" / "screenshots" / "slide1.png").exists()
    assert (tmp_path / "notes.assets" / "images" / "diagram.png").exists()
    assert result.asset_warnings == []


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

    result = generate_notes_result(deck, tmp_path, use_llm=True, provider="openai", api_key="test")

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

    result = generate_notes_result(deck, tmp_path, use_llm=True, provider="openai", api_key="test")

    assert len(prompts) >= 2
    assert '"context_kind": "section"' in prompts[0]
    assert result.llm_usage["summary"]["contexts_total"] == len(prompts)
