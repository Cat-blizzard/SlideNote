from __future__ import annotations

import argparse
import sys
from pathlib import Path

from slidenote.build_pipeline import (
    UserFacingConfigError,
    _apply_build_preset_defaults,
    _apply_note_profile_defaults,
    _apply_speed_mode_defaults,
    _parse_slide_ranges,
    _resolve_api_concurrency,
    run_build,
)
from slidenote.doctor import render_doctor_report, run_doctor
from slidenote.extractors import available_parser_choices
from slidenote.llm import supported_provider_names
from slidenote.study_pack_runner import run_study_pack
from slidenote.utils import write_json


def main(argv: list[str] | None = None) -> int:
    raw_argv = list(argv) if argv is not None else sys.argv[1:]
    parser = _build_parser()
    args = parser.parse_args(raw_argv)
    args._explicit_options = _explicit_cli_options(raw_argv)
    try:
        if args.command == "build":
            return _build(args)
        if args.command == "doctor":
            return _doctor(args)
        if args.command == "study-pack":
            return _study_pack(args)
    except UserFacingConfigError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    parser.print_help()
    return 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="slidenote", description="Coverage-aware course note generator.")
    subparsers = parser.add_subparsers(dest="command")

    build = subparsers.add_parser("build", help="Build notes from a PPTX/PPT/PDF file.")
    build.add_argument("input", type=Path, help="Input .pptx, .ppt, .pdf, or an external-parser-supported file.")
    build.add_argument("--out", type=Path, default=Path("outputs") / "slidenote", help="Output directory.")
    build.add_argument(
        "--parser",
        choices=available_parser_choices(),
        default="auto",
        help="Parser adapter. auto uses the built-in PPT/PDF parser first; docling/marker/mineru use optional external CLI adapters.",
    )
    build.add_argument(
        "--preset",
        choices=["lecture", "local"],
        default="lecture",
        help="Product-level build preset. lecture is the quality-first default; local runs without API calls.",
    )
    build.add_argument("--progress-json", type=Path, default=None, help="Progress JSON path. Defaults to <out>/progress.json.")
    build.add_argument("--quiet", action="store_true", help="Suppress live progress output while still writing progress.json.")
    build.add_argument(
        "--provider",
        default="deepseek",
        help=f"Text LLM provider for the lecture preset. Supported: {', '.join(supported_provider_names())}.",
    )
    build.add_argument(
        "--export",
        default=None,
        help="Comma-separated extra export formats: markdown-zip, markdown-toc, docx, pdf, latex, or all.",
    )
    build.add_argument(
        "--vision",
        choices=["off", "auto"],
        default="auto",
        help="Vision extraction mode. auto is the quality default; off skips visual API calls.",
    )

    doctor = subparsers.add_parser("doctor", help="Check local dependencies, optional tools, and API key environment variables.")
    doctor.add_argument("--json", type=Path, default=None, help="Write the doctor report as JSON to this path.")

    study_pack = subparsers.add_parser("study-pack", help="Generate review and exam materials from an existing build output directory.")
    study_pack.add_argument("build_out_dir", type=Path, help="Output directory from a previous slidenote build run.")
    study_pack.add_argument("--question-count", type=int, default=12, help="Target number of self-test questions.")
    study_pack.add_argument("--quiet", action="store_true", help="Suppress output summary.")
    return parser


def _doctor(args: argparse.Namespace) -> int:
    report = run_doctor()
    if args.json:
        write_json(args.json.resolve(), report)
    print(render_doctor_report(report))
    return 0


def _explicit_cli_options(argv: list[str]) -> set[str]:
    explicit: set[str] = set()
    for token in argv:
        if token == "--":
            break
        if not token.startswith("--") or token == "--":
            continue
        option = token[2:].split("=", 1)[0]
        if option:
            explicit.add(option.replace("-", "_"))
    return explicit


def _build(args: argparse.Namespace) -> int:
    return run_build(args)


def _study_pack(args: argparse.Namespace) -> int:
    return run_study_pack(args)


if __name__ == "__main__":
    raise SystemExit(main())
