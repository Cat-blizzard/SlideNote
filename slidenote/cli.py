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
from slidenote.agent_backend import AgentBackendError, run_agent_build, run_agent_pack, run_agent_run
from slidenote.agent_eval import run_agent_eval
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
        if args.command == "agent-pack":
            return _agent_pack(args)
        if args.command == "agent-run":
            return _agent_run(args)
        if args.command == "agent-build":
            return _agent_build(args)
        if args.command == "agent-eval":
            return _agent_eval(args)
        if args.command == "doctor":
            return _doctor(args)
        if args.command == "study-pack":
            return _study_pack(args)
    except (UserFacingConfigError, AgentBackendError) as exc:
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

    agent_pack = subparsers.add_parser("agent-pack", help="Build a Claude Code-friendly agent pack without generating notes.")
    agent_pack.add_argument("input", type=Path, help="Input .pptx, .ppt, or .pdf file.")
    agent_pack.add_argument("--out", type=Path, default=Path("outputs") / "agent_pack", help="Output directory.")
    _add_agent_pack_options(agent_pack)

    agent_run = subparsers.add_parser("agent-run", help="Run an agent pack through a stdout-only agent backend.")
    agent_run.add_argument("agent_pack_dir", type=Path, help="Directory containing manifest.json, sections, assets, style.md, and skill.md.")
    _add_agent_run_options(agent_run)

    agent_build = subparsers.add_parser("agent-build", help="Build an agent pack, run Claude Code, and produce notes plus coverage.")
    agent_build.add_argument("input", type=Path, help="Input .pptx, .ppt, or .pdf file.")
    agent_build.add_argument("--out", type=Path, default=Path("outputs") / "agent_build", help="Output directory.")
    _add_agent_pack_options(agent_build)
    _add_agent_run_options(agent_build, include_out=False, include_quiet=False)

    agent_eval = subparsers.add_parser("agent-eval", help="Compare baseline build output with the Claude agent build.")
    agent_eval.add_argument("input", type=Path, help="Input .pptx, .ppt, or .pdf file.")
    agent_eval.add_argument("--out", type=Path, default=Path("outputs") / "agent_eval", help="Evaluation output directory.")
    _add_agent_pack_options(agent_eval)
    _add_agent_run_options(agent_eval, include_out=False, include_quiet=False)
    return parser


def _add_agent_pack_options(command: argparse.ArgumentParser) -> None:
    command.add_argument(
        "--speed-mode",
        choices=["fast", "balanced", "quality", "debug"],
        default="quality",
        help="Preset for cost/time limits. Defaults to quality; it fills unset limits but does not enable OCR or vision by itself.",
    )
    command.add_argument("--concurrency", type=int, default=1, help="Fallback parallel API calls for OCR, vision, figure crop, and section detection.")
    command.add_argument("--llm-concurrency", type=int, default=None, help="Parallel text LLM calls for optional section detection/content guard.")
    command.add_argument("--vision-concurrency", type=int, default=None, help="Parallel vision extraction calls. Defaults to --concurrency.")
    command.add_argument("--ocr-concurrency", type=int, default=None, help="Parallel OCR calls. Defaults to --concurrency.")
    command.add_argument("--figure-concurrency", type=int, default=None, help="Parallel figure crop/bbox calls. Defaults to --concurrency.")
    command.add_argument("--global-cache-dir", type=Path, default=None, help="Shared cache root. Defaults to per-output .cache folders.")
    command.add_argument("--refresh-pages", default=None, help="Comma-separated slide IDs or ranges to bypass local cache.")
    command.add_argument("--progress-json", type=Path, default=None, help="Progress JSON path. Defaults to <out>/progress.json.")
    command.add_argument("--quiet", action="store_true", help="Suppress live progress output.")
    command.add_argument("--use-llm", action="store_true", help="Allow LLM-assisted section detection/content guard while building the pack.")
    command.add_argument(
        "--provider",
        default="openai",
        help=f"Text LLM provider for optional section/content guard calls. Supported: {', '.join(supported_provider_names())}.",
    )
    command.add_argument("--model", default=None, help="Text LLM model for optional section/content guard calls.")
    command.add_argument("--api-key", default=None, help="Text LLM API key override.")
    command.add_argument("--base-url", default=None, help="Text LLM base URL override.")
    command.add_argument("--max-output-tokens", type=int, default=None, help="Maximum tokens for optional text LLM calls.")
    command.add_argument("--temperature", type=float, default=None, help="Optional text LLM temperature.")
    command.add_argument("--cache", choices=["on", "off", "refresh"], default="on", help="Text LLM cache mode.")
    command.add_argument("--cache-dir", type=Path, default=None, help="Text LLM cache directory.")
    command.add_argument("--content-guard", choices=["auto", "off"], default="auto", help="Include local or LLM-assisted required-content hints.")
    command.add_argument("--section-detection", choices=["auto", "local", "llm"], default="auto", help="How SlideNote detects section boundaries.")
    command.add_argument("--section-cache", choices=["on", "off", "refresh"], default="on", help="Section detection cache mode.")
    command.add_argument("--section-cache-dir", type=Path, default=None, help="Section detection cache directory.")
    command.add_argument("--semantic-layout", choices=["auto", "local", "vision"], default="local", help="How semantic page blocks are built.")
    command.add_argument("--ocr", choices=["off", "auto", "all"], default="off", help="Dedicated OCR mode before pack generation.")
    command.add_argument("--ocr-provider", default="baidu", help="OCR provider. Supported: baidu, mathpix, google.")
    command.add_argument("--ocr-api-key", default=None, help="OCR API key/app id override.")
    command.add_argument("--ocr-secret-key", default=None, help="OCR secret key/app key override.")
    command.add_argument("--ocr-endpoint", default=None, help="OCR endpoint override.")
    command.add_argument("--ocr-language", default="CHN_ENG", help="OCR language hint.")
    command.add_argument("--ocr-cache", choices=["on", "off", "refresh"], default="on", help="OCR local cache mode.")
    command.add_argument("--ocr-cache-dir", type=Path, default=None, help="OCR cache directory.")
    command.add_argument("--ocr-max-targets", type=int, default=None, help="Maximum OCR targets. Use 0 for unlimited.")
    command.add_argument("--ocr-min-text-chars", type=int, default=80, help="auto mode OCRs pages below this extracted text length.")
    command.add_argument("--ocr-min-area", type=int, default=120000, help="Minimum embedded image pixel area for OCR fallback.")
    command.add_argument("--ocr-max-edge", type=int, default=None, help="Resize OCR target long edge before API calls.")
    command.add_argument("--vision", choices=["off", "auto", "all"], default="off", help="Vision extraction mode before pack generation.")
    command.add_argument("--vision-provider", default="qwen", help="Vision provider. Supported image providers include openai, qwen, doubao, gemini, and claude.")
    command.add_argument("--vision-model", default=None, help="Vision model override.")
    command.add_argument("--vision-api-key", default=None, help="Vision API key override.")
    command.add_argument("--vision-base-url", default=None, help="Vision provider base URL override.")
    command.add_argument("--vision-cache", choices=["on", "off", "refresh"], default="on", help="Vision local cache mode.")
    command.add_argument("--vision-cache-dir", type=Path, default=None, help="Vision cache directory.")
    command.add_argument("--vision-max-targets", type=int, default=None, help="Maximum images/screenshots to parse. Use 0 for unlimited.")
    command.add_argument("--vision-min-area", type=int, default=120000, help="Minimum embedded image pixel area for auto fallback selection.")
    command.add_argument("--vision-max-edge", type=int, default=None, help="Resize image long edge before API calls.")
    command.add_argument("--vision-max-output-tokens", type=int, default=None, help="Maximum vision output tokens per image.")
    command.add_argument("--vision-temperature", type=float, default=0.0, help="Vision model temperature.")
    command.add_argument("--vision-detail", choices=["low", "high", "auto"], default=None, help="OpenAI image detail setting.")
    command.add_argument("--figure-crop", choices=["off", "auto", "vision"], default="off", help="Crop meaningful local figures from page screenshots.")
    command.add_argument("--composite-figures", choices=["off", "auto"], default="auto", help="Detect diagrams assembled from many embedded images.")
    command.add_argument("--figure-max-targets", type=int, default=None, help="Maximum pages to send for figure bbox detection.")
    command.add_argument("--figure-max-crops-per-page", type=int, default=3, help="Maximum local figure crops per page.")
    command.add_argument("--figure-min-confidence", type=float, default=0.45, help="Minimum model confidence for accepting a figure crop.")
    command.add_argument("--figure-min-area", type=int, default=40000, help="Minimum crop area in pixels.")
    command.add_argument("--figure-cache", choices=["on", "off", "refresh"], default="on", help="Figure crop local cache mode.")
    command.add_argument("--figure-cache-dir", type=Path, default=None, help="Figure crop cache directory.")
    command.add_argument("--image-ranking", choices=["off", "local"], default="local", help="Rank images by study value before pack generation.")
    command.add_argument("--figure-grounding", choices=["off", "auto", "vision"], default="auto", help="Anchor study-value figures to nearby text/table elements.")
    command.add_argument("--figure-placement", choices=["inline", "page-end"], default="inline", help="Preferred figure placement rule for the agent.")
    command.add_argument("--figure-audit", choices=["off", "local", "llm"], default="local", help="Audit figure placement/explanation quality.")


def _add_agent_run_options(command: argparse.ArgumentParser, include_out: bool = True, include_quiet: bool = True) -> None:
    command.add_argument("--backend", choices=["claude"], default="claude", help="Agent backend. First version supports official Claude Code only.")
    if include_out:
        command.add_argument("--out", type=Path, default=None, help="Output directory. Defaults to the agent pack parent directory.")
    command.add_argument("--repair", choices=["auto", "off"], default="auto", help="Run one coverage repair pass after initial agent output. Defaults to auto.")
    command.add_argument("--repair-rounds", type=int, default=1, help="Maximum repair rounds. First version supports 0 or 1.")
    command.add_argument("--claude-command", default="claude", help="Claude Code executable to run.")
    command.add_argument("--claude-model", default=None, help="Optional Claude Code model argument.")
    command.add_argument("--max-budget-usd", type=float, default=None, help="Optional Claude Code --max-budget-usd value.")
    command.add_argument("--claude-timeout", type=int, default=900, help="Per-section Claude Code timeout in seconds.")
    if include_quiet:
        command.add_argument("--quiet", action="store_true", help="Suppress progress output.")


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


def _agent_pack(args: argparse.Namespace) -> int:
    return run_agent_pack(args)


def _agent_run(args: argparse.Namespace) -> int:
    return run_agent_run(args)


def _agent_build(args: argparse.Namespace) -> int:
    return run_agent_build(args)


def _agent_eval(args: argparse.Namespace) -> int:
    return run_agent_eval(args)




if __name__ == "__main__":
    raise SystemExit(main())
