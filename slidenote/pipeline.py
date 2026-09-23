from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from slidenote.utils import display_path, write_json, write_text


@dataclass(frozen=True, slots=True)
class BuildStep:
    name: str
    runner: Callable[[Any], None]
    enabled: Callable[[Any], bool] | None = None
    tracks_progress: bool = True


@dataclass(frozen=True, slots=True)
class BuildPhase:
    name: str
    steps: tuple[BuildStep, ...]


def run_build_plan(state: Any, phases: tuple[BuildPhase, ...]) -> None:
    """Run one explicit plan; disabled steps never enter progress accounting."""
    planned = [
        (phase.name, step)
        for phase in phases
        for step in phase.steps
        if step.enabled is None or step.enabled(state)
    ]
    state.progress.set_plan([step.name for _, step in planned if step.tracks_progress])
    for phase in phases:
        phase_steps = [step for phase_name, step in planned if phase_name == phase.name]
        if not phase_steps:
            continue
        state.progress.set_phase(phase.name)
        for step in phase_steps:
            step.runner(state)
    state.progress.set_phase(None)


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
        value = display_path(Path(path), self.output_root)
        self._artifacts[name] = value

    def get(self, name: str) -> str | None:
        return self._artifacts.get(name)

    def relative_path(self, name: str) -> str | None:
        return self.get(name)

    def as_summary(self) -> dict[str, str]:
        return dict(sorted(self._artifacts.items()))
