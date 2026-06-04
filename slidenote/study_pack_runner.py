from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from slidenote.build.errors import UserFacingConfigError
from slidenote.llm import get_provider_spec
from slidenote.models import deck_from_dict
from slidenote.study_pack import (
    build_study_pack,
    render_exam_html,
    render_exam_markdown,
    render_final_exam_answers_markdown,
    render_final_exam_markdown,
    render_review_markdown,
    render_wrong_answer_review_prompt,
)
from slidenote.utils import write_json, write_text


def run_study_pack(args: argparse.Namespace) -> int:
    output_root = args.build_out_dir.resolve()
    if not output_root.exists():
        raise UserFacingConfigError(f"Build output directory does not exist: {output_root}")

    content_path = output_root / "content.json"
    notes_path = output_root / "notes.md"
    if not content_path.exists() or not notes_path.exists():
        raise UserFacingConfigError("study-pack requires an existing build output with content.json and notes.md.")

    deck = deck_from_dict(_read_json(content_path))
    notes_markdown = notes_path.read_text(encoding="utf-8")
    run_summary = _read_optional_json(output_root / "run_summary.json") or {}
    run_config = run_summary.get("run") if isinstance(run_summary.get("run"), dict) else {}
    provider = str(run_config.get("provider") or "deepseek")
    use_llm = _provider_can_run(provider)

    report = build_study_pack(
        deck=deck,
        notes_markdown=notes_markdown,
        output_root=output_root,
        review_mode="auto",
        exam_mode="auto",
        question_count=max(1, int(args.question_count or 12)),
        use_llm=use_llm,
        provider=provider,
        cache_mode="on",
        cache_dir=output_root / ".cache" / "llm",
        max_output_tokens=12000,
        temperature=0.0,
        note_language=str(run_config.get("note_language") or "zh"),
        section_plan=_read_optional_json(output_root / "sections.json"),
        deck_brief=_read_optional_json(output_root / "deck_brief.json"),
        content_guard=_read_optional_json(output_root / "content_guard.json"),
        coverage_report=_read_optional_json(output_root / "coverage.json"),
        source_map=_read_optional_json(output_root / "source_map.json"),
    )
    if report is None:
        raise UserFacingConfigError("study-pack did not produce any output.")

    _write_study_pack_outputs(output_root, report)
    if not args.quiet:
        mode = "llm" if use_llm else "local"
        print(f"SlideNote study pack complete ({mode}): {output_root}")
        print(f"- review: {output_root / 'review.md'}")
        print(f"- exam:   {output_root / 'exam.md'}")
        print(f"- html:   {output_root / 'exam.html'}")
    return 0


def _write_study_pack_outputs(output_root: Path, report: dict[str, Any]) -> None:
    write_json(output_root / "study_pack.json", report)
    if report.get("review"):
        write_text(output_root / "review.md", render_review_markdown(report))
    if report.get("exam"):
        write_json(output_root / "exam.json", report["exam"])
        write_text(output_root / "exam.md", render_exam_markdown(report))
        write_text(output_root / "exam.html", render_exam_html(report))
    if report.get("section_study_pack"):
        write_json(output_root / "section_study_pack.json", report["section_study_pack"])
    if report.get("exam_review_pack"):
        write_json(output_root / "exam_review_pack.json", report["exam_review_pack"])
    if report.get("final_exam"):
        write_text(output_root / "final_exam.md", render_final_exam_markdown(report))
        write_text(output_root / "final_exam.answers.md", render_final_exam_answers_markdown(report))
    if report.get("wrong_answer_review"):
        write_text(output_root / "wrong_answer_review_prompt.md", render_wrong_answer_review_prompt(report))


def _provider_can_run(provider: str) -> bool:
    try:
        spec = get_provider_spec(provider)
    except ValueError:
        return False
    has_key = any(os.getenv(name) for name in spec.api_key_envs)
    has_model = bool(os.getenv("SLIDENOTE_MODEL") or any(os.getenv(name) for name in spec.model_envs) or spec.default_model)
    return has_key and has_model


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_optional_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    data = _read_json(path)
    return data if isinstance(data, dict) else None
