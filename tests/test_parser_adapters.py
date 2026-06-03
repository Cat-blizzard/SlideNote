import json
import subprocess

from slidenote.extractors import extract_deck
from slidenote.models import Deck, SlidePage
from slidenote.parser_adapters import available_parser_choices, parser_adapter_infos, parser_adapters


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
