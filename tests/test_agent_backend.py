import json
import subprocess
from pathlib import Path

import fitz

from slidenote.agent_backend import AgentBackendError, parse_claude_stdout
from slidenote.cli import main


def _write_pdf(path: Path) -> None:
    doc = fitz.open()
    page = doc.new_page(width=360, height=240)
    page.insert_text((40, 60), "Consensus")
    page.insert_text((40, 95), "Quorum reads and writes must overlap.")
    doc.save(path)
    doc.close()


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


def test_parse_claude_stdout_accepts_wrapped_json_result():
    payload, metadata = parse_claude_stdout(
        json.dumps(
            {
                "type": "result",
                "result": json.dumps(
                    {
                        "markdown": "## Notes <!-- slidenote-source: p1:s1_t1 -->",
                        "used_asset_paths": [],
                        "covered_source_ids": ["s1_t1"],
                        "warnings": [],
                    }
                ),
                "session_id": "abc",
            }
        )
    )

    assert payload["covered_source_ids"] == ["s1_t1"]
    assert metadata["session_id"] == "abc"


def test_parse_claude_stdout_rejects_missing_required_fields():
    try:
        parse_claude_stdout(json.dumps({"markdown": "hello"}))
    except AgentBackendError as exc:
        assert "missing required field" in str(exc)
    else:
        raise AssertionError("expected AgentBackendError")


def test_agent_run_with_mock_claude_writes_notes_coverage_and_sources(tmp_path, monkeypatch):
    source = tmp_path / "lecture.pdf"
    build_out = tmp_path / "build"
    run_out = tmp_path / "run"
    _write_pdf(source)
    assert main(["agent-pack", str(source), "--out", str(build_out), "--quiet"]) == 0
    manifest = json.loads((build_out / "agent_pack" / "manifest.json").read_text(encoding="utf-8"))
    asset_path = manifest["assets"][0]["path"]
    source_ids = manifest["sections"][0]["source_ids"]

    def fake_run(*args, **kwargs):
        del args, kwargs
        return subprocess.CompletedProcess(
            ["claude"],
            0,
            stdout=json.dumps(
                {
                    "type": "result",
                    "result": json.dumps(
                        {
                            "markdown": (
                                "## Consensus\n\n"
                                "Quorum reads and writes overlap to preserve consistency."
                                f"<!-- slidenote-source: p1:{','.join(source_ids)} -->\n\n"
                                f"![Page]({asset_path})"
                            ),
                            "used_asset_paths": [asset_path],
                            "covered_source_ids": source_ids,
                            "warnings": [],
                        }
                    ),
                }
            ),
            stderr="",
        )

    monkeypatch.setattr("slidenote.agent_backend.subprocess.run", fake_run)

    exit_code = main(["agent-run", str(build_out / "agent_pack"), "--out", str(run_out), "--quiet"])

    assert exit_code == 0
    notes = (run_out / "notes.md").read_text(encoding="utf-8")
    coverage = json.loads((run_out / "coverage.json").read_text(encoding="utf-8"))
    source_map = json.loads((run_out / "source_map.json").read_text(encoding="utf-8"))
    assert f"![Page]({asset_path})" in notes
    assert "<!-- slidenote-source: p1:" in notes
    assert coverage["missing"] == 0
    assert source_map["note_blocks"]
    assert (run_out / asset_path).exists()


def test_agent_run_invalid_claude_json_writes_diagnostics(tmp_path, monkeypatch):
    source = tmp_path / "lecture.pdf"
    build_out = tmp_path / "build"
    run_out = tmp_path / "run"
    _write_pdf(source)
    assert main(["agent-pack", str(source), "--out", str(build_out), "--quiet"]) == 0

    def fake_run(*args, **kwargs):
        del args, kwargs
        return subprocess.CompletedProcess(["claude"], 0, stdout="not json", stderr="")

    monkeypatch.setattr("slidenote.agent_backend.subprocess.run", fake_run)

    exit_code = main(["agent-run", str(build_out / "agent_pack"), "--out", str(run_out), "--quiet"])

    diagnostics = json.loads((run_out / "agent_diagnostics.json").read_text(encoding="utf-8"))
    assert exit_code == 1
    assert diagnostics["status"] == "error"
    assert "stdout was not JSON" in diagnostics["message"]


def test_agent_run_nonzero_claude_exit_writes_diagnostics(tmp_path, monkeypatch):
    source = tmp_path / "lecture.pdf"
    build_out = tmp_path / "build"
    run_out = tmp_path / "run"
    _write_pdf(source)
    assert main(["agent-pack", str(source), "--out", str(build_out), "--quiet"]) == 0

    def fake_run(*args, **kwargs):
        del args, kwargs
        return subprocess.CompletedProcess(["claude"], 7, stdout="", stderr="auth failed")

    monkeypatch.setattr("slidenote.agent_backend.subprocess.run", fake_run)

    exit_code = main(["agent-run", str(build_out / "agent_pack"), "--out", str(run_out), "--quiet"])

    diagnostics = json.loads((run_out / "agent_diagnostics.json").read_text(encoding="utf-8"))
    assert exit_code == 1
    assert "exited with code 7" in diagnostics["message"]
