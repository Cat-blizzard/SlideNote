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
                text = "TCP 是传输层协议。\n【对应 PPT：第 1 页，文本块 s1_t1】"
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
