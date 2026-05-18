from argparse import Namespace

from slidenote.models import Deck
from slidenote.pipeline import ArtifactRegistry, BuildContext, FunctionStage, StageResult, run_stage


def test_function_stage_records_result_and_registered_artifact(tmp_path):
    deck = Deck(source_path="demo.pdf", source_type="pdf", pages=[])
    registry = ArtifactRegistry(tmp_path)
    context = BuildContext(
        args=Namespace(),
        input_path=tmp_path / "demo.pdf",
        output_root=tmp_path,
        progress=None,
        artifacts=registry,
    )

    def runner(stage_deck, stage_context):
        stage_context.artifacts.write_json("demo", "demo.json", {"source_type": stage_deck.source_type})
        return StageResult(name="demo_stage", report={"ok": True}, artifacts={"demo": "demo.json"})

    result = run_stage(deck, context, FunctionStage(name="demo_stage", runner=runner, artifacts=["demo"]))

    assert result.report == {"ok": True}
    assert context.reports["demo_stage"] is result
    assert registry.as_summary()["demo"] == "demo.json"
    assert (tmp_path / "demo.json").exists()


def test_stage_dependencies_are_checked(tmp_path):
    deck = Deck(source_path="demo.pdf", source_type="pdf", pages=[])
    context = BuildContext(
        args=Namespace(),
        input_path=tmp_path / "demo.pdf",
        output_root=tmp_path,
        progress=None,
    )
    stage = FunctionStage(name="needs_parse", dependencies=["parse"], runner=lambda *_: {})

    try:
        run_stage(deck, context, stage)
    except RuntimeError as exc:
        assert "depends on missing stage" in str(exc)
    else:
        raise AssertionError("missing dependency should fail")
