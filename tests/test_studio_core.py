from __future__ import annotations

import ast
import os
import sys
from pathlib import Path

import pytest

from gui.studio_core import (
    StudioConfig,
    TextbookConfig,
    build_env,
    build_slidenote_command,
    build_study_pack_command,
    build_textbook_command,
    command_for_display,
    discover_outputs,
    discover_textbook_outputs,
    performance_tips,
    safe_run_name,
)


def test_command_builds_gui_options_and_redacts_keys(tmp_path: Path):
    cfg = StudioConfig(
        input_path=tmp_path / "a.pdf",
        output_dir=tmp_path / "out",
        progress_json=tmp_path / "out" / "progress.json",
        preset="lecture",
        provider="openai",
        api_key="sk-secret",
        vision="auto",
        vision_provider="qwen",
        vision_api_key="qwen-secret",
        ocr="auto",
        ocr_api_key="ocr-secret",
        ocr_secret_key="ocr-secret-2",
    )
    cmd = build_slidenote_command(cfg)
    assert cmd[:3] == [sys.executable, "-m", "slidenote"]
    assert cmd[cmd.index("--preset") + 1] == "lecture"
    assert cmd[cmd.index("--provider") + 1] == "openai"
    assert cmd[cmd.index("--vision") + 1] == "auto"
    assert "--use-llm" not in cmd
    assert "--concurrency" not in cmd
    assert "--review-mode" not in cmd
    assert "--exam-mode" not in cmd
    assert "--api-key" not in cmd
    assert "--vision-api-key" not in cmd
    assert "--ocr-api-key" not in cmd
    assert "--ocr-secret-key" not in cmd
    display = command_for_display(cmd)
    assert "sk-secret" not in display
    assert "qwen-secret" not in display
    assert "ocr-secret-2" not in display
    env = build_env({}, cfg)
    assert env["OPENAI_API_KEY"] == "sk-secret"
    assert env["QWEN_API_KEY"] == "qwen-secret"
    assert env["BAIDU_OCR_API_KEY"] == "ocr-secret"
    assert env["BAIDU_OCR_SECRET_KEY"] == "ocr-secret-2"


def test_study_pack_command_is_separate_from_build(tmp_path: Path):
    cfg = StudioConfig(
        input_path=tmp_path / "a.pdf",
        output_dir=tmp_path / "out",
        progress_json=tmp_path / "out" / "progress.json",
        provider="deepseek",
        api_key="deep-key",
    )

    build_cmd = build_slidenote_command(cfg)
    study_cmd = build_study_pack_command(cfg.output_dir, question_count=18)
    env = build_env({}, cfg)

    assert "study-pack" not in build_cmd
    assert study_cmd[:4] == [sys.executable, "-m", "slidenote", "study-pack"]
    assert study_cmd[study_cmd.index("--question-count") + 1] == "18"
    assert env["DEEPSEEK_API_KEY"] == "deep-key"


def test_textbook_command_uses_env_for_ocr_keys(tmp_path: Path):
    cfg = TextbookConfig(
        input_path=tmp_path / "book.pdf",
        output_dir=tmp_path / "textbook",
        ocr="auto",
        ocr_api_key="ocr-key",
        ocr_secret_key="ocr-secret",
    )

    cmd = build_textbook_command(cfg)
    env = build_env({}, cfg)
    display = command_for_display(cmd)

    assert cmd[:4] == [sys.executable, "-m", "slidenote", "textbook-index"]
    assert cmd[cmd.index("--ocr") + 1] == "auto"
    assert "--ocr-api-key" not in cmd
    assert "--ocr-secret-key" not in cmd
    assert "ocr-key" not in display
    assert env["BAIDU_OCR_API_KEY"] == "ocr-key"
    assert env["BAIDU_OCR_SECRET_KEY"] == "ocr-secret"


def test_command_display_redacts_secret_flags_defensively():
    display = command_for_display(
        ["slidenote", "build", "lecture.pdf", "--api-key", "text-secret", "--vision-api-key", "vision-secret"]
    )

    assert display == "slidenote build lecture.pdf --api-key *** --vision-api-key ***"


def test_discover_outputs_includes_markdown_zip_and_exports(tmp_path: Path):
    for filename in ("notes.zip", "notes.toc.md", "notes.docx", "notes.pdf", "notes.tex"):
        (tmp_path / filename).write_bytes(b"x")

    outputs = discover_outputs(tmp_path)

    assert outputs["notes_zip"] == tmp_path / "notes.zip"
    assert outputs["notes_toc"] == tmp_path / "notes.toc.md"
    assert outputs["docx"] == tmp_path / "notes.docx"
    assert outputs["pdf"] == tmp_path / "notes.pdf"
    assert outputs["latex"] == tmp_path / "notes.tex"


def test_discover_textbook_outputs(tmp_path: Path):
    for filename in ("textbook_manifest.json", "textbook_chunks.jsonl", "textbook_report.md"):
        (tmp_path / filename).write_text("{}", encoding="utf-8")

    outputs = discover_textbook_outputs(tmp_path)

    assert outputs["manifest"] == tmp_path / "textbook_manifest.json"
    assert outputs["chunks"] == tmp_path / "textbook_chunks.jsonl"
    assert outputs["report"] == tmp_path / "textbook_report.md"


def test_local_preset_does_not_require_api_env(tmp_path: Path):
    cfg = StudioConfig(
        input_path=tmp_path / "a.pdf",
        output_dir=tmp_path / "out",
        progress_json=tmp_path / "out" / "progress.json",
        preset="local",
        provider="deepseek",
        api_key="deep-key",
        vision="auto",
        vision_provider="qwen",
        vision_api_key="qwen-secret",
    )

    cmd = build_slidenote_command(cfg)
    env = build_env({}, cfg)

    assert cmd[cmd.index("--preset") + 1] == "local"
    assert "DEEPSEEK_API_KEY" not in env
    assert "QWEN_API_KEY" not in env


def test_env_and_speed_tips(tmp_path: Path):
    cfg = StudioConfig(
        input_path=tmp_path / "a.pdf",
        output_dir=tmp_path / "out",
        progress_json=tmp_path / "out" / "progress.json",
        preset="lecture",
        provider="deepseek",
        api_key="deep-key",
        vision="off",
    )
    env = build_env({}, cfg)
    assert env["DEEPSEEK_API_KEY"] == "deep-key"
    tips = " ".join(performance_tips(cfg))
    assert "Lecture preset" in tips
    assert "Vision is off" in tips
    assert safe_run_name("我的 课件!!.pdf")


def test_gui_api_status_accepts_provider_alias_env(monkeypatch):
    pytest.importorskip("streamlit")
    from gui.app import _api_status

    monkeypatch.delenv("QWEN_API_KEY", raising=False)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "dashscope-key")

    assert _api_status(True, "", "qwen") == ("Ready", "DASHSCOPE_API_KEY", "good")


def test_gui_first_run_surface_has_two_presets_and_markdown_zip_default():
    pytest.importorskip("streamlit")
    from gui.app import PRESETS, _selected_export_formats

    assert list(PRESETS) == ["Lecture quality", "Local preview"]
    assert PRESETS["Lecture quality"] == {"preset": "lecture", "vision": "auto"}
    assert PRESETS["Local preview"] == {"preset": "local", "vision": "off"}
    assert _selected_export_formats(True, False, False, False, False) == ["markdown-zip"]


def test_gui_workbench_surface_prioritizes_real_upload_flow():
    source = (Path(__file__).resolve().parents[1] / "gui" / "app.py").read_text(encoding="utf-8")

    assert "def _render_hero" not in source
    assert "hero-card" not in source
    assert "hero-device" not in source
    assert "hero-button" not in source
    assert "Course Intelligence" not in source
    assert "94%" not in source
    assert "Run settings" not in source
    assert "Notes workspace" in source
    assert "Textbook library" in source
    assert "Usage & diagnostics" in source
    assert "_render_empty_upload_panel" in source
    assert "_render_workflow_stepper" in source
    assert "Upload your course material" in source
    assert 'key="source_upload"' in source
    assert "st.columns([0.27, 0.46, 0.27]" in source
    assert "_render_review_export_rail" in source
    assert "Coverage and downloads will appear here." in source
    assert "review-summary" in source


def test_gui_app_invokes_main_when_streamlit_executes_the_script():
    source = (Path(__file__).resolve().parents[1] / "gui" / "app.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    final_statement = tree.body[-1]

    assert isinstance(final_statement, ast.If)
    assert isinstance(final_statement.test, ast.Compare)
    assert ast.unparse(final_statement.test) == "__name__ == '__main__'"
    assert any(
        isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "main"
        for node in ast.walk(final_statement)
    )


def test_gui_workbench_file_size_helper():
    pytest.importorskip("streamlit")
    from gui.app import _format_file_size, _has_generated_notes

    assert _format_file_size(512) == "512 B"
    assert _format_file_size(1536) == "1.5 KB"
    assert _format_file_size(None) == "unknown size"
    assert not _has_generated_notes(None)


def test_gui_workflow_only_marks_real_notes_as_generated(tmp_path: Path):
    pytest.importorskip("streamlit")
    from gui.app import _has_generated_notes

    output_dir = tmp_path / "run"
    output_dir.mkdir()
    assert not _has_generated_notes(output_dir)

    (output_dir / "progress.json").write_text('{"status":"complete"}', encoding="utf-8")
    assert not _has_generated_notes(output_dir)

    (output_dir / "notes.md").write_text("# Notes", encoding="utf-8")
    assert _has_generated_notes(output_dir)


def test_gui_uploaded_file_opens_three_part_workspace(tmp_path: Path):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    app_path = Path(__file__).resolve().parents[1] / "gui" / "app.py"
    app = AppTest.from_file(app_path, default_timeout=10).run()
    assert not app.exception
    assert len(app.file_uploader) == 1

    output_dir = tmp_path / "generated"
    output_dir.mkdir()
    (output_dir / "notes.md").write_text("# Generated notes", encoding="utf-8")
    (output_dir / "notes.zip").write_bytes(b"package")
    (output_dir / "coverage.md").write_text("# Coverage", encoding="utf-8")
    (output_dir / "coverage.json").write_text('{"total":4,"covered":4,"missing":0}', encoding="utf-8")
    (output_dir / "run_summary.json").write_text('{"counts":{"pages":2}}', encoding="utf-8")
    app.file_uploader[0].set_value(("lecture.pdf", b"%PDF-1.4\n%%EOF\n", "application/pdf"))
    app.run()
    app.session_state["last_output_dir"] = str(output_dir)
    app.run()

    assert not app.exception
    assert len(app.columns) >= 3
    markdown = [element.value for element in app.markdown]
    assert "### Source" in markdown
    assert "### Notes workspace" in markdown
    assert "### Review & export" in markdown
    assert any("Ready to review" in value for value in markdown)
    assert any("100.0%" in value for value in markdown)


@pytest.mark.parametrize("write_notes", [True, False])
def test_gui_workflow_updates_immediately_after_build(tmp_path: Path, monkeypatch, write_notes: bool):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest
    import gui.app as studio

    output_dir = tmp_path / "generated"
    output_dir.mkdir()
    monkeypatch.setattr(
        studio, "_prepare_run_paths",
        lambda *_args: (tmp_path / "lecture.pdf", output_dir, output_dir / "progress.json"),
    )

    def fake_build(config):
        if write_notes:
            (config.output_dir / "notes.md").write_text("# Generated notes", encoding="utf-8")
            studio.st.success("Test build finished")
        else:
            studio.st.error("Test build failed")

    monkeypatch.setattr(studio, "_run_build", fake_build)
    app = AppTest.from_string("from gui.app import main\nmain()", default_timeout=10).run()
    app.file_uploader[0].set_value(("lecture.pdf", b"%PDF-1.4\n%%EOF\n", "application/pdf")).run()
    next(button for button in app.button if button.label == "Run SlideNote build").click().run()

    assert not app.exception
    stepper = next(element.value for element in app.markdown if "<div class='workflow-steps'>" in element.value)
    assert stepper.count("workflow-step-complete") == (2 if write_notes else 1)
    assert ("Test build finished" in [element.value for element in app.success]) == write_notes
    assert ("Test build failed" in [element.value for element in app.error]) == (not write_notes)
    assert app.session_state["last_output_dir"] == str(output_dir)

    app.file_uploader[0].set_value(("replacement.pdf", b"%PDF-1.4\n%replacement\n%%EOF\n", "application/pdf")).run()
    assert not app.exception
    assert "last_output_dir" not in app.session_state
    markdown = [element.value for element in app.markdown]
    assert "# Generated notes" not in markdown
    assert any("Coverage and downloads will appear here." in value for value in markdown)
    if write_notes:
        assert (output_dir / "notes.md").exists()


def test_gui_review_rail_warns_when_exports_fail(tmp_path: Path):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    (tmp_path / "notes.md").write_text("# Generated notes", encoding="utf-8")
    (tmp_path / "export_report.json").write_text(
        '{"summary":{"failed":1,"blocking_failures":1},"results":[{"format":"docx","status":"failed","reason":"pandoc_not_found","blocking":true}]}',
        encoding="utf-8",
    )
    app = AppTest.from_string(
        "from pathlib import Path\n"
        "from gui.app import _render_review_export_rail, StudioConfig\n"
        f"output_dir = Path({str(tmp_path)!r})\n"
        "config = StudioConfig(input_path=output_dir / 'lecture.pdf', output_dir=output_dir, progress_json=output_dir / 'progress.json')\n"
        "_render_review_export_rail(output_dir, config, ['docx'])",
        default_timeout=10,
    ).run()

    assert not app.exception
    markdown = [element.value for element in app.markdown]
    assert any("review-summary-warn" in value and "exports need attention" in value for value in markdown)
    assert any("Some exports failed" in warning.value for warning in app.warning)
