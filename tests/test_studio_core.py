from __future__ import annotations

import os
import sys
from pathlib import Path

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
        global_cache_dir=tmp_path / "cache",
        note_strategy="direct",
    )
    cmd = build_slidenote_command(cfg)
    assert cmd[:3] == [sys.executable, "-m", "slidenote"]
    assert "--use-llm" in cmd
    assert cmd[cmd.index("--provider") + 1] == "openai"
    assert cmd[cmd.index("--concurrency") + 1] == "6"
    assert cmd[cmd.index("--global-cache-dir") + 1] == str(tmp_path / "cache")
    display = command_for_display(cmd)
    assert "sk-secret" not in display
    assert "qwen-secret" not in display
    assert "ocr-secret-2" not in display
    assert "***" in display


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
