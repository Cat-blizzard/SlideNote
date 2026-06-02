from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from slidenote.llm import get_provider_spec


def _friendly_build_error(exc: Exception, args: argparse.Namespace) -> str | None:
    message = str(exc)
    if "Missing API key for provider `" not in message:
        return None
    provider = _provider_from_missing_key_error(message)
    if not provider:
        return None
    try:
        missing_spec = get_provider_spec(provider)
    except ValueError:
        return None

    if _vision_features_enabled(args):
        try:
            vision_spec = get_provider_spec(args.vision_provider)
        except ValueError:
            vision_spec = None
        if vision_spec and missing_spec.canonical_name == vision_spec.canonical_name:
            envs = ", ".join(vision_spec.api_key_envs)
            features = []
            if args.vision != "off":
                features.append("vision")
            if args.figure_crop == "vision" or (args.figure_crop == "auto" and args.vision != "off"):
                features.append("figure-crop")
            if args.semantic_layout == "vision" or (args.semantic_layout == "auto" and args.vision != "off"):
                features.append("semantic-layout")
            if args.figure_grounding == "vision":
                features.append("figure-grounding")
            feature_text = "/".join(features) or "vision"
            return (
                f"当前开启了 {feature_text}，但视觉模型 provider `{vision_spec.canonical_name}` 没有可用 API key。\n"
                f"请设置 {envs}，或传 `--vision-api-key ...`。\n"
                "如果只想用文本模型生成笔记，请加：`--vision off --figure-crop off`。"
            )

    if args.use_llm:
        try:
            text_spec = get_provider_spec(args.provider)
        except ValueError:
            text_spec = None
        if text_spec and missing_spec.canonical_name == text_spec.canonical_name:
            envs = ", ".join(text_spec.api_key_envs)
            return (
                f"当前开启了 `--use-llm`，但文本模型 provider `{text_spec.canonical_name}` 没有可用 API key。\n"
                f"请设置 {envs}，或传 `--api-key ...`。\n"
                "如果只想先生成本地规则草稿，请去掉 `--use-llm`。"
            )
    return None


def _provider_from_missing_key_error(message: str) -> str | None:
    marker = "Missing API key for provider `"
    start = message.find(marker)
    if start == -1:
        return None
    start += len(marker)
    end = message.find("`", start)
    if end == -1:
        return None
    return message[start:end]


def _vision_features_enabled(args: argparse.Namespace) -> bool:
    return (
        args.vision != "off"
        or args.semantic_layout == "vision"
        or (args.semantic_layout == "auto" and args.vision != "off")
        or args.figure_grounding == "vision"
        or args.figure_crop == "vision"
        or (args.figure_crop == "auto" and args.vision != "off")
    )


def _should_build_deck_brief(args: argparse.Namespace) -> bool:
    if args.deck_brief == "off":
        return False
    if args.deck_brief == "force":
        return True
    return bool(args.use_llm and args.note_strategy == "lecture-weave")


def _apply_note_profile_defaults(args: argparse.Namespace) -> None:
    if getattr(args, "note_depth", None) is None:
        if getattr(args, "note_profile", "auto") == "lecture-notes":
            args.note_depth = "very-detailed"
        else:
            args.note_depth = "detailed"


BUILD_PRESET_DEFAULTS: dict[str, dict[str, Any]] = {
    "fast": {
        "speed_mode": "fast",
        "note_context": "auto",
        "note_style": "article",
        "note_profile": "auto",
        "note_strategy": "direct",
        "note_depth": "balanced",
        "teaching_enrichment": "off",
        "deck_brief": "off",
        "content_guard": "off",
        "section_detection": "local",
        "ocr": "off",
        "vision": "off",
        "figure_crop": "off",
        "semantic_layout": "local",
    },
    "faithful": {
        "speed_mode": "quality",
        "note_context": "section",
        "note_style": "faithful",
        "note_profile": "auto",
        "note_strategy": "lecture-weave",
        "note_depth": "detailed",
        "teaching_enrichment": "off",
        "deck_brief": "auto",
        "content_guard": "auto",
        "section_detection": "auto",
    },
    "lecture": {
        "speed_mode": "quality",
        "note_context": "section",
        "note_style": "article",
        "note_profile": "lecture-notes",
        "note_strategy": "lecture-weave",
        "teaching_enrichment": "auto",
        "deck_brief": "auto",
        "content_guard": "auto",
        "section_detection": "auto",
        "source_display": "hidden",
    },
}


def _apply_build_preset_defaults(args: argparse.Namespace) -> None:
    preset = getattr(args, "preset", "auto")
    if preset == "auto":
        return
    preset_defaults = BUILD_PRESET_DEFAULTS[preset]
    explicit_options = set(getattr(args, "_explicit_options", set()) or set())
    for name, value in preset_defaults.items():
        if name not in explicit_options:
            setattr(args, name, value)


def _apply_speed_mode_defaults(args: argparse.Namespace) -> None:
    presets = {
        "fast": {
            "max_output_tokens": 2500,
            "ocr_max_targets": 40,
            "ocr_max_edge": 1200,
            "figure_max_targets": 25,
            "vision_max_targets": 25,
            "vision_max_edge": 1000,
            "vision_max_output_tokens": 800,
            "vision_detail": "low",
        },
        "balanced": {
            "max_output_tokens": 4096,
            "ocr_max_targets": 120,
            "ocr_max_edge": 1800,
            "figure_max_targets": 80,
            "vision_max_targets": 80,
            "vision_max_edge": 1400,
            "vision_max_output_tokens": 1200,
            "vision_detail": "low",
        },
        "quality": {
            "max_output_tokens": 7000,
            "ocr_max_targets": 0,
            "ocr_max_edge": 2200,
            "figure_max_targets": 160,
            "vision_max_targets": 160,
            "vision_max_edge": 1800,
            "vision_max_output_tokens": 2000,
            "vision_detail": "high",
        },
        "debug": {
            "max_output_tokens": 4096,
            "ocr_max_targets": 20,
            "ocr_max_edge": 1400,
            "figure_max_targets": 20,
            "vision_max_targets": 20,
            "vision_max_edge": 1200,
            "vision_max_output_tokens": 1000,
            "vision_detail": "low",
        },
    }
    preset = presets[args.speed_mode]
    for name, value in preset.items():
        if getattr(args, name, None) is None:
            setattr(args, name, value)


def _resolve_api_concurrency(args: argparse.Namespace) -> dict[str, int]:
    fallback = max(1, int(args.concurrency or 1))
    return {
        "llm": _coerce_api_concurrency(args.llm_concurrency, fallback),
        "vision": _coerce_api_concurrency(args.vision_concurrency, fallback),
        "ocr": _coerce_api_concurrency(args.ocr_concurrency, fallback),
        "figure": _coerce_api_concurrency(args.figure_concurrency, fallback),
    }


def _coerce_api_concurrency(value: int | None, fallback: int) -> int:
    if value is None:
        value = fallback
    return max(1, int(value))


def _resolve_cache_dirs(args: argparse.Namespace, output_root: Path) -> dict[str, Path | None]:
    global_cache = args.global_cache_dir.resolve() if args.global_cache_dir else None
    return {
        "llm": args.cache_dir.resolve() if args.cache_dir else (global_cache / "llm" if global_cache else None),
        "ocr": args.ocr_cache_dir.resolve() if args.ocr_cache_dir else (global_cache / "ocr" if global_cache else None),
        "vision": args.vision_cache_dir.resolve() if args.vision_cache_dir else (global_cache / "vision" if global_cache else None),
        "figure": args.figure_cache_dir.resolve() if args.figure_cache_dir else (global_cache / "figure" if global_cache else None),
        "sections": args.section_cache_dir.resolve() if args.section_cache_dir else (global_cache / "sections" if global_cache else None),
    }


def _parse_slide_ranges(value: str | None) -> set[int]:
    if not value:
        return set()
    result: set[int] = set()
    for raw_part in value.split(","):
        part = raw_part.strip()
        if not part:
            continue
        if "-" in part:
            start_text, end_text = part.split("-", 1)
            start = int(start_text)
            end = int(end_text)
            if start <= 0 or end <= 0 or end < start:
                raise ValueError(f"Invalid slide range: {part}")
            result.update(range(start, end + 1))
        else:
            slide_id = int(part)
            if slide_id <= 0:
                raise ValueError(f"Invalid slide id: {part}")
            result.add(slide_id)
    return result
