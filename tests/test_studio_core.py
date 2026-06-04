from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from gui.studio_core import (
    StudioConfig,
    build_env,
    build_slidenote_command,
    build_study_pack_command,
    command_for_display,
    discover_outputs,
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


def test_discover_outputs_includes_markdown_zip_and_exports(tmp_path: Path):
    for filename in ("notes.zip", "notes.toc.md", "notes.docx", "notes.pdf", "notes.tex"):
        (tmp_path / filename).write_bytes(b"x")

    outputs = discover_outputs(tmp_path)

    assert outputs["notes_zip"] == tmp_path / "notes.zip"
    assert outputs["notes_toc"] == tmp_path / "notes.toc.md"
    assert outputs["docx"] == tmp_path / "notes.docx"
    assert outputs["pdf"] == tmp_path / "notes.pdf"
    assert outputs["latex"] == tmp_path / "notes.tex"


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


def test_gui_workbench_surface_replaces_hero_cards():
    source = (Path(__file__).resolve().parents[1] / "gui" / "app.py").read_text(encoding="utf-8")

    assert "def _render_hero" not in source
    assert "hero-card" not in source
    assert "Run settings" not in source
    assert "Notes workspace" in source
    assert "Usage & diagnostics" in source
    assert "_render_empty_upload_panel" in source


def test_gui_workbench_file_size_helper():
    pytest.importorskip("streamlit")
    from gui.app import _format_file_size

    assert _format_file_size(512) == "512 B"
    assert _format_file_size(1536) == "1.5 KB"
    assert _format_file_size(None) == "unknown size"
