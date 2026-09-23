from types import SimpleNamespace

from slidenote.pipeline import ArtifactRegistry, BuildPhase, BuildStep, run_build_plan
from slidenote.progress import ProgressReporter


def test_build_plan_runs_enabled_steps_in_phase_order(tmp_path):
    progress = ProgressReporter(tmp_path / "progress.json", quiet=True)
    state = SimpleNamespace(progress=progress, calls=[])

    def step(name, phase):
        def run(current_state):
            assert current_state.progress.current_phase == phase
            current_state.progress.start_stage(name)
            current_state.calls.append(name)
            current_state.progress.finish_stage()
        return run

    phases = (
        BuildPhase("ingest", (BuildStep("parse", step("parse", "ingest")),)),
        BuildPhase("understand", (
            BuildStep("ocr", step("ocr", "understand"), enabled=lambda _: False),
            BuildStep("layout", step("layout", "understand")),
        )),
        BuildPhase("write", (
            BuildStep("notes", step("notes", "write")),
            BuildStep("summary", lambda current_state: current_state.calls.append("summary"), tracks_progress=False),
        )),
    )

    run_build_plan(state, phases)

    assert state.calls == ["parse", "layout", "notes", "summary"]
    snapshot = progress.snapshot()
    assert snapshot["planned_stages"] == state.calls[:-1]
    assert [stage["name"] for stage in snapshot["stages"]] == state.calls[:-1]
    assert snapshot["current_phase"] is None


def test_artifact_registry_records_written_file(tmp_path):
    registry = ArtifactRegistry(tmp_path)
    registry.write_json("demo", "demo.json", {"ok": True})

    assert registry.as_summary()["demo"] == "demo.json"
    assert (tmp_path / "demo.json").exists()
