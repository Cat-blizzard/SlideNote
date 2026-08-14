import json
from pathlib import Path

import fitz
import pytest

from slidenote.agent_backend import AgentBackendError, parse_dsh_output
from slidenote.cli import main


def _write_pdf(path: Path) -> None:
    doc = fitz.open()
    page = doc.new_page(width=360, height=240)
    page.insert_text((40, 60), "Consensus")
    page.insert_text((40, 95), "Quorum reads and writes must overlap.")
    doc.save(path)
    doc.close()


def _add_test_figure_to_pack(pack_dir: Path) -> dict:
    manifest_path = pack_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    raw_path = "images/quorum.png"
    asset_path = "assets/images/quorum.png"
    figure_id = "s1_img1"
    asset_file = pack_dir / asset_path
    asset_file.parent.mkdir(parents=True, exist_ok=True)
    asset_file.write_bytes(b"fake-png")
    manifest["assets"].append(
        {
            "id": figure_id,
            "slide_id": 1,
            "kind": "images",
            "path": asset_path,
            "original_path": raw_path,
            "caption": "Quorum overlap diagram",
            "role": "content",
            "source_element_ids": [],
            "anchor_element_ids": ["s1_t1"],
            "importance_score": 0.95,
            "importance_rank": 1,
            "visual_summary": "A diagram showing overlapping read and write quorums.",
            "ocr_text": "read quorum / write quorum",
        }
    )
    manifest["deck"]["pages"][0].setdefault("images", []).append(
        {
            "id": figure_id,
            "path": raw_path,
            "caption": "Quorum overlap diagram",
            "visual_summary": "A diagram showing overlapping read and write quorums.",
            "ocr_text": "read quorum / write quorum",
            "role": "content",
            "ignored": False,
            "source_element_ids": [],
            "anchor_element_ids": ["s1_t1"],
            "anchor_reason": "The figure illustrates quorum overlap.",
            "grounding_confidence": 0.86,
            "importance_score": 0.95,
            "importance_rank": 1,
            "figure_explanation_status": "missing",
            "figure_audit_status": "needs_review",
        }
    )
    if figure_id not in manifest["sections"][0]["source_ids"]:
        manifest["sections"][0]["source_ids"].append(figure_id)
    section_path = pack_dir / manifest["sections"][0]["file"]
    section_text = section_path.read_text(encoding="utf-8")
    section_text += (
        "\n#### Images And Figures\n\n"
        f"- `{figure_id}`: `{asset_path}`\n"
        "  - caption: Quorum overlap diagram\n"
        "  - source_ids: `s1_img1`\n"
        "  - anchor_element_ids: `s1_t1`\n"
        "  - visual_summary: A diagram showing overlapping read and write quorums.\n"
        "  - ocr_text: read quorum / write quorum\n"
        "  - figure_explanation_status: missing\n"
        "  - figure_audit_status: needs_review\n"
        "  - markdown_reference: ![Quorum overlap diagram](assets/images/quorum.png)\n"
    )
    section_path.write_text(section_text, encoding="utf-8")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def test_agent_pack_writes_manifest_sections_and_assets(tmp_path):
    source = tmp_path / "lecture.pdf"
    out = tmp_path / "out"
    _write_pdf(source)

    exit_code = main(["agent-pack", str(source), "--out", str(out), "--quiet"])

    assert exit_code == 0
    pack = out / "agent_pack"
    manifest = json.loads((pack / "manifest.json").read_text(encoding="utf-8"))
    section_text = (pack / "sections" / "section_001.md").read_text(encoding="utf-8")
    assert manifest["schema_version"] == 1
    assert manifest["sections"][0]["file"] == "sections/section_001.md"
    assert manifest["assets"]
    assert (pack / manifest["assets"][0]["path"]).exists()
    assert "s1_t" in section_text
    assert "assets/" in section_text

def test_agent_eval_writes_baseline_agent_comparison(tmp_path, monkeypatch):
    source = tmp_path / "lecture.pdf"
    eval_out = tmp_path / "eval"
    _write_pdf(source)
    build_out = eval_out / "agent_build"
    assert main(["agent-pack", str(source), "--out", str(build_out), "--quiet"]) == 0
    manifest = json.loads((build_out / "agent_pack" / "manifest.json").read_text(encoding="utf-8"))
    source_ids = manifest["sections"][0]["source_ids"]
    calls: list[str] = []
    _install_fake_dsh_client(monkeypatch, calls, source_ids)

    exit_code = main(["agent-eval", str(source), "--out", str(eval_out), "--quiet"])

    report = json.loads((eval_out / "eval_report.json").read_text(encoding="utf-8"))
    markdown = (eval_out / "eval_report.md").read_text(encoding="utf-8")
    assert exit_code == 0
    assert report["status"] == "ok"
    assert report["baseline"]["status"] == "ok"
    assert report["agent"]["status"] == "ok"
    assert report["comparison"]["coverage_ratio"]["agent"] is not None
    assert report["agent"]["agent_run"]["backend"] == "dsh"
    assert report["agent"]["agent_run"]["estimated_backend_calls"] >= 1
    assert (eval_out / "baseline_build" / "coverage.json").exists()
    assert (eval_out / "agent_build" / "agent_run.json").exists()
    assert "# SlideNote Agent Eval" in markdown


# ---------------------------------------------------------------------------
# DeepSeek backend (--backend dsh)
# ---------------------------------------------------------------------------


def _dsh_result_markdown(source_ids: list[str], *, repaired: bool = False) -> str:
    marker_ids = source_ids if repaired else [source_ids[0]]
    if repaired:
        body = "Repaired coverage for the quorum protocol."
    else:
        body = "Quorum reads and writes overlap to preserve consistency."
    return f"## Consensus\n\n{body} " f"<!-- slidenote-source: p1:{','.join(marker_ids)} -->"


class _FakeDSHClient:
    """Fake for slidenote.agent_backend.LLMClient that records prompts."""

    def __init__(self, **kwargs):
        self.model = kwargs.get("model") or "fake-dsh-model"

    def generate_with_usage(self, prompt, system_prompt=None):
        raise NotImplementedError


def _install_fake_dsh_client(monkeypatch, calls: list[str], source_ids: list[str]):
    def make_client(**kwargs):
        client = _FakeDSHClient(**kwargs)

        def generate_with_usage(prompt, system_prompt=None):
            calls.append(prompt)
            repaired = "Repair one SlideNote section" in prompt

            class Result:
                text = json.dumps(
                    {
                        "markdown": _dsh_result_markdown(source_ids, repaired=repaired),
                        "used_asset_paths": [],
                        "covered_source_ids": source_ids if repaired else [source_ids[0]],
                        "warnings": [],
                    },
                    ensure_ascii=False,
                )
                usage = {"input_tokens": 10, "output_tokens": 8, "total_tokens": 18}

            return Result()

        client.generate_with_usage = generate_with_usage
        return client

    monkeypatch.setattr("slidenote.agent_backend.LLMClient", make_client)


def test_agent_run_with_mock_dsh_writes_notes_coverage_and_sources(tmp_path, monkeypatch):
    source = tmp_path / "lecture.pdf"
    build_out = tmp_path / "build"
    run_out = tmp_path / "run"
    _write_pdf(source)
    assert main(["agent-pack", str(source), "--out", str(build_out), "--quiet"]) == 0
    manifest = json.loads((build_out / "agent_pack" / "manifest.json").read_text(encoding="utf-8"))
    asset_path = manifest["assets"][0]["path"]
    source_ids = manifest["sections"][0]["source_ids"]
    calls: list[str] = []
    _install_fake_dsh_client(monkeypatch, calls, source_ids)

    exit_code = main(
        ["agent-run", str(build_out / "agent_pack"), "--out", str(run_out), "--quiet", "--backend", "dsh"]
    )

    assert exit_code == 0
    notes = (run_out / "notes.md").read_text(encoding="utf-8")
    coverage = json.loads((run_out / "coverage.json").read_text(encoding="utf-8"))
    report = json.loads((run_out / "agent_run.json").read_text(encoding="utf-8"))
    assert "<!-- slidenote-source: p1:" in notes
    assert coverage["missing"] == 0
    assert report["backend"] == "dsh"
    assert report["sections"][0]["dsh"]["source"] == "deepseek"
    assert report["sections"][0]["dsh"]["usage"]["total_tokens"] == 18
    assert (run_out / asset_path).exists()


def test_agent_run_dsh_repairs_missing_trace_coverage(tmp_path, monkeypatch):
    source = tmp_path / "lecture.pdf"
    build_out = tmp_path / "build"
    run_out = tmp_path / "run"
    _write_pdf(source)
    assert main(["agent-pack", str(source), "--out", str(build_out), "--quiet"]) == 0
    manifest = json.loads((build_out / "agent_pack" / "manifest.json").read_text(encoding="utf-8"))
    source_ids = manifest["sections"][0]["source_ids"]
    calls: list[str] = []
    _install_fake_dsh_client(monkeypatch, calls, source_ids)

    exit_code = main(
        ["agent-run", str(build_out / "agent_pack"), "--out", str(run_out), "--quiet", "--backend", "dsh"]
    )

    coverage = json.loads((run_out / "coverage.json").read_text(encoding="utf-8"))
    report = json.loads((run_out / "agent_run.json").read_text(encoding="utf-8"))
    assert exit_code == 0
    assert len(calls) == 2
    assert "Repair one SlideNote section" in calls[1]
    assert coverage["missing"] == 0
    assert report["repair"]["attempted_sections"] == 1
    assert report["repair"]["failed_repairs"] == 0


def test_agent_run_dsh_uses_local_cache(tmp_path, monkeypatch):
    source = tmp_path / "lecture.pdf"
    build_out = tmp_path / "build"
    run_out = tmp_path / "run"
    cache_dir = tmp_path / "cache"
    _write_pdf(source)
    assert main(["agent-pack", str(source), "--out", str(build_out), "--quiet"]) == 0
    manifest = json.loads((build_out / "agent_pack" / "manifest.json").read_text(encoding="utf-8"))
    source_ids = manifest["sections"][0]["source_ids"]
    calls: list[str] = []
    _install_fake_dsh_client(monkeypatch, calls, source_ids)

    run_args = [
        "agent-run",
        str(build_out / "agent_pack"),
        "--out",
        str(run_out),
        "--quiet",
        "--backend",
        "dsh",
        "--dsh-cache",
        "on",
        "--dsh-cache-dir",
        str(cache_dir),
    ]
    assert main(run_args) == 0
    calls_after_first = len(calls)
    assert calls_after_first >= 1
    assert main(run_args) == 0
    assert len(calls) == calls_after_first, "second run should hit the local cache without LLM calls"


def test_agent_run_dsh_rejects_invalid_json_output(tmp_path, monkeypatch):
    source = tmp_path / "lecture.pdf"
    build_out = tmp_path / "build"
    run_out = tmp_path / "run"
    _write_pdf(source)
    assert main(["agent-pack", str(source), "--out", str(build_out), "--quiet"]) == 0

    def make_client(**kwargs):
        client = _FakeDSHClient(**kwargs)

        def generate_with_usage(prompt, system_prompt=None):
            class Result:
                text = "not json at all"
                usage = {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}

            return Result()

        client.generate_with_usage = generate_with_usage
        return client

    monkeypatch.setattr("slidenote.agent_backend.LLMClient", make_client)

    exit_code = main(
        ["agent-run", str(build_out / "agent_pack"), "--out", str(run_out), "--quiet", "--backend", "dsh"]
    )

    assert exit_code == 1
    diagnostics = json.loads((run_out / "agent_diagnostics.json").read_text(encoding="utf-8"))
    assert "JSON object" in diagnostics["message"]


def test_parse_dsh_output_accepts_direct_json():
    payload, metadata = parse_dsh_output(
        json.dumps(
            {
                "markdown": "## Notes <!-- slidenote-source: p1:s1_t1 -->",
                "used_asset_paths": [],
                "covered_source_ids": ["s1_t1"],
                "warnings": [],
            }
        )
    )

    assert payload["covered_source_ids"] == ["s1_t1"]
    assert metadata["source"] == "deepseek"


def test_parse_dsh_output_rejects_missing_required_fields():
    with pytest.raises(AgentBackendError, match="missing required field"):
        parse_dsh_output(json.dumps({"markdown": "hello"}))
