import json
from argparse import Namespace
from pathlib import Path

import fitz

from slidenote.cli import (
    _apply_build_preset_defaults,
    _apply_note_profile_defaults,
    _apply_speed_mode_defaults,
    _explicit_cli_options,
    _parse_slide_ranges,
    _resolve_api_concurrency,
    main,
)
from slidenote.notes import NoteGenerationResult


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


def test_api_concurrency_defaults_to_global_value():
    args = Namespace(
        concurrency=3,
        llm_concurrency=None,
        vision_concurrency=None,
        ocr_concurrency=None,
        figure_concurrency=None,
    )

    assert _resolve_api_concurrency(args) == {"llm": 3, "vision": 3, "ocr": 3, "figure": 3}


def test_api_concurrency_can_override_individual_stages():
    args = Namespace(
        concurrency=3,
        llm_concurrency=5,
        vision_concurrency=None,
        ocr_concurrency=0,
        figure_concurrency=-2,
    )

    assert _resolve_api_concurrency(args) == {"llm": 5, "vision": 3, "ocr": 1, "figure": 1}


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
    table_understanding = json.loads((out / "table_understanding.json").read_text(encoding="utf-8"))
    semantic_layout = json.loads((out / "semantic_layout.json").read_text(encoding="utf-8"))
    deck_understanding = json.loads((out / "deck_understanding.json").read_text(encoding="utf-8"))
    page_understanding = json.loads((out / "page_understanding.json").read_text(encoding="utf-8"))
    element_ir = json.loads((out / "element_ir.json").read_text(encoding="utf-8"))
    quality_report = json.loads((out / "quality_report.json").read_text(encoding="utf-8"))
    sections = json.loads((out / "sections.json").read_text(encoding="utf-8"))
    image_importance = json.loads((out / "image_importance.json").read_text(encoding="utf-8"))
    composite_figures = json.loads((out / "composite_figures.json").read_text(encoding="utf-8"))
    figure_grounding = json.loads((out / "figure_grounding.json").read_text(encoding="utf-8"))
    assert progress["status"] == "complete"
    assert run_summary["counts"]["pages"] == 1
    assert run_summary["artifacts"]["progress"] == "progress.json"
    assert run_summary["artifacts"]["source_map"] == "source_map.json"
    assert run_summary["artifacts"]["page_modalities"] == "page_modalities.json"
    assert run_summary["artifacts"]["table_understanding"] == "table_understanding.json"
    assert run_summary["artifacts"]["semantic_layout"] == "semantic_layout.json"
    assert run_summary["artifacts"]["deck_understanding"] == "deck_understanding.json"
    assert run_summary["artifacts"]["page_understanding"] == "page_understanding.json"
    assert run_summary["artifacts"]["element_ir"] == "element_ir.json"
    assert run_summary["artifacts"]["quality_report"] == "quality_report.json"
    assert run_summary["artifacts"]["registered"]["quality_report"] == "quality_report.json"
    assert run_summary["artifacts"]["registered"]["element_ir"] == "element_ir.json"
    assert run_summary["artifacts"]["sections"] == "sections.json"
    assert run_summary["artifacts"]["deck_brief"] is None
    assert run_summary["artifacts"]["image_importance"] == "image_importance.json"
    assert run_summary["artifacts"]["composite_figures"] == "composite_figures.json"
    assert run_summary["artifacts"]["figure_grounding"] == "figure_grounding.json"
    assert run_summary["artifacts"]["export_report"] is None
    assert page_modalities["summary"]["pages_total"] == 1
    assert table_understanding["summary"]["tables_total"] == 0
    assert run_summary["table_understanding"]["tables_total"] == 0
    assert semantic_layout["summary"]["pages_total"] == 1
    assert deck_understanding["summary"]["pages_total"] == 1
    assert page_understanding["summary"]["pages_total"] == 1
    assert run_summary["deck_understanding"]["pages_total"] == 1
    assert run_summary["page_understanding"]["pages_total"] == 1
    assert element_ir["schema_version"] == 1
    assert "normalized_bbox" in element_ir["schema_features"]
    assert element_ir["pages"][0]["slide_id"] == 1
    assert "mechanical_page_listing_score" in quality_report
    assert "explanation_depth_score" in quality_report
    assert "figure_integration_score" in quality_report
    first_element = element_ir["pages"][0]["elements"][0]
    assert first_element["role"]
    assert first_element["confidence"] >= 0.0
    assert first_element["reading_order"] == 1
    assert first_element["coverage_state"] in {
        "covered",
        "visible_covered",
        "marker_only",
        "missing",
        "missing_required",
    }
    assert "trace_covered" in first_element["coverage"]
    assert run_summary["semantic_layout"]["pages_total"] == 1
    assert sections["summary"]["sections_total"] == 1
    assert image_importance["summary"]["images_total"] == 0
    assert composite_figures["summary"]["composites_created"] == 0
    assert figure_grounding["summary"]["candidate_images"] == 0
    assert run_summary["artifacts"]["note_assets"] == "notes.assets"
    assert source_map["default_display_mode"] == "hidden"
    assert run_summary["run"]["preset"] == "auto"
    assert run_summary["run"]["parser"] == "auto"
    assert run_summary["run"]["note_language"] == "zh"
    assert run_summary["run"]["note_profile"] == "auto"
    assert run_summary["run"]["teaching_enrichment"] == "auto"
    assert run_summary["run"]["term_policy"] == "bilingual"
    assert run_summary["run"]["deck_brief"] == "auto"
    assert run_summary["run"]["semantic_layout"] == "auto"
    assert run_summary["run"]["api_concurrency"] == {"llm": 1, "vision": 1, "ocr": 1, "figure": 1}
    assert "slowest_stages" in run_summary["stage_timings"]
    assert not (out / "export_report.json").exists()


def test_api_concurrency_is_wired_to_build_stages(tmp_path, monkeypatch):
    source = tmp_path / "lecture.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Transport Layer")
    doc.save(source)
    doc.close()
    out = tmp_path / "out"
    seen = {}

    def fake_ocr(*args, **kwargs):
        seen["ocr"] = kwargs["concurrency"]
        return {"summary": {"api_calls": 0}}

    def fake_vision(*args, **kwargs):
        seen["vision"] = kwargs["concurrency"]
        return {"summary": {"llm_calls": 0}}

    def fake_figures(*args, **kwargs):
        seen["figure"] = kwargs["concurrency"]
        return {"summary": {"llm_calls": 0}}

    def fake_notes(*args, **kwargs):
        seen["llm"] = kwargs["concurrency"]
        return NoteGenerationResult(markdown="Transport Layer. <!-- slidenote-source: p1:s1_t1 -->")

    monkeypatch.setattr("slidenote.build.stages.enrich_deck_with_ocr", fake_ocr)
    monkeypatch.setattr("slidenote.build.stages.enrich_deck_with_vision", fake_vision)
    monkeypatch.setattr("slidenote.build.stages.enrich_deck_with_figures", fake_figures)
    monkeypatch.setattr("slidenote.build.stages.generate_notes_result", fake_notes)

    exit_code = main(
        [
            "build",
            str(source),
            "--out",
            str(out),
            "--quiet",
            "--use-llm",
            "--ocr",
            "auto",
            "--vision",
            "auto",
            "--figure-crop",
            "vision",
            "--content-guard",
            "off",
            "--deck-brief",
            "off",
            "--section-detection",
            "local",
            "--concurrency",
            "9",
            "--llm-concurrency",
            "5",
            "--vision-concurrency",
            "4",
            "--ocr-concurrency",
            "2",
            "--figure-concurrency",
            "3",
        ]
    )

    assert exit_code == 0
    assert seen == {"figure": 3, "ocr": 2, "vision": 4, "llm": 5}
    run_summary = json.loads((out / "run_summary.json").read_text(encoding="utf-8"))
    assert run_summary["run"]["concurrency"] == 9
    assert run_summary["run"]["api_concurrency"] == {"llm": 5, "vision": 4, "ocr": 2, "figure": 3}


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
    assert args.preset == "auto"
    assert args.parser == "auto"
    assert args.vision == "auto"
    assert args.vision_provider == "qwen"
    _apply_note_profile_defaults(args)
    assert args.note_strategy == "lecture-weave"
    assert args.note_context == "section"
    assert args.note_style == "article"
    assert args.note_profile == "auto"
    assert args.note_depth == "detailed"
    assert args.teaching_enrichment == "auto"
    assert args.deck_brief == "auto"
    assert args.note_language == "zh"
    assert args.term_policy == "bilingual"
    assert args.section_detection == "auto"
    assert args.semantic_layout == "auto"
    assert args.image_ranking == "local"
    assert args.composite_figures == "auto"
    assert args.figure_grounding == "auto"
    assert args.figure_placement == "inline"
    assert args.figure_audit == "local"
    assert args.content_guard == "auto"
    assert args.review_mode == "off"
    assert args.exam_mode == "off"
    assert args.exam_question_count == 12
    assert args.export is None
    assert args.export_toc == "auto"


def test_lecture_preset_maps_to_teacher_style_pipeline():
    from slidenote.cli import _build_parser

    argv = ["build", "lecture.pdf", "--preset", "lecture"]
    args = _build_parser().parse_args(argv)
    args._explicit_options = _explicit_cli_options(argv)
    _apply_build_preset_defaults(args)
    _apply_note_profile_defaults(args)

    assert args.preset == "lecture"
    assert args.speed_mode == "quality"
    assert args.note_profile == "lecture-notes"
    assert args.note_strategy == "lecture-weave"
    assert args.note_context == "section"
    assert args.note_style == "article"
    assert args.note_depth == "very-detailed"
    assert args.teaching_enrichment == "auto"
    assert args.deck_brief == "auto"
    assert args.content_guard == "auto"
    assert args.source_display == "hidden"


def test_preset_respects_explicit_lower_level_overrides():
    from slidenote.cli import _build_parser

    argv = [
        "build",
        "lecture.pdf",
        "--preset",
        "lecture",
        "--note-depth",
        "balanced",
        "--teaching-enrichment",
        "off",
        "--deck-brief",
        "off",
    ]
    args = _build_parser().parse_args(argv)
    args._explicit_options = _explicit_cli_options(argv)
    _apply_build_preset_defaults(args)
    _apply_note_profile_defaults(args)

    assert args.note_profile == "lecture-notes"
    assert args.note_depth == "balanced"
    assert args.teaching_enrichment == "off"
    assert args.deck_brief == "off"


def test_fast_preset_minimizes_extra_passes():
    from slidenote.cli import _build_parser

    argv = ["build", "lecture.pdf", "--preset", "fast"]
    args = _build_parser().parse_args(argv)
    args._explicit_options = _explicit_cli_options(argv)
    _apply_build_preset_defaults(args)

    assert args.speed_mode == "fast"
    assert args.note_strategy == "direct"
    assert args.note_depth == "balanced"
    assert args.teaching_enrichment == "off"
    assert args.deck_brief == "off"
    assert args.content_guard == "off"
    assert args.section_detection == "local"
    assert args.vision == "off"
    assert args.ocr == "off"
    assert args.figure_crop == "off"


def test_lecture_notes_profile_defaults_to_very_detailed():
    from slidenote.cli import _build_parser

    args = _build_parser().parse_args(["build", "lecture.pdf", "--note-profile", "lecture-notes"])
    _apply_note_profile_defaults(args)

    assert args.note_profile == "lecture-notes"
    assert args.note_depth == "very-detailed"


def test_explicit_note_depth_overrides_lecture_notes_profile():
    from slidenote.cli import _build_parser

    args = _build_parser().parse_args(
        ["build", "lecture.pdf", "--note-profile", "lecture-notes", "--note-depth", "balanced"]
    )
    _apply_note_profile_defaults(args)

    assert args.note_depth == "balanced"


def test_doctor_command_writes_json(tmp_path):
    report_path = tmp_path / "doctor.json"

    exit_code = main(["doctor", "--json", str(report_path)])

    assert exit_code == 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["schema_version"] == 1
    assert "checks" in report
    assert "recommended_actions" in report
    assert "readiness" in report
    assert "install_guide" in report
    assert "gui" in report
    assert all("category" in check and "impact" in check for check in report["checks"])
    assert isinstance(report["gui"]["ready_for_local_parse"], bool)
    assert isinstance(report["gui"]["ready_for_exports"], bool)
    assert any(item["id"] == "local_parse" for item in report["readiness"])
    assert any(check["name"] == "Pandoc" and check["required"] is False for check in report["checks"])


def test_doctor_report_renders_install_guide():
    from slidenote.doctor import render_doctor_report, run_doctor

    text = render_doctor_report(run_doctor())

    assert "SlideNote setup guide" in text
    assert "Readiness:" in text
    assert "Install guide:" in text or "Local parsing is ready" in text
    assert "./run_gui.ps1" in text


def test_doctor_reports_pywin32_missing_when_parent_package_is_absent(monkeypatch):
    from slidenote import doctor

    real_find_spec = doctor.importlib.util.find_spec

    def fake_find_spec(module):
        if module == "win32com.client":
            raise ModuleNotFoundError("No module named 'win32com'")
        return real_find_spec(module)

    monkeypatch.setattr(doctor.importlib.util, "find_spec", fake_find_spec)

    report = doctor.run_doctor()

    pywin32_check = next(check for check in report["checks"] if check["name"] == "pywin32")
    assert pywin32_check["status"] == "warn"
    assert pywin32_check["required"] is False


def test_build_can_export_markdown_with_toc_without_pandoc(tmp_path, monkeypatch):
    monkeypatch.setattr("slidenote.exporting.shutil.which", lambda name: None)
    source = tmp_path / "lecture.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Transport Layer")
    doc.save(source)
    doc.close()
    out = tmp_path / "out"

    exit_code = main(["build", str(source), "--out", str(out), "--quiet", "--vision", "off", "--export", "markdown-toc"])

    assert exit_code == 0
    toc_markdown = (out / "notes.toc.md").read_text(encoding="utf-8")
    export_report = json.loads((out / "export_report.json").read_text(encoding="utf-8"))
    run_summary = json.loads((out / "run_summary.json").read_text(encoding="utf-8"))
    assert "## 目录" in toc_markdown
    assert export_report["summary"]["succeeded"] == 1
    assert run_summary["artifacts"]["notes_toc"] == "notes.toc.md"
    assert run_summary["warnings"]["export"] == []
    assert not (out / "notes.docx").exists()


def test_build_can_generate_local_review_and_exam_pack(tmp_path):
    source = tmp_path / "lecture.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Transport Layer\nTCP provides reliable ordered delivery.")
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
            "--vision",
            "off",
            "--review-mode",
            "local",
            "--exam-mode",
            "local",
            "--exam-question-count",
            "4",
        ]
    )

    assert exit_code == 0
    run_summary = json.loads((out / "run_summary.json").read_text(encoding="utf-8"))
    study_pack = json.loads((out / "study_pack.json").read_text(encoding="utf-8"))
    assert (out / "review.md").exists()
    assert (out / "exam.md").exists()
    assert (out / "exam.json").exists()
    assert (out / "exam.html").exists()
    assert (out / "section_study_pack.json").exists()
    assert (out / "exam_review_pack.json").exists()
    assert (out / "final_exam.md").exists()
    assert (out / "final_exam.answers.md").exists()
    assert (out / "wrong_answer_review_prompt.md").exists()
    assert run_summary["run"]["review_mode"] == "local"
    assert run_summary["run"]["exam_mode"] == "local"
    assert run_summary["artifacts"]["study_pack"] == "study_pack.json"
    assert run_summary["artifacts"]["review_markdown"] == "review.md"
    assert run_summary["artifacts"]["exam_html"] == "exam.html"
    assert run_summary["artifacts"]["section_study_pack"] == "section_study_pack.json"
    assert run_summary["artifacts"]["exam_review_pack"] == "exam_review_pack.json"
    assert run_summary["artifacts"]["final_exam_markdown"] == "final_exam.md"
    assert run_summary["artifacts"]["wrong_answer_review_prompt"] == "wrong_answer_review_prompt.md"
    assert study_pack["summary"]["questions_total"] == 4
    assert study_pack["question_quality"]["overall_score"] >= 0
    quality_report = json.loads((out / "quality_report.json").read_text(encoding="utf-8"))
    assert quality_report["question_quality_score"] is not None


def test_build_returns_nonzero_when_requested_pandoc_export_is_missing(tmp_path, monkeypatch):
    monkeypatch.setattr("slidenote.exporting.shutil.which", lambda name: None)
    source = tmp_path / "lecture.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Transport Layer")
    doc.save(source)
    doc.close()
    out = tmp_path / "out"

    exit_code = main(["build", str(source), "--out", str(out), "--quiet", "--vision", "off", "--export", "docx"])

    assert exit_code == 1
    assert (out / "notes.md").exists()
    export_report = json.loads((out / "export_report.json").read_text(encoding="utf-8"))
    run_summary = json.loads((out / "run_summary.json").read_text(encoding="utf-8"))
    assert export_report["summary"]["blocking_failures"] == 1
    assert export_report["results"][0]["reason"] == "pandoc_not_found"
    assert "Pandoc was not found" in run_summary["warnings"]["export"][0]


def test_deck_brief_auto_runs_before_lecture_weave(tmp_path, monkeypatch):
    source = tmp_path / "lecture.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Replication")
    doc.save(source)
    doc.close()
    out = tmp_path / "out"
    calls = []

    class FakeDeckBriefClient:
        def __init__(self, **kwargs):
            pass

        def generate_with_usage(self, prompt, system_prompt=None):
            calls.append("deck_brief")

            class Result:
                text = json.dumps(
                    {
                        "course_title": "Replication",
                        "one_sentence_summary": "Replica basics.",
                        "page_roles": [{"slide_id": 1, "role": "definition", "reason": "Opening definition"}],
                    }
                )
                usage = {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}

            return Result()

    class FakeNotesClient:
        def __init__(self, **kwargs):
            pass

        def generate_with_usage(self, prompt):
            class Result:
                usage = {"input_tokens": 2, "output_tokens": 3, "total_tokens": 5}

            result = Result()
            if '"task": "page_lecture"' in prompt:
                calls.append("page_note")
                result.text = "Replication keeps copies available. <!-- slidenote-source: p1:s1_t1 -->"
            else:
                calls.append("weave")
                result.text = "Replication keeps copies available. <!-- slidenote-source: p1:s1_t1 -->"
            return result

    monkeypatch.setattr("slidenote.deck_brief.LLMClient", FakeDeckBriefClient)
    monkeypatch.setattr("slidenote.notes.orchestrator.LLMClient", FakeNotesClient)

    exit_code = main(
        [
            "build",
            str(source),
            "--out",
            str(out),
            "--quiet",
            "--vision",
            "off",
            "--figure-crop",
            "off",
            "--use-llm",
            "--provider",
            "openai",
            "--api-key",
            "test",
            "--note-context",
            "document",
        ]
    )

    assert exit_code == 0
    assert calls == ["deck_brief", "page_note", "weave"]
    assert (out / "deck_brief.json").exists()
    assert (out / "deck_brief.md").exists()
    run_summary = json.loads((out / "run_summary.json").read_text(encoding="utf-8"))
    llm_usage = json.loads((out / "llm_usage.json").read_text(encoding="utf-8"))
    page_notes = json.loads((out / "page_notes.json").read_text(encoding="utf-8"))
    weave_report = json.loads((out / "weave_report.json").read_text(encoding="utf-8"))
    assert run_summary["deck_brief"]["llm_call"] is True
    assert run_summary["artifacts"]["deck_brief"] == "deck_brief.json"
    assert llm_usage["request"]["deck_brief_used"] is True
    assert page_notes["request"]["deck_brief_used"] is True
    assert weave_report["request"]["deck_brief_used"] is True
