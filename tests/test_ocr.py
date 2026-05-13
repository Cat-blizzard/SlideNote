from pathlib import Path

import pytest
from PIL import Image

from slidenote.models import Deck, SlidePage, TextBlock
from slidenote.ocr import enrich_deck_with_ocr, select_ocr_targets


def test_ocr_auto_skips_text_rich_page(tmp_path):
    screenshot = tmp_path / "screenshots" / "slide1.png"
    screenshot.parent.mkdir()
    Image.new("RGB", (800, 450), "white").save(screenshot)
    deck = Deck(
        source_path="lecture.pdf",
        source_type="pdf",
        pages=[
            SlidePage(
                slide_id=1,
                text_blocks=[TextBlock(id="s1_t1", type="paragraph", content="A" * 120)],
                page_screenshot="screenshots/slide1.png",
            )
        ],
    )

    targets = select_ocr_targets(deck, tmp_path, mode="auto", min_text_chars=80)

    assert targets == []


def test_ocr_auto_selects_low_text_page_screenshot(tmp_path):
    screenshot = tmp_path / "screenshots" / "slide1.png"
    screenshot.parent.mkdir()
    Image.new("RGB", (800, 450), "white").save(screenshot)
    deck = Deck(source_path="lecture.pdf", source_type="pdf", pages=[SlidePage(slide_id=1, page_screenshot="screenshots/slide1.png")])

    targets = select_ocr_targets(deck, tmp_path, mode="auto", min_text_chars=80)

    assert len(targets) == 1
    assert targets[0].kind == "page_screenshot"


def test_ocr_enrichment_writes_text_and_uses_cache(tmp_path, monkeypatch):
    screenshot = tmp_path / "screenshots" / "slide1.png"
    screenshot.parent.mkdir()
    Image.new("RGB", (800, 450), "white").save(screenshot)
    deck = Deck(source_path="lecture.pdf", source_type="pdf", pages=[SlidePage(slide_id=1, page_screenshot="screenshots/slide1.png")])

    class FakeOCRClient:
        def __init__(self, **kwargs):
            pass

        def recognize(self, image_path: Path):
            class Result:
                text = "TCP 三次握手"
                usage = {"words_result_num": 1}
                raw = {"words_result_num": 1}

            return Result()

    monkeypatch.setattr("slidenote.ocr.OCRClient", FakeOCRClient)
    report = enrich_deck_with_ocr(deck, tmp_path, mode="auto", provider="baidu", api_key="k", secret_key="s", cache_dir=tmp_path / "cache")

    assert report["summary"]["api_calls"] == 1
    assert deck.pages[0].page_ocr_text == "TCP 三次握手"
    assert deck.pages[0].page_ocr_status == "parsed"

    class FailingOCRClient:
        def __init__(self, **kwargs):
            raise AssertionError("cache hit should not instantiate an OCR client")

    monkeypatch.setattr("slidenote.ocr.OCRClient", FailingOCRClient)
    second = enrich_deck_with_ocr(deck, tmp_path, mode="auto", provider="baidu", cache_dir=tmp_path / "cache")

    assert second["summary"]["local_cache_hits"] == 1
    assert second["summary"]["api_calls"] == 0


def test_ocr_enrichment_cleans_temp_image_on_api_error(tmp_path, monkeypatch):
    screenshot = tmp_path / "screenshots" / "slide1.png"
    screenshot.parent.mkdir()
    Image.new("RGB", (800, 450), "white").save(screenshot)
    deck = Deck(source_path="lecture.pdf", source_type="pdf", pages=[SlidePage(slide_id=1, page_screenshot="screenshots/slide1.png")])
    seen: dict[str, Path] = {}

    class FailingOCRClient:
        def __init__(self, **kwargs):
            pass

        def recognize(self, image_path: Path):
            assert image_path.exists()
            seen["path"] = image_path
            raise RuntimeError("boom")

    monkeypatch.setattr("slidenote.ocr.OCRClient", FailingOCRClient)

    with pytest.raises(RuntimeError, match="boom"):
        enrich_deck_with_ocr(deck, tmp_path, mode="auto", provider="baidu", api_key="k", secret_key="s", cache_dir=tmp_path / "cache")

    assert seen["path"].exists() is False
