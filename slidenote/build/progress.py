from __future__ import annotations

from typing import Any

from slidenote.progress import ProgressReporter


def _target_progress(progress: ProgressReporter, name: str):
    def callback(event: dict[str, Any]) -> None:
        if event.get("event") == "start":
            progress.set_total(event.get("total"))
            return
        record = event.get("record") or {}
        slide_id = event.get("slide_id")
        cache_hit = record.get("cache_status") == "local_hit"
        api_call = bool(record.get("api_call") or record.get("llm_call"))
        skipped = record.get("cache_status") == "skipped"
        progress.advance(
            message=f"{name} slide {slide_id}",
            cache_hit=cache_hit,
            api_call=api_call,
            skipped=skipped,
        )

    return callback


def _llm_progress(progress: ProgressReporter):
    def callback(record: dict[str, Any]) -> None:
        label = record.get("context_id") or record.get("slide_id")
        progress.advance(
            message=f"LLM context {label}",
            cache_hit=record.get("cache_status") == "local_hit",
            api_call=bool(record.get("llm_call")),
        )

    return callback


def _stage_metrics(progress: ProgressReporter) -> dict[str, Any]:
    snapshot = progress.snapshot()
    stages = snapshot.get("stages") if isinstance(snapshot, dict) else []
    stage_records = [stage for stage in stages if isinstance(stage, dict)]
    return {
        "elapsed_seconds": snapshot.get("elapsed_seconds") if isinstance(snapshot, dict) else None,
        "stages": stage_records,
        "slowest_stages": _slowest_stage_records(stage_records, limit=3),
    }


def _slowest_stages(progress: ProgressReporter, limit: int = 3) -> list[dict[str, Any]]:
    snapshot = progress.snapshot()
    stages = snapshot.get("stages") if isinstance(snapshot, dict) else []
    return _slowest_stage_records([stage for stage in stages if isinstance(stage, dict)], limit=limit)


def _slowest_stage_records(stages: list[dict[str, Any]], limit: int = 3) -> list[dict[str, Any]]:
    ranked = sorted(stages, key=lambda stage: float(stage.get("elapsed_seconds") or 0.0), reverse=True)
    return [
        {
            "name": stage.get("name"),
            "elapsed_seconds": float(stage.get("elapsed_seconds") or 0.0),
            "api_calls": int(stage.get("api_calls") or 0),
            "cache_hits": int(stage.get("cache_hits") or 0),
            "skipped": int(stage.get("skipped") or 0),
        }
        for stage in ranked[: max(0, limit)]
    ]
