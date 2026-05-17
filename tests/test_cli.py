import json
from argparse import Namespace
from pathlib import Path

import fitz

from slidenote.cli import _apply_speed_mode_defaults, _parse_slide_ranges, main


def test_parse_slide_ranges():
    assert _parse_slide_ranges("1,3-5,8") == {1, 3, 4, 5, 8}
    assert _parse_slide_ranges(None) == set()


def test_speed_mode_fills_unset_limits():
    args = Namespace(
        speed_mode="fast",
        max_output_tokens=None,
        ocr_max_targets=None,
        ocr_max_edge=None,
        vision_max_targets=None,
        vision_max_edge=None,
        vision_max_output_tokens=None,
        vision_detail=None,
    )

    _apply_speed_mode_defaults(args)

    assert args.max_output_tokens == 2500
    assert args.vision_max_targets == 25
    assert args.vision_detail == "low"


def test_build_writes_progress_and_run_summary(tmp_path):
    source = tmp_path / "lecture.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Transport Layer")
    doc.save(source)
    doc.close()
    out = tmp_path / "out"

    exit_code = main(["build", str(source), "--out", str(out), "--quiet", "--vision", "off"])

    assert exit_code == 0
    progress = json.loads((out / "progress.json").read_text(encoding="utf-8"))
    run_summary = json.loads((out / "run_summary.json").read_text(encoding="utf-8"))
    source_map = json.loads((out / "source_map.json").read_text(encoding="utf-8"))
    page_modalities = json.loads((out / "page_modalities.json").read_text(encoding="utf-8"))
    sections = json.loads((out / "sections.json").read_text(encoding="utf-8"))
    image_importance = json.loads((out / "image_importance.json").read_text(encoding="utf-8"))
    assert progress["status"] == "complete"
    assert run_summary["counts"]["pages"] == 1
    assert run_summary["artifacts"]["progress"] == "progress.json"
    assert run_summary["artifacts"]["source_map"] == "source_map.json"
    assert run_summary["artifacts"]["page_modalities"] == "page_modalities.json"
    assert run_summary["artifacts"]["sections"] == "sections.json"
    assert run_summary["artifacts"]["image_importance"] == "image_importance.json"
    assert page_modalities["summary"]["pages_total"] == 1
    assert sections["summary"]["sections_total"] == 1
    assert image_importance["summary"]["images_total"] == 0
    assert run_summary["artifacts"]["note_assets"] == "notes.assets"
    assert source_map["default_display_mode"] == "hidden"
    assert run_summary["run"]["note_language"] == "zh"
    assert run_summary["run"]["term_policy"] == "bilingual"


def test_auto_figure_crop_with_vision_off_does_not_call_api(tmp_path):
    source = tmp_path / "lecture.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Transport Layer")
    doc.save(source)
    doc.close()
    out = tmp_path / "out"

    exit_code = main(["build", str(source), "--out", str(out), "--quiet", "--figure-crop", "auto", "--vision", "off"])

    assert exit_code == 0
    assert not (out / "figure_usage.json").exists()
    run_summary = json.loads((out / "run_summary.json").read_text(encoding="utf-8"))
    assert run_summary["figure_crop"] is None


def test_missing_default_vision_key_prints_text_mode_hint(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("QWEN_API_KEY", raising=False)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    source = tmp_path / "lecture.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "A short slide")
    doc.save(source)
    doc.close()
    out = tmp_path / "out"

    exit_code = main(
        [
            "build",
            str(source),
            "--out",
            str(out),
            "--quiet",
            "--use-llm",
            "--provider",
            "deepseek",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "当前开启了 vision/figure-crop" in captured.err
    assert "DASHSCOPE_API_KEY" in captured.err
    assert "--vision off --figure-crop off" in captured.err
    assert "Traceback" not in captured.err
    assert "Traceback" not in captured.out


def test_quality_first_defaults_are_exposed_by_parser():
    from slidenote.cli import _build_parser

    args = _build_parser().parse_args(["build", "lecture.pdf"])

    assert args.speed_mode == "quality"
    assert args.vision == "auto"
    assert args.vision_provider == "qwen"
    assert args.note_strategy == "lecture-weave"
    assert args.note_context == "section"
    assert args.note_depth == "detailed"
    assert args.note_language == "zh"
    assert args.term_policy == "bilingual"
    assert args.section_detection == "auto"
    assert args.image_ranking == "local"


def test_doctor_command_writes_json(tmp_path):
    report_path = tmp_path / "doctor.json"

    exit_code = main(["doctor", "--json", str(report_path)])

    assert exit_code == 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["schema_version"] == 1
    assert "checks" in report
    assert "recommended_actions" in report
    assert "gui" in report
    assert all("category" in check and "impact" in check for check in report["checks"])
    assert isinstance(report["gui"]["ready_for_local_parse"], bool)
