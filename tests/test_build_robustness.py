from __future__ import annotations

import json
import sys
import urllib.error
from pathlib import Path
from types import SimpleNamespace

import fitz
import pytest

from slidenote.api_retry import is_transient_api_error
from slidenote.build.config import _apply_build_preset_defaults
from slidenote.cli import _build_parser, _explicit_cli_options, main
from slidenote.llm import LLMClient, resolve_provider_runtime
from slidenote.llm_cache import LLMCache, atomic_write_text
from slidenote.models import Deck, SlidePage, TextBlock
from slidenote.notes import generate_notes_result
from slidenote.notes.assets import _repair_markdown_image_links
from slidenote.notes.contexts import NoteContext
from slidenote.notes.repair import _repair_required_markdown_once


def _pdf(tmp_path: Path) -> Path:
    source = tmp_path / "lecture.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Transport Layer")
    doc.save(source)
    doc.close()
    return source


def _preset_args(argv: list[str]):
    args = _build_parser().parse_args(argv)
    args._explicit_options = _explicit_cli_options(argv)
    _apply_build_preset_defaults(args)
    return args


# -- bug 1: stale artifacts -------------------------------------------------


def test_build_removes_stale_generated_artifacts_but_keeps_cache_and_overrides(tmp_path):
    source = _pdf(tmp_path)
    out = tmp_path / "out"
    (out / "notes.assets" / "images").mkdir(parents=True)
    (out / "notes.assets" / "images" / "old.png").write_bytes(b"old")
    (out / ".cache" / "llm").mkdir(parents=True)
    (out / ".cache" / "llm" / "entry.json").write_text("{}", encoding="utf-8")
    (out / "run_summary.json").write_text(
        json.dumps({"schema_version": 1, "source_path": str(source), "source_type": "pdf", "artifacts": {"content": "content.json"}}),
        encoding="utf-8",
    )
    (out / "vision_usage.json").write_text('{"summary": {"api_calls": 9}}', encoding="utf-8")
    (out / "cost_report.json").write_text("{}", encoding="utf-8")
    (out / "page_modalities.overrides.json").write_text('{"schema_version": 1, "pages": {}}', encoding="utf-8")
    (out / "my_notes.txt").write_text("user file", encoding="utf-8")

    assert main(["build", str(source), "--out", str(out), "--quiet", "--preset", "local"]) == 0

    assert not (out / "vision_usage.json").exists()
    assert not (out / "cost_report.json").exists()
    assert not (out / "notes.assets" / "images" / "old.png").exists()
    assert (out / ".cache" / "llm" / "entry.json").exists()
    assert (out / "page_modalities.overrides.json").exists()
    assert (out / "my_notes.txt").read_text(encoding="utf-8") == "user file"
    assert (out / "notes.md").exists()


def test_stale_artifact_cleanup_never_removes_the_input_file(tmp_path):
    out = tmp_path / "out"
    (out / "images").mkdir(parents=True)
    source = _pdf(out / "images")

    assert main(["build", str(source), "--out", str(out), "--quiet", "--preset", "local"]) == 0
    assert source.exists()


# -- bug 2: retry classification ---------------------------------------------


@pytest.mark.parametrize(("code", "transient"), [(400, False), (401, False), (403, False), (404, False), (429, True), (503, True)])
def test_http_error_status_decides_retry(code, transient):
    error = urllib.error.HTTPError("https://api.test", code, "msg", {}, None)
    assert is_transient_api_error(error) is transient
    wrapped = RuntimeError(f"LLM request failed with HTTP {code}: body")
    wrapped.__cause__ = error
    assert is_transient_api_error(wrapped) is transient


def test_wrapped_connection_error_is_transient():
    wrapped = RuntimeError("LLM request failed: <urlopen error refused>")
    wrapped.__cause__ = urllib.error.URLError("refused")
    assert is_transient_api_error(wrapped) is True


@pytest.mark.parametrize(
    "message",
    ["prompt has 500 tokens over the limit", "invalid model gpt-4o-2024-05-13 (id 503)", "max_tokens must be <= 429"],
)
def test_bare_numbers_in_messages_are_not_status_codes(message):
    assert is_transient_api_error(ValueError(message)) is False


def test_status_in_message_requires_status_context():
    assert is_transient_api_error(RuntimeError("HTTP 502 bad gateway")) is True
    assert is_transient_api_error(RuntimeError("status code: 401 unauthorized")) is False


# -- bug 3: one SDK client, SDK retries disabled -------------------------------


def test_openai_client_is_reused_and_sdk_retries_disabled(monkeypatch):
    constructed: list[dict] = []
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="ok"), finish_reason="stop")],
        usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    )

    def fake_openai(**kwargs):
        constructed.append(kwargs)
        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **_: response)))

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=fake_openai))
    client = LLMClient(provider="openai", model="m", api_key="k")
    client.generate_with_usage("a")
    client.generate_with_usage("b")

    assert len(constructed) == 1
    assert constructed[0]["max_retries"] == 0


# -- bug 4: generic overrides apply to the text role only ----------------------


def test_generic_model_and_base_url_overrides_do_not_leak_into_vision(monkeypatch):
    monkeypatch.setenv("SLIDENOTE_MODEL", "deepseek-text")
    monkeypatch.setenv("SLIDENOTE_BASE_URL", "https://text.example")
    for name in ("QWEN_VISION_MODEL", "DASHSCOPE_VISION_MODEL", "SLIDENOTE_VISION_MODEL", "QWEN_BASE_URL", "DASHSCOPE_BASE_URL"):
        monkeypatch.delenv(name, raising=False)

    vision = resolve_provider_runtime("qwen", for_vision=True)
    text = resolve_provider_runtime("qwen")

    assert vision["model"] == "qwen-vl-plus"
    assert vision["base_url"] == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    assert text["model"] == "deepseek-text"
    assert text["base_url"] == "https://text.example"
    client = LLMClient(provider="openai", model="gpt-4.1-mini", api_key="k", for_vision=True)
    assert client.base_url is None


# -- bug 5 / 6: local preset conflicts and --vision off --------------------------


def test_local_preset_warns_about_conflicting_explicit_flags():
    args = _preset_args(["build", "lecture.pdf", "--preset", "local", "--vision", "auto", "--ocr", "all"])

    assert args.vision == "off"
    assert args.ocr == "off"
    assert any("--vision" in warning for warning in args._config_warnings)
    assert any("--ocr" in warning for warning in args._config_warnings)


def test_local_preset_without_conflict_has_no_warning():
    args = _preset_args(["build", "lecture.pdf", "--preset", "local", "--vision", "off"])
    assert args._config_warnings == []


def test_local_preset_conflict_is_printed_and_recorded(tmp_path, capsys):
    source = _pdf(tmp_path)
    out = tmp_path / "out"

    assert main(["build", str(source), "--out", str(out), "--quiet", "--preset", "local", "--vision", "auto"]) == 0

    assert "--vision" in capsys.readouterr().err
    run_summary = json.loads((out / "run_summary.json").read_text(encoding="utf-8"))
    assert any("--vision" in warning for warning in run_summary["warnings"]["config"])


def test_vision_off_downgrades_vision_dependent_modes():
    from slidenote.build.config import BUILD_PRESET_DEFAULTS

    lecture = BUILD_PRESET_DEFAULTS["lecture"]
    original = dict(lecture)
    try:
        lecture.update(figure_grounding="vision", semantic_layout="vision", figure_crop="vision")
        args = _preset_args(["build", "lecture.pdf", "--vision", "off"])
    finally:
        lecture.clear()
        lecture.update(original)

    assert args.figure_grounding == "auto"
    assert args.semantic_layout == "local"
    assert args.figure_crop == "off"


def test_vision_stage_is_not_planned_when_vision_is_off():
    from slidenote.build.stages import BUILD_PHASES

    vision_step = next(step for phase in BUILD_PHASES for step in phase.steps if step.name == "vision")
    state = SimpleNamespace(args=SimpleNamespace(vision="off", figure_grounding="vision"))
    assert vision_step.enabled(state) is False


# -- bug 7: ./ prefix handling -------------------------------------------------


def test_image_link_repair_does_not_strip_parent_directory(tmp_path):
    asset_map = {"images/x.png": "notes.assets/images/x.png"}
    markdown = "![a](../x.png)\n\n![b](./images/x.png)\n"

    repaired = _repair_markdown_image_links(markdown, tmp_path, asset_map)

    assert "![b](notes.assets/images/x.png)" in repaired
    # `../x.png` is a different file; it may only be rewritten via the by-name
    # fallback, never by treating it as `x.png` through character stripping.
    assert "](../x.png)" in repaired or "](notes.assets/images/x.png)" in repaired
    from slidenote.notes.assets import _asset_link_rewrite_maps

    exact, _ = _asset_link_rewrite_maps({"../shared/x.png": "notes.assets/images/x.png"})
    assert "shared/x.png" not in exact
    assert "../shared/x.png" in exact


# -- bug 8: repair refresh and per-context final repair ------------------------


def _guard(items: list[tuple[str, int]]) -> dict:
    return {
        "required_confidence_threshold": 0.7,
        "summary": {"repair_attempts": 0, "required_missing": 0, "residual_risks": 0},
        "pages": [],
        "items": [
            {"element_id": element_id, "slide_id": slide_id, "learning_role": "definition",
             "must_explain": True, "confidence": 0.95, "reason": "learning content"}
            for element_id, slide_id in items
        ],
        "repairs": [],
    }


TEXTS = {
    "s1_t1": "Alpha replicas acknowledge each committed write before the client proceeds.",
    "s2_t1": "Beta quorums overlap so that every read observes the latest committed write.",
    "s2_t2": "Gamma majority writes prevent two conflicting values from being accepted together.",
}


def _para(element_id: str) -> str:
    slide = element_id[1]
    return f"{TEXTS[element_id]} <!-- slidenote-source: p{slide}:{element_id} -->"


def _two_page_deck() -> Deck:
    return Deck(
        source_path="lecture.pdf",
        source_type="pdf",
        pages=[
            SlidePage(slide_id=1, title="Alpha", text_blocks=[TextBlock(id="s1_t1", type="paragraph", content=TEXTS["s1_t1"])]),
            SlidePage(slide_id=2, title="Beta", text_blocks=[
                TextBlock(id="s2_t1", type="paragraph", content=TEXTS["s2_t1"]),
                TextBlock(id="s2_t2", type="paragraph", content=TEXTS["s2_t2"]),
            ]),
        ],
    )


def test_repair_propagates_force_refresh(monkeypatch, tmp_path):
    deck = _two_page_deck()
    captured = {}

    def fake_generate(**kwargs):
        captured.update(kwargs)
        return _para("s2_t1") + "\n\n" + _para("s2_t2"), {"llm_call": True}

    monkeypatch.setattr("slidenote.notes.repair._generate_cached_llm_text", fake_generate)
    from slidenote.notes import NoteOptions

    _repair_required_markdown_once(
        deck=Deck(source_path="lecture.pdf", source_type="pdf", pages=[deck.pages[1]]),
        context=NoteContext(id="p2", kind="page", title="Beta", pages=[deck.pages[1]]),
        markdown=_para("s2_t1"),
        output_root=tmp_path,
        cache=LLMCache(tmp_path / "cache", mode="off"),
        options=NoteOptions(content_guard=_guard([("s2_t2", 2)]), cache_mode="off"),
        stage="final",
        force_refresh=True,
    )

    assert captured["force_refresh"] is True


def test_repair_skips_input_that_cannot_fit_output_budget(monkeypatch, tmp_path):
    deck = _two_page_deck()
    monkeypatch.setattr(
        "slidenote.notes.repair._generate_cached_llm_text",
        lambda **kwargs: pytest.fail("over-long repair must not be sent"),
    )
    from slidenote.notes import NoteOptions

    original = _para("s2_t1") + "\n\n" + ("filler text " * 400)
    markdown, record = _repair_required_markdown_once(
        deck=Deck(source_path="lecture.pdf", source_type="pdf", pages=[deck.pages[1]]),
        context=NoteContext(id="final", kind="final", title="final", pages=[deck.pages[1]]),
        markdown=original,
        output_root=tmp_path,
        cache=LLMCache(tmp_path / "cache", mode="off"),
        options=NoteOptions(content_guard=_guard([("s2_t2", 2)]), cache_mode="off", max_output_tokens=500),
        stage="final",
    )

    assert markdown == original
    assert record["accepted"] is False
    assert record["rejection_reasons"] == ["input_too_long_for_output_budget"]


def test_final_repair_only_sends_the_context_with_missing_items(monkeypatch, tmp_path):
    repair_prompts: list[str] = []

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def generate_with_usage(self, prompt):
            if '"task": "repair_required_learning_coverage"' in prompt:
                repair_prompts.append(prompt)
                text = _para("s2_t1") + "\n\n" + _para("s2_t2")
            elif "s2_t1" in prompt:
                text = _para("s2_t1")
            else:
                text = _para("s1_t1")
            return SimpleNamespace(text=text, usage={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2})

    monkeypatch.setattr("slidenote.notes.llm_calls.LLMClient", FakeClient)
    guard = _guard([("s1_t1", 1), ("s2_t2", 2)])
    result = generate_notes_result(
        _two_page_deck(), tmp_path, use_llm=True, provider="openai", api_key="test",
        note_strategy="direct", note_context="page", content_guard=guard, cache_mode="off",
    )

    assert len(repair_prompts) == 1
    assert "Alpha replicas" not in repair_prompts[0]
    assert TEXTS["s2_t2"] in result.markdown
    assert TEXTS["s1_t1"] in result.markdown
    assert guard["repairs"][0]["accepted"] is True
    assert guard["repairs"][0]["slide_ids"] == [2]


# -- bug 9: setup errors ---------------------------------------------------------


def test_missing_input_is_a_friendly_error_recorded_in_progress(tmp_path, capsys):
    out = tmp_path / "out"

    assert main(["build", str(tmp_path / "missing.pdf"), "--out", str(out), "--quiet"]) == 2

    assert "Input file not found" in capsys.readouterr().err
    progress = json.loads((out / "progress.json").read_text(encoding="utf-8"))
    assert progress["status"] == "failed"
    assert "Input file not found" in progress["message"]


# -- bug 15: atomic writes and per-context failure fallback -----------------------


def test_atomic_write_replaces_content_without_leaving_temp_files(tmp_path):
    target = tmp_path / "sub" / "progress.json"
    atomic_write_text(target, "first")
    atomic_write_text(target, "second")

    assert target.read_text(encoding="utf-8") == "second"
    assert [path.name for path in target.parent.iterdir()] == ["progress.json"]


def test_atomic_write_retries_windows_permission_error(tmp_path, monkeypatch):
    import os

    calls = {"count": 0}
    real_replace = os.replace

    def flaky_replace(src, dst):
        calls["count"] += 1
        if calls["count"] == 1:
            raise PermissionError("locked by reader")
        return real_replace(src, dst)

    monkeypatch.setattr("slidenote.llm_cache.os.replace", flaky_replace)
    monkeypatch.setattr("slidenote.llm_cache.time.sleep", lambda _seconds: None)
    atomic_write_text(tmp_path / "x.json", "ok")

    assert (tmp_path / "x.json").read_text(encoding="utf-8") == "ok"
    assert calls["count"] == 2


@pytest.mark.parametrize("concurrency", [1, 2])
def test_failed_context_falls_back_to_local_notes(monkeypatch, tmp_path, concurrency):
    class FlakyClient:
        def __init__(self, **kwargs):
            pass

        def generate_with_usage(self, prompt):
            if "s2_t1" in prompt:
                raise RuntimeError("HTTP 500 upstream exploded")
            return SimpleNamespace(text=_para("s1_t1"), usage={"total_tokens": 2})

    monkeypatch.setattr("slidenote.notes.llm_calls.LLMClient", FlakyClient)
    result = generate_notes_result(
        _two_page_deck(), tmp_path, use_llm=True, provider="openai", api_key="test",
        note_strategy="direct", note_context="page", cache_mode="off", concurrency=concurrency,
    )

    assert TEXTS["s1_t1"] in result.markdown
    assert TEXTS["s2_t1"] in result.markdown
    assert result.llm_usage["summary"]["failed_contexts"] == 1
    assert result.generation_warnings and "p2" in result.generation_warnings[0]
    assert "upstream exploded" not in json.dumps(result.llm_usage)


def test_all_contexts_failing_still_raises(monkeypatch, tmp_path):
    class BrokenClient:
        def __init__(self, **kwargs):
            raise RuntimeError("Missing API key for provider `openai`.")

    monkeypatch.setattr("slidenote.notes.llm_calls.LLMClient", BrokenClient)
    with pytest.raises(RuntimeError, match="Missing API key"):
        generate_notes_result(
            _two_page_deck(), tmp_path, use_llm=True, provider="openai", api_key="test",
            note_strategy="direct", note_context="page", cache_mode="off",
        )
