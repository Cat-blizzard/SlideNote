from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from slidenote.llm_cache import utc_now_iso
from slidenote.utils import write_json


@dataclass(slots=True)
class StageRecord:
    name: str
    started_at: str
    finished_at: str | None = None
    elapsed_seconds: float | None = None
    total: int | None = None
    current: int = 0
    cache_hits: int = 0
    api_calls: int = 0
    skipped: int = 0


class ProgressReporter:
    def __init__(self, path: Path, quiet: bool = False) -> None:
        self.path = path
        self.quiet = quiet
        self.started_at = utc_now_iso()
        self._run_started = time.perf_counter()
        self._stage_started = self._run_started
        self.current_stage: StageRecord | None = None
        self.stages: list[StageRecord] = []
        self.status = "running"
        self.message = ""
        self.write()

    def start_stage(self, name: str, total: int | None = None, message: str | None = None) -> None:
        self._stage_started = time.perf_counter()
        self.current_stage = StageRecord(name=name, started_at=utc_now_iso(), total=total)
        self.message = message or name
        self._print(f"[{len(self.stages) + 1}] {self.message}")
        self.write()

    def set_total(self, total: int | None) -> None:
        if self.current_stage is not None:
            self.current_stage.total = total
            self.write()

    def advance(
        self,
        amount: int = 1,
        message: str | None = None,
        cache_hit: bool = False,
        api_call: bool = False,
        skipped: bool = False,
    ) -> None:
        if self.current_stage is None:
            return
        self.current_stage.current += amount
        if cache_hit:
            self.current_stage.cache_hits += 1
        if api_call:
            self.current_stage.api_calls += 1
        if skipped:
            self.current_stage.skipped += 1
        if message:
            self.message = message
        self._print_progress_line()
        self.write()

    def finish_stage(self, message: str | None = None) -> None:
        if self.current_stage is None:
            return
        self.current_stage.finished_at = utc_now_iso()
        self.current_stage.elapsed_seconds = round(time.perf_counter() - self._stage_started, 3)
        self.stages.append(self.current_stage)
        self.message = message or f"{self.current_stage.name} complete"
        self._print(f"  done in {self.current_stage.elapsed_seconds:.1f}s")
        self.current_stage = None
        self.write()

    def complete(self, message: str = "Build complete") -> None:
        self.status = "complete"
        self.message = message
        self.write()

    def fail(self, message: str) -> None:
        self.status = "failed"
        self.message = message
        self.write()

    def write(self) -> None:
        write_json(self.path, self.snapshot())

    def snapshot(self) -> dict[str, Any]:
        current = _stage_to_dict(self.current_stage) if self.current_stage else None
        elapsed = time.perf_counter() - self._run_started
        return {
            "schema_version": 1,
            "status": self.status,
            "message": self.message,
            "started_at": self.started_at,
            "updated_at": utc_now_iso(),
            "elapsed_seconds": round(elapsed, 3),
            "current_stage": current,
            "stages": [_stage_to_dict(stage) for stage in self.stages],
        }

    def _print_progress_line(self) -> None:
        if self.quiet or self.current_stage is None:
            return
        stage = self.current_stage
        if stage.total:
            progress = f"{stage.current}/{stage.total}"
        else:
            progress = str(stage.current)
        parts = [f"  {stage.name}: {progress}"]
        if stage.cache_hits:
            parts.append(f"cache hits {stage.cache_hits}")
        if stage.api_calls:
            parts.append(f"API calls {stage.api_calls}")
        if stage.skipped:
            parts.append(f"skipped {stage.skipped}")
        if self.message and self.message != stage.name:
            parts.append(self.message)
        self._print(", ".join(parts))

    def _print(self, message: str) -> None:
        if not self.quiet:
            print(message, flush=True)


def _stage_to_dict(stage: StageRecord | None) -> dict[str, Any] | None:
    if stage is None:
        return None
    return {
        "name": stage.name,
        "started_at": stage.started_at,
        "finished_at": stage.finished_at,
        "elapsed_seconds": stage.elapsed_seconds,
        "total": stage.total,
        "current": stage.current,
        "cache_hits": stage.cache_hits,
        "api_calls": stage.api_calls,
        "skipped": stage.skipped,
    }
