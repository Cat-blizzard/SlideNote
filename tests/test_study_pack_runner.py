from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest
import fitz

from slidenote import study_pack_runner
from slidenote.build.errors import UserFacingConfigError
from slidenote.cli import main
from slidenote.llm import PROVIDERS
from slidenote.models import Deck, SlidePage, TextBlock


def _clear_provider_env(monkeypatch) -> None:
    for spec in PROVIDERS.values():
        for name in (*spec.api_key_envs, *spec.model_envs):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("SLIDENOTE_MODEL", raising=False)


def _args(out_dir: Path) -> argparse.Namespace:
    return argparse.Namespace(build_out_dir=out_dir, question_count=4, quiet=True)


def _write_build(out_dir: Path, run_summary: dict | None) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    deck = Deck(
        source_path="lecture.pdf",
        source_type="pdf",
        pages=[SlidePage(slide_id=1, title="TCP", text_blocks=[TextBlock(id="s1_t1", type="paragraph", content="定义：TCP 提供可靠有序的字节流。")])],
    )
    (out_dir / "content.json").write_text(json.dumps(deck.to_dict()), encoding="utf-8")
    (out_dir / "notes.md").write_text("# TCP\n\n## 可靠传输\n\nTCP 提供可靠有序的字节流。\n", encoding="utf-8")
    if run_summary is not None:
        (out_dir / "run_summary.json").write_text(json.dumps(run_summary), encoding="utf-8")


def test_provider_can_run_requires_key_and_known_provider(monkeypatch):
    _clear_provider_env(monkeypatch)
    assert study_pack_runner._provider_can_run("not-a-provider") is False
    assert study_pack_runner._provider_can_run("deepseek") is False
    monkeypatch.setenv("DEEPSEEK_API_KEY", "key")
    assert study_pack_runner._provider_can_run("deepseek") is True


def test_provider_can_run_accepts_explicit_model_for_providers_without_default(monkeypatch):
    _clear_provider_env(monkeypatch)
    no_default = next((name for name, spec in PROVIDERS.items() if not spec.default_model), None)
    if no_default is None:
        pytest.skip("every provider has a default model")
    monkeypatch.setenv(PROVIDERS[no_default].api_key_envs[0], "key")
    assert study_pack_runner._provider_can_run(no_default) is False
    assert study_pack_runner._provider_can_run(no_default, model="custom-model") is True


def test_study_pack_requires_existing_build_dir(tmp_path):
    with pytest.raises(UserFacingConfigError, match="does not exist"):
        study_pack_runner.run_study_pack(_args(tmp_path / "missing"))


def test_study_pack_requires_content_and_notes(tmp_path):
    (tmp_path / "notes.md").write_text("# Notes\n", encoding="utf-8")
    with pytest.raises(UserFacingConfigError, match="content.json and notes.md"):
        study_pack_runner.run_study_pack(_args(tmp_path))


def test_study_pack_warns_when_run_summary_is_missing(tmp_path, monkeypatch, capsys):
    _clear_provider_env(monkeypatch)
    _write_build(tmp_path, run_summary=None)

    assert study_pack_runner.run_study_pack(_args(tmp_path)) == 0

    report = json.loads((tmp_path / "study_pack.json").read_text(encoding="utf-8"))
    assert any(warning.startswith("study_pack_provider_fallback:deepseek") for warning in report["warnings"])
    assert "study_pack_provider_fallback" in capsys.readouterr().err
    assert (tmp_path / "exam.md").exists()


def test_study_pack_reuses_model_and_base_url_from_run_summary(tmp_path, monkeypatch):
    _clear_provider_env(monkeypatch)
    _write_build(tmp_path, run_summary={"run": {"provider": "openai", "model": "gpt-custom", "base_url": "https://proxy.example/v1"}})
    captured = {}
    real_build = study_pack_runner.build_study_pack

    def spy(**kwargs):
        captured.update(kwargs)
        return real_build(**kwargs)

    monkeypatch.setattr(study_pack_runner, "build_study_pack", spy)

    assert study_pack_runner.run_study_pack(_args(tmp_path)) == 0

    assert captured["provider"] == "openai"
    assert captured["model"] == "gpt-custom"
    assert captured["base_url"] == "https://proxy.example/v1"
    assert captured["max_output_tokens"] == study_pack_runner.STUDY_PACK_MAX_OUTPUT_TOKENS
    report = json.loads((tmp_path / "study_pack.json").read_text(encoding="utf-8"))
    assert not any("provider_fallback" in warning for warning in report["warnings"])


def test_study_pack_reuses_text_runtime_from_actual_build(tmp_path, monkeypatch):
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("SLIDENOTE_MODEL", "ep-custom")
    monkeypatch.setenv("SLIDENOTE_BASE_URL", "https://gateway.example/v3")
    source = tmp_path / "lecture.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Reliable transport")
    doc.save(source)
    doc.close()
    out = tmp_path / "out"
    assert main([
        "build", str(source), "--out", str(out), "--quiet", "--preset", "local", "--provider", "doubao",
    ]) == 0

    run = json.loads((out / "run_summary.json").read_text(encoding="utf-8"))["run"]
    assert run["provider"] == "doubao"
    assert run["model"] == "ep-custom"
    assert run["base_url"] == "https://gateway.example/v3"

    monkeypatch.delenv("SLIDENOTE_MODEL")
    monkeypatch.delenv("SLIDENOTE_BASE_URL")
    monkeypatch.setenv("DOUBAO_API_KEY", "dummy")
    captured = {}
    real_build = study_pack_runner.build_study_pack

    def spy(**kwargs):
        captured.update(kwargs)
        return real_build(**{**kwargs, "use_llm": False})

    monkeypatch.setattr(study_pack_runner, "build_study_pack", spy)
    assert study_pack_runner.run_study_pack(_args(out)) == 0
    assert captured["use_llm"] is True
    assert captured["model"] == "ep-custom"
    assert captured["base_url"] == "https://gateway.example/v3"
