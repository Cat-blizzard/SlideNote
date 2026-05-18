from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

from slidenote.models import Deck
from slidenote.utils import write_json, write_text


@dataclass(slots=True)
class StageResult:
    name: str
    status: str = "ok"
    report: dict[str, Any] | None = None
    artifacts: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class BuildContext:
    args: Any
    input_path: Path
    output_root: Path
    progress: Any
    cache_dirs: dict[str, Path | None] = field(default_factory=dict)
    refresh_slide_ids: set[int] = field(default_factory=set)
    concurrency: int = 1
    artifacts: "ArtifactRegistry" | None = None
    reports: dict[str, StageResult] = field(default_factory=dict)


class Stage(Protocol):
    name: str
    dependencies: list[str]
    artifacts: list[str]

    def run(self, deck: Deck, context: BuildContext) -> StageResult:
        ...


@dataclass(slots=True)
class FunctionStage:
    name: str
    runner: Callable[[Deck, BuildContext], StageResult | dict[str, Any] | None]
    dependencies: list[str] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)

    def run(self, deck: Deck, context: BuildContext) -> StageResult:
        result = self.runner(deck, context)
        if isinstance(result, StageResult):
            return result
        return StageResult(name=self.name, report=result)


class ArtifactRegistry:
    def __init__(self, output_root: Path) -> None:
        self.output_root = output_root
        self._artifacts: dict[str, str] = {}

    def write_json(self, name: str, relative_path: str | Path, data: Any) -> Path:
        path = self.output_root / Path(relative_path)
        write_json(path, data)
        self.register(name, path)
        return path

    def write_text(self, name: str, relative_path: str | Path, content: str) -> Path:
        path = self.output_root / Path(relative_path)
        write_text(path, content)
        self.register(name, path)
        return path

    def register(self, name: str, path: str | Path | None) -> None:
        if path is None:
            return
        value = _display_path(Path(path), self.output_root)
        self._artifacts[name] = value

    def get(self, name: str) -> str | None:
        return self._artifacts.get(name)

    def relative_path(self, name: str) -> str | None:
        return self.get(name)

    def as_summary(self) -> dict[str, str]:
        return dict(sorted(self._artifacts.items()))


def run_stage(deck: Deck, context: BuildContext, stage: Stage) -> StageResult:
    for dependency in stage.dependencies:
        if dependency not in context.reports:
            raise RuntimeError(f"Stage `{stage.name}` depends on missing stage `{dependency}`.")
    result = stage.run(deck, context)
    context.reports[stage.name] = result
    return result


def _display_path(path: Path, output_root: Path) -> str:
    try:
        return path.resolve().relative_to(output_root.resolve()).as_posix()
    except ValueError:
        return str(path)
