import io
import json
import random
import subprocess

import fitz
from PIL import Image

from slidenote.extractors import extract_deck
from slidenote.extractors.pdf import extract_pdf
from slidenote.models import Deck, SlidePage
from slidenote.parser_adapters import available_parser_choices, parser_adapter_infos, parser_adapters


def _png_bytes(size: int, color: str) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (size, size), color).save(buffer, format="PNG")
    return buffer.getvalue()


def _noisy_png_bytes(size: int) -> bytes:
    """Random pixels so the compressed file stays well above the tiny_file threshold."""
    pixels = bytearray(random.Random(42).randbytes(size * size * 3))
    buffer = io.BytesIO()
    Image.frombytes("RGB", (size, size), bytes(pixels)).save(buffer, format="PNG")
    return buffer.getvalue()


def test_extract_pdf_skips_tiny_images_without_writing_them(tmp_path):
    """Tiny decorations must be classified from xref metadata and never decoded to disk."""
    source = tmp_path / "images.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_image(fitz.Rect(50, 50, 90, 90), stream=_png_bytes(8, "red"))  # min_dim 8 < 24
    page.insert_image(fitz.Rect(150, 50, 300, 200), stream=_noisy_png_bytes(120))  # real content image
    doc.save(str(source))
    doc.close()

    deck = extract_pdf(source, tmp_path / "out")

    page_images = deck.pages[0].images
    assert len(page_images) == 2
    tiny = next(image for image in page_images if image.role == "decorative")
    content = next(image for image in page_images if image.role == "content")
    assert tiny.ignored is True
    assert tiny.ignore_reason in {"tiny_area", "tiny_dimension", "thin_decoration"}
    assert not (tmp_path / "out" / tiny.path).exists(), "tiny image must not be written to disk"
    assert content.ignored is False
    assert (tmp_path / "out" / content.path).exists()
    # Only the real image should be on disk.
    written = list((tmp_path / "out" / "images").glob("slide1_img*"))
    assert len(written) == 1


def test_extract_pdf_parallel_keeps_page_order_and_content(tmp_path):
    """Page-parallel extraction must return ordered pages with screenshots."""
    source = tmp_path / "multi.pdf"
    doc = fitz.open()
    for index in range(1, 7):
        page = doc.new_page()
        page.insert_text((72, 72), f"Slide {index} title")
        page.insert_text((72, 104), f"Body line for slide {index}")
    doc.save(str(source))
    doc.close()

    deck = extract_pdf(source, tmp_path / "out")

    assert [page.slide_id for page in deck.pages] == [1, 2, 3, 4, 5, 6]
    assert deck.pages[0].title == "Slide 1 title"
    assert deck.pages[5].title == "Slide 6 title"
    assert all(page.page_screenshot for page in deck.pages)
    assert (tmp_path / "out" / "screenshots" / "slide1.png").exists()
    assert (tmp_path / "out" / "screenshots" / "slide6.png").exists()
    assert any(block.content.startswith("Body line") for block in deck.pages[2].text_blocks)


def test_builtin_parser_adapter_delegates_by_suffix(tmp_path, monkeypatch):
    source = tmp_path / "lecture.pdf"
    source.write_bytes(b"%PDF")
    expected = Deck(source_path=str(source), source_type="pdf", pages=[SlidePage(slide_id=1, title="Intro")])

    monkeypatch.setattr("slidenote.extractors.pdf.extract_pdf", lambda input_path, output_root: expected)

    deck = extract_deck(source, tmp_path / "out", parser="builtin")

    assert deck is expected
    assert "parser_adapter:builtin" in deck.warnings
    assert "auto" in available_parser_choices()
    assert "docling" in {info.name for info in parser_adapter_infos()}


def test_external_cli_adapter_normalizes_slidenote_json_stdout(tmp_path, monkeypatch):
    source = tmp_path / "lecture.pdf"
    source.write_bytes(b"%PDF")
    adapter = parser_adapters()["docling"]
    payload = {
        "source_path": str(source),
        "source_type": "pdf",
        "pages": [
            {
                "slide_id": 1,
                "title": "Consensus",
                "text_blocks": [{"id": "s1_t1", "type": "heading", "content": "Consensus basics"}],
                "tables": [],
                "images": [],
            }
        ],
    }

    monkeypatch.setattr("slidenote.parser_adapters.find_executable", lambda candidates: "docling")
    monkeypatch.setattr(
        "slidenote.parser_adapters._run_command",
        lambda command: subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload), stderr=""),
    )

    deck = adapter.extract(source, tmp_path / "out")

    assert deck.pages[0].title == "Consensus"
    assert deck.pages[0].text_blocks[0].content == "Consensus basics"
    assert any("external parser adapter `docling`" in warning for warning in deck.warnings)


def test_external_cli_adapter_normalizes_generic_pages_stdout(tmp_path, monkeypatch):
    source = tmp_path / "lecture.pdf"
    source.write_bytes(b"%PDF")
    adapter = parser_adapters()["marker"]
    payload = {"pages": [{"page": 1, "blocks": [{"type": "heading", "text": "Transport Layer"}]}]}

    monkeypatch.setattr("slidenote.parser_adapters.find_executable", lambda candidates: "marker_single")
    monkeypatch.setattr(
        "slidenote.parser_adapters._run_command",
        lambda command: subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload), stderr=""),
    )

    deck = adapter.extract(source, tmp_path / "out")

    assert deck.pages[0].slide_id == 1
    assert deck.pages[0].title == "Transport Layer"
    assert deck.pages[0].text_blocks[0].content == "Transport Layer"
