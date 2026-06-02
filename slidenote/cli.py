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
from slidenote.llm import supported_provider_names
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
    except UserFacingConfigError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    parser.print_help()
    return 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="slidenote", description="Coverage-aware course note generator.")
    subparsers = parser.add_subparsers(dest="command")

    build = subparsers.add_parser("build", help="Build notes from a PPTX/PPT/PDF file.")
    build.add_argument("input", type=Path, help="Input .pptx, .ppt, or .pdf file.")
    build.add_argument("--out", type=Path, default=Path("outputs") / "slidenote", help="Output directory.")
    build.add_argument(
        "--preset",
        choices=["auto", "fast", "faithful", "lecture"],
        default="auto",
        help="Product-level build preset. fast minimizes extra passes, faithful prioritizes traceable coverage, lecture enables teacher-style notes.",
    )
    build.add_argument(
        "--speed-mode",
        choices=["fast", "balanced", "quality", "debug"],
        default="quality",
        help="Preset for cost/time limits. Defaults to quality; it fills unset limits but does not enable OCR or LLM by itself.",
    )
    build.add_argument("--concurrency", type=int, default=1, help="Fallback parallel API calls for OCR, vision, figure crop, and page-level LLM work.")
    build.add_argument("--llm-concurrency", type=int, default=None, help="Parallel text LLM calls for notes, page notes, weave, and repairs. Defaults to --concurrency.")
    build.add_argument("--vision-concurrency", type=int, default=None, help="Parallel vision extraction calls. Defaults to --concurrency.")
    build.add_argument("--ocr-concurrency", type=int, default=None, help="Parallel OCR calls. Defaults to --concurrency.")
    build.add_argument("--figure-concurrency", type=int, default=None, help="Parallel figure crop/bbox vision calls. Defaults to --concurrency.")
    build.add_argument("--global-cache-dir", type=Path, default=None, help="Shared cache root. Defaults to per-output .cache folders.")
    build.add_argument(
        "--refresh-pages",
        default=None,
        help="Comma-separated slide IDs or ranges to bypass local cache, for example 3,5-8.",
    )
    build.add_argument("--progress-json", type=Path, default=None, help="Progress JSON path. Defaults to <out>/progress.json.")
    build.add_argument("--quiet", action="store_true", help="Suppress live progress output while still writing progress.json.")
    build.add_argument("--use-llm", action="store_true", help="Use an LLM provider for AI rewriting.")
    build.add_argument(
        "--provider",
        default="openai",
        help=f"LLM provider when --use-llm is enabled. Supported: {', '.join(supported_provider_names())}.",
    )
    build.add_argument("--model", default=None, help="Model name or provider endpoint id when --use-llm is enabled.")
    build.add_argument("--api-key", default=None, help="API key override. Prefer environment variables for normal use.")
    build.add_argument("--base-url", default=None, help="Provider base URL override for compatible/proxy endpoints.")
    build.add_argument("--max-output-tokens", type=int, default=None, help="Maximum generated tokens per page.")
    build.add_argument("--temperature", type=float, default=None, help="Optional model temperature.")
    build.add_argument(
        "--content-guard",
        choices=["auto", "off"],
        default="auto",
        help="Classify high-value learning content before note generation. auto uses the text LLM when --use-llm is enabled; off disables it.",
    )
    build.add_argument(
        "--export",
        default=None,
        help="Comma-separated extra export formats: markdown-toc, docx, pdf, latex, or all.",
    )
    build.add_argument(
        "--review-mode",
        choices=["off", "auto", "local", "llm"],
        default="off",
        help="Generate an exam-oriented review checklist. auto uses LLM when --use-llm is enabled, otherwise local.",
    )
    build.add_argument(
        "--exam-mode",
        choices=["off", "auto", "local", "llm"],
        default="off",
        help="Generate self-test questions and an interactive exam.html. auto uses LLM when --use-llm is enabled, otherwise local.",
    )
    build.add_argument(
        "--exam-question-count",
        type=int,
        default=12,
        help="Target number of self-test questions when --exam-mode is enabled.",
    )
    build.add_argument(
        "--export-toc",
        choices=["auto", "off"],
        default="auto",
        help="Whether markdown-toc export inserts a table of contents.",
    )
    build.add_argument(
        "--asset-mode",
        choices=["bundle", "absolute", "embed"],
        default="bundle",
        help="How notes.md references images. bundle copies assets into notes.assets, absolute uses local absolute paths, embed writes data URLs.",
    )
    build.add_argument(
        "--source-display",
        choices=["hidden", "footnote", "inline"],
        default="hidden",
        help="How source page/element references appear in notes.md.",
    )
    build.add_argument(
        "--note-context",
        choices=["auto", "document", "section", "page"],
        default="section",
        help="LLM note generation context. section is the quality-first default; auto uses document context for short files and section context for larger files.",
    )
    build.add_argument(
        "--note-style",
        choices=["article", "faithful"],
        default="article",
        help="Article mode is the default study-note organization; faithful mode keeps closer slide order.",
    )
    build.add_argument(
        "--note-profile",
        choices=["auto", "lecture-notes", "study-guide"],
        default="auto",
        help="Higher-level writing profile. lecture-notes enables teacher-style enrichment; study-guide emphasizes review and self-test framing.",
    )
    build.add_argument(
        "--note-language",
        choices=["auto", "zh", "en"],
        default="zh",
        help="Output language for generated notes. zh writes Chinese notes, en writes English notes, auto follows the source material.",
    )
    build.add_argument(
        "--term-policy",
        choices=["preserve", "translate", "bilingual"],
        default="bilingual",
        help="How academic terms are handled. bilingual keeps Chinese notes readable while preserving key English terms.",
    )
    build.add_argument(
        "--note-strategy",
        choices=["direct", "lecture-weave"],
        default="lecture-weave",
        help="direct uses the selected context directly; lecture-weave first explains each page, then weaves sections.",
    )
    build.add_argument(
        "--note-depth",
        choices=["concise", "balanced", "detailed", "very-detailed"],
        default=None,
        help="Detail level for LLM note writing. Defaults to detailed, or very-detailed when --note-profile lecture-notes is used.",
    )
    build.add_argument(
        "--teaching-enrichment",
        choices=["auto", "off", "force"],
        default="auto",
        help="Extra teacher-style enrichment pass after lecture-weave. auto runs for lecture-notes/study-guide; force runs for any LLM lecture-weave build.",
    )
    build.add_argument(
        "--weave-dedup",
        choices=["soft", "normal", "aggressive"],
        default="soft",
        help="How strongly lecture-weave merges repeated page-note content.",
    )
    build.add_argument(
        "--page-neighborhood",
        type=int,
        choices=[0, 1, 2],
        default=1,
        help="How many nearby page titles/briefs each page lecture may see.",
    )
    build.add_argument(
        "--deck-brief",
        choices=["auto", "off", "force"],
        default="auto",
        help="Build a global deck map before note generation. auto runs only with --use-llm and lecture-weave.",
    )
    build.add_argument(
        "--screenshot-policy",
        choices=["fallback", "always", "never"],
        default="fallback",
        help="How notes.md includes full-page screenshots. fallback shows them only when no local figure/image exists.",
    )
    build.add_argument(
        "--section-detection",
        choices=["auto", "local", "llm"],
        default="auto",
        help="How SlideNote detects section boundaries. auto uses LLM section detection when LLM notes are enabled, otherwise local rules.",
    )
    build.add_argument(
        "--semantic-layout",
        choices=["auto", "local", "vision"],
        default="auto",
        help="How semantic page blocks are built. auto uses local rules and vision-enhances selected pages when vision is enabled.",
    )
    build.add_argument(
        "--section-cache",
        choices=["on", "off", "refresh"],
        default="on",
        help="Section detection cache mode when --section-detection uses an LLM.",
    )
    build.add_argument("--section-cache-dir", type=Path, default=None, help="Section detection cache directory. Defaults to <out>/.cache/sections.")
    build.add_argument(
        "--cache",
        choices=["on", "off", "refresh"],
        default="on",
        help="LLM local cache mode: on reads/writes, refresh rewrites, off disables.",
    )
    build.add_argument(
        "--cache-dir",
        type=Path,
        default=None,
        help="LLM cache directory. Defaults to <out>/.cache/llm.",
    )
    build.add_argument(
        "--ocr",
        choices=["off", "auto", "all"],
        default="off",
        help="Dedicated OCR mode before vision/note generation. auto OCRs pages with little extracted text.",
    )
    build.add_argument(
        "--ocr-provider",
        default="baidu",
        help="OCR provider. Supported: baidu, mathpix, google.",
    )
    build.add_argument("--ocr-api-key", default=None, help="OCR API key/app id override.")
    build.add_argument("--ocr-secret-key", default=None, help="OCR secret key/app key override when the provider needs one.")
    build.add_argument("--ocr-endpoint", default=None, help="OCR endpoint override.")
    build.add_argument("--ocr-language", default="CHN_ENG", help="OCR language hint, for example CHN_ENG, ENG, or CHN.")
    build.add_argument(
        "--ocr-cache",
        choices=["on", "off", "refresh"],
        default="on",
        help="OCR local cache mode.",
    )
    build.add_argument("--ocr-cache-dir", type=Path, default=None, help="OCR cache directory. Defaults to <out>/.cache/ocr.")
    build.add_argument("--ocr-max-targets", type=int, default=None, help="Maximum OCR targets. Use 0 for unlimited.")
    build.add_argument("--ocr-min-text-chars", type=int, default=80, help="auto mode OCRs pages below this extracted text length.")
    build.add_argument("--ocr-min-area", type=int, default=120000, help="Minimum embedded image pixel area for OCR fallback.")
    build.add_argument("--ocr-max-edge", type=int, default=None, help="Resize OCR target long edge before API calls.")
    build.add_argument(
        "--figure-crop",
        choices=["off", "auto", "vision"],
        default="auto",
        help="Crop meaningful local figures from page screenshots. auto only runs when vision is enabled; vision forces bbox detection.",
    )
    build.add_argument(
        "--composite-figures",
        choices=["off", "auto"],
        default="auto",
        help="Detect diagrams assembled from many embedded images and crop them as one local figure.",
    )
    build.add_argument("--figure-max-targets", type=int, default=None, help="Maximum pages to send for figure bbox detection.")
    build.add_argument("--figure-max-crops-per-page", type=int, default=3, help="Maximum local figure crops per page.")
    build.add_argument("--figure-min-confidence", type=float, default=0.45, help="Minimum model confidence for accepting a figure crop.")
    build.add_argument("--figure-min-area", type=int, default=40000, help="Minimum crop area in pixels.")
    build.add_argument(
        "--image-ranking",
        choices=["off", "local"],
        default="local",
        help="Rank images by study value before vision and note generation.",
    )
    build.add_argument(
        "--figure-grounding",
        choices=["off", "auto", "vision"],
        default="auto",
        help="Anchor study-value figures to nearby text/table elements. auto uses local layout and existing vision/OCR summaries.",
    )
    build.add_argument(
        "--figure-placement",
        choices=["inline", "page-end"],
        default="inline",
        help="Where figures appear in notes.md. inline places them near anchored concepts; page-end groups them after each page.",
    )
    build.add_argument(
        "--figure-audit",
        choices=["off", "local", "llm"],
        default="local",
        help="Audit figure placement/explanation quality. local writes deterministic review flags; llm is reserved for future deeper checks.",
    )
    build.add_argument(
        "--figure-cache",
        choices=["on", "off", "refresh"],
        default="on",
        help="Figure crop local cache mode.",
    )
    build.add_argument("--figure-cache-dir", type=Path, default=None, help="Figure crop cache directory. Defaults to <out>/.cache/figure.")
    build.add_argument(
        "--vision",
        choices=["off", "auto", "all"],
        default="auto",
        help="Vision extraction mode before note generation. auto selects high-value images; all reads every page screenshot when possible.",
    )
    build.add_argument(
        "--vision-provider",
        default="qwen",
        help="Vision provider. Defaults to qwen for China-friendly visual parsing. Supported image providers currently include openai, qwen, doubao, gemini, and claude.",
    )
    build.add_argument("--vision-model", default=None, help="Vision model override.")
    build.add_argument("--vision-api-key", default=None, help="Vision API key override. Prefer environment variables.")
    build.add_argument("--vision-base-url", default=None, help="Vision provider base URL override.")
    build.add_argument(
        "--vision-cache",
        choices=["on", "off", "refresh"],
        default="on",
        help="Vision local cache mode.",
    )
    build.add_argument("--vision-cache-dir", type=Path, default=None, help="Vision cache directory. Defaults to <out>/.cache/vision.")
    build.add_argument("--vision-max-targets", type=int, default=None, help="Maximum images/screenshots to parse. Use 0 for unlimited.")
    build.add_argument("--vision-min-area", type=int, default=120000, help="Minimum embedded image pixel area for auto fallback selection.")
    build.add_argument("--vision-max-edge", type=int, default=None, help="Resize image long edge before API calls to reduce cost.")
    build.add_argument("--vision-max-output-tokens", type=int, default=None, help="Maximum vision output tokens per image.")
    build.add_argument("--vision-temperature", type=float, default=0.0, help="Vision model temperature.")
    build.add_argument("--vision-detail", choices=["low", "high", "auto"], default=None, help="OpenAI image detail setting.")

    doctor = subparsers.add_parser("doctor", help="Check local dependencies, optional tools, and API key environment variables.")
    doctor.add_argument("--json", type=Path, default=None, help="Write the doctor report as JSON to this path.")
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




if __name__ == "__main__":
    raise SystemExit(main())
