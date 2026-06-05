from __future__ import annotations

import json
from pathlib import Path

import fitz
import pytest

from slidenote.cli import main
from slidenote.textbook import (
    build_textbook_chunks,
    build_textbook_index,
    build_textbook_sections,
    detect_textbook_toc,
)


def test_textbook_index_native_pdf_generates_rag_artifacts(tmp_path: Path):
    source = tmp_path / "textbook.pdf"
    _write_textbook_pdf(
        source,
        [
            ["Contents", "Chapter 1 Introduction .... 2", "Chapter 2 Advanced Topics .... 3"],
            ["Chapter 1 Introduction", "Reliable delivery uses sequence numbers and acknowledgements."],
            ["Chapter 2 Advanced Topics", "Congestion control adjusts sending rate according to feedback."],
        ],
    )
    out = tmp_path / "out"

    result = build_textbook_index(source, out, ocr="off", quiet=True)

    assert (out / "textbook_manifest.json").exists()
    assert (out / "textbook_pages.jsonl").exists()
    assert (out / "textbook_toc.json").exists()
    assert (out / "textbook_sections.json").exists()
    assert (out / "textbook_chunks.jsonl").exists()
    assert (out / "textbook_index.json").exists()
    assert (out / "textbook_report.md").exists()
    assert not (out / "ocr_usage.json").exists()
    assert result["manifest"]["counts"]["pages"] == 3
    assert result["manifest"]["counts"]["toc_entries"] == 2
    assert result["manifest"]["pipeline"]["note_generation"] == "not_connected"
    chunks = _read_jsonl(out / "textbook_chunks.jsonl")
    assert chunks
    assert chunks[0]["source_refs"][0]["type"] == "textbook_page"


def test_textbook_ocr_auto_only_runs_on_low_text_pages(tmp_path: Path, monkeypatch):
    source = tmp_path / "scan.pdf"
    _write_textbook_pdf(
        source,
        [
            [
                "Chapter 1 Native Text",
                "This page has enough native text for the extractor.",
                "It should not be sent to OCR during auto mode.",
                "The next blank page simulates a scanned page.",
            ],
            [],
        ],
    )

    calls: list[Path] = []

    class FakeOCRClient:
        def __init__(self, **kwargs):
            pass

        def recognize(self, image_path: Path):
            calls.append(image_path)

            class Result:
                text = "OCR recovered scanned theorem"
                usage = {"words_result_num": 4}
                raw = {"words_result_num": 4}

            return Result()

    monkeypatch.setattr("slidenote.textbook.OCRClient", FakeOCRClient)

    result = build_textbook_index(source, tmp_path / "out", ocr="auto", quiet=True)

    assert len(calls) == 1
    assert result["manifest"]["counts"]["ocr_targets"] == 1
    assert result["manifest"]["counts"]["ocr_api_calls"] == 1
    assert result["manifest"]["counts"]["low_text_pages"] == 1
    assert result["manifest"]["artifacts"]["ocr_usage"] == "ocr_usage.json"


def test_textbook_ocr_off_keeps_low_text_pages_without_api(tmp_path: Path):
    source = tmp_path / "low.pdf"
    _write_textbook_pdf(source, [[]])

    result = build_textbook_index(source, tmp_path / "out", ocr="off", quiet=True)

    assert result["manifest"]["counts"]["low_text_pages"] == 1
    assert result["manifest"]["counts"]["ocr_targets"] == 0
    assert result["manifest"]["artifacts"]["ocr_usage"] is None


def test_textbook_toc_detection_supports_chinese_and_english_patterns():
    pages = [
        {
            "physical_page": 1,
            "printed_page_label": "i",
            "combined_text": "目录\n第1章 绪论 ........ 3\n1.1 研究背景 ........ 4\nContents\nChapter 2 Methods .... 8",
        }
    ]

    toc = detect_textbook_toc(pages)

    titles = [entry["title"] for entry in toc["entries"]]
    assert "第1章 绪论" in titles
    assert "1.1 研究背景" in titles
    assert "Chapter 2 Methods" in titles
    assert toc["summary"]["has_toc"] is True


def test_textbook_sections_fallback_to_headings_without_toc():
    pages = [
        {"physical_page": 1, "printed_page_label": "1", "combined_text": "Chapter 1 Intro\nNative text"},
        {"physical_page": 2, "printed_page_label": "2", "combined_text": "More text"},
        {"physical_page": 3, "printed_page_label": "3", "combined_text": "Chapter 2 Next\nMore text"},
    ]
    toc = detect_textbook_toc(pages)

    sections = build_textbook_sections(pages, toc)

    assert [section["title"] for section in sections] == ["Chapter 1 Intro", "Chapter 2 Next"]
    assert sections[0]["start_page"] == 1
    assert sections[0]["end_page"] == 2


def test_textbook_chunk_ids_are_stable():
    pages = [{"physical_page": 1, "printed_page_label": "1", "combined_text": "Chapter 1 Intro\n" + "A" * 500}]
    sections = [{"section_id": "tb_sec001", "title": "Chapter 1 Intro", "start_page": 1, "end_page": 1}]

    first = build_textbook_chunks(pages, sections, source_hash="sha256:" + "a" * 64)
    second = build_textbook_chunks(pages, sections, source_hash="sha256:" + "a" * 64)

    assert first[0]["chunk_id"] == second[0]["chunk_id"]
    assert first[0]["content_hash"] == second[0]["content_hash"]


def test_textbook_index_cli_rejects_non_pdf(tmp_path: Path, capsys):
    source = tmp_path / "book.txt"
    source.write_text("text", encoding="utf-8")

    exit_code = main(["textbook-index", str(source), "--out", str(tmp_path / "out"), "--quiet"])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "only supports PDF" in captured.err


def test_textbook_index_cli_missing_ocr_key_is_friendly(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.delenv("BAIDU_OCR_API_KEY", raising=False)
    monkeypatch.delenv("BAIDU_OCR_SECRET_KEY", raising=False)
    source = tmp_path / "scan.pdf"
    _write_textbook_pdf(source, [[]])

    exit_code = main(["textbook-index", str(source), "--out", str(tmp_path / "out"), "--quiet"])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "textbook-index" in captured.err
    assert "BAIDU_OCR_API_KEY" in captured.err
    assert "--ocr off" in captured.err
    assert "Traceback" not in captured.err


def _write_textbook_pdf(path: Path, pages: list[list[str]]) -> None:
    doc = fitz.open()
    for lines in pages:
        page = doc.new_page(width=520, height=720)
        y = 72
        for line in lines:
            page.insert_text((72, y), line, fontsize=12)
            y += 28
    doc.save(path)
    doc.close()


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
