from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from gui.studio_core import StudioConfig, build_env, build_slidenote_command, command_for_display, performance_tips, safe_run_name


def test_command_builds_gui_options_and_redacts_keys(tmp_path: Path):
    cfg = StudioConfig(
        input_path=tmp_path / "a.pdf",
        output_dir=tmp_path / "out",
        progress_json=tmp_path / "out" / "progress.json",
        use_llm=True,
        provider="openai",
        model="gpt-test",
        api_key="sk-secret",
        vision="auto",
        vision_provider="qwen",
        vision_api_key="qwen-secret",
        ocr="auto",
        ocr_api_key="ocr-secret",
        ocr_secret_key="ocr-secret-2",
        concurrency=6,
        llm_concurrency=5,
        vision_concurrency=4,
        ocr_concurrency=3,
        figure_concurrency=2,
        global_cache_dir=tmp_path / "cache",
        note_strategy="direct",
        review_mode="llm",
        exam_mode="llm",
        exam_question_count=18,
    )
    cmd = build_slidenote_command(cfg)
    assert cmd[:3] == [sys.executable, "-m", "slidenote"]
    assert "--use-llm" in cmd
    assert cmd[cmd.index("--provider") + 1] == "openai"
    assert cmd[cmd.index("--concurrency") + 1] == "6"
    assert cmd[cmd.index("--llm-concurrency") + 1] == "5"
    assert cmd[cmd.index("--vision-concurrency") + 1] == "4"
    assert cmd[cmd.index("--ocr-concurrency") + 1] == "3"
    assert cmd[cmd.index("--figure-concurrency") + 1] == "2"
    assert cmd[cmd.index("--global-cache-dir") + 1] == str(tmp_path / "cache")
    assert cmd[cmd.index("--review-mode") + 1] == "llm"
    assert cmd[cmd.index("--exam-mode") + 1] == "llm"
    assert cmd[cmd.index("--exam-question-count") + 1] == "18"
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


def test_study_pack_llm_mode_uses_text_env_without_llm_notes(tmp_path: Path):
    cfg = StudioConfig(
        input_path=tmp_path / "a.pdf",
        output_dir=tmp_path / "out",
        progress_json=tmp_path / "out" / "progress.json",
        use_llm=False,
        provider="deepseek",
        api_key="deep-key",
        review_mode="llm",
        exam_mode="off",
    )

    cmd = build_slidenote_command(cfg)
    env = build_env({}, cfg)

    assert "--use-llm" not in cmd
    assert cmd[cmd.index("--provider") + 1] == "deepseek"
    assert env["DEEPSEEK_API_KEY"] == "deep-key"


def test_vision_key_env_is_used_for_figure_vision_mode(tmp_path: Path):
    cfg = StudioConfig(
        input_path=tmp_path / "a.pdf",
        output_dir=tmp_path / "out",
        progress_json=tmp_path / "out" / "progress.json",
        vision="off",
        vision_provider="qwen",
        vision_api_key="qwen-secret",
        figure_crop="vision",
    )

    cmd = build_slidenote_command(cfg)
    env = build_env({}, cfg)

    assert "--vision-api-key" not in cmd
    assert cmd[cmd.index("--vision-provider") + 1] == "qwen"
    assert env["QWEN_API_KEY"] == "qwen-secret"


def test_env_and_speed_tips(tmp_path: Path):
    cfg = StudioConfig(
        input_path=tmp_path / "a.pdf",
        output_dir=tmp_path / "out",
        progress_json=tmp_path / "out" / "progress.json",
        use_llm=True,
        provider="deepseek",
        api_key="deep-key",
        vision="all",
        ocr="all",
        cache="off",
        concurrency=1,
        note_strategy="lecture-weave",
    )
    env = build_env({}, cfg)
    assert env["DEEPSEEK_API_KEY"] == "deep-key"
    tips = " ".join(performance_tips(cfg))
    assert "Increase concurrency" in tips
    assert "Vision=all" in tips
    assert "OCR=all" in tips
    assert "lecture-weave" in tips
    assert safe_run_name("我的 课件!!.pdf")


def test_gui_api_status_accepts_provider_alias_env(monkeypatch):
    pytest.importorskip("streamlit")
    from gui.app import _api_status

    monkeypatch.delenv("QWEN_API_KEY", raising=False)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "dashscope-key")

    assert _api_status(True, "", "qwen") == ("Ready", "DASHSCOPE_API_KEY", "good")
