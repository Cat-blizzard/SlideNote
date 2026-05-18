from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from slidenote.figure_grounding import FIGURE_PLACEMENT_MODES
from slidenote.models import Deck

from .assembly import (
    NoteContext,
    _prepare_note_assets,
    _select_note_contexts,
    _validate_markdown_image_links,
)
from .local import _generate_notes_locally
from .orchestrator import _generate_notes_with_llm
from .prompts import _llm_page_prompt

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ASSET_MODES = {"bundle", "absolute", "embed"}
SOURCE_DISPLAY_MODES = {"hidden", "footnote", "inline"}
NOTE_CONTEXT_MODES = {"auto", "document", "section", "page"}
NOTE_STYLES = {"article", "faithful"}
NOTE_STRATEGIES = {"direct", "lecture-weave"}
NOTE_DEPTHS = {"concise", "balanced", "detailed"}
WEAVE_DEDUP_MODES = {"soft", "normal", "aggressive"}
SCREENSHOT_POLICIES = {"fallback", "always", "never"}
NOTE_LANGUAGES = {"auto", "zh", "en"}
TERM_POLICIES = {"preserve", "translate", "bilingual"}

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class NoteGenerationResult:
    markdown: str
    llm_usage: dict[str, Any] | None = None
    asset_warnings: list[str] | None = None
    page_notes: dict[str, Any] | None = None
    page_notes_markdown: str | None = None
    weave_report: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def estimate_note_generation_steps(
    deck: Deck,
    note_context: str = "section",
    note_strategy: str = "lecture-weave",
    section_plan: dict[str, Any] | None = None,
) -> int:
    if note_strategy == "lecture-weave":
        return len(deck.pages) + len(_select_note_contexts(deck, note_context, section_plan=section_plan))
    return len(_select_note_contexts(deck, note_context, section_plan=section_plan))


def generate_notes(
    deck: Deck,
    output_root: Path,
    use_llm: bool = False,
    provider: str = "openai",
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    max_output_tokens: int = 4096,
    temperature: float | None = None,
    cache_mode: str = "on",
    cache_dir: Path | None = None,
    concurrency: int = 1,
    refresh_slide_ids: set[int] | None = None,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
    asset_mode: str = "bundle",
    source_display: str = "hidden",
    note_context: str = "section",
    note_style: str = "article",
    note_strategy: str = "lecture-weave",
    note_depth: str = "detailed",
    note_language: str = "zh",
    term_policy: str = "bilingual",
    weave_dedup: str = "soft",
    page_neighborhood: int = 1,
    screenshot_policy: str = "fallback",
    figure_placement: str = "inline",
    section_plan: dict[str, Any] | None = None,
    deck_brief: dict[str, Any] | None = None,
    content_guard: dict[str, Any] | None = None,
) -> str:
    return generate_notes_result(
        deck=deck,
        output_root=output_root,
        use_llm=use_llm,
        provider=provider,
        model=model,
        api_key=api_key,
        base_url=base_url,
        max_output_tokens=max_output_tokens,
        temperature=temperature,
        cache_mode=cache_mode,
        cache_dir=cache_dir,
        concurrency=concurrency,
        refresh_slide_ids=refresh_slide_ids,
        progress_callback=progress_callback,
        asset_mode=asset_mode,
        source_display=source_display,
        note_context=note_context,
        note_style=note_style,
        note_strategy=note_strategy,
        note_depth=note_depth,
        note_language=note_language,
        term_policy=term_policy,
        weave_dedup=weave_dedup,
        page_neighborhood=page_neighborhood,
        screenshot_policy=screenshot_policy,
        figure_placement=figure_placement,
        section_plan=section_plan,
        deck_brief=deck_brief,
        content_guard=content_guard,
    ).markdown


def generate_notes_result(
    deck: Deck,
    output_root: Path,
    use_llm: bool = False,
    provider: str = "openai",
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    max_output_tokens: int = 4096,
    temperature: float | None = None,
    cache_mode: str = "on",
    cache_dir: Path | None = None,
    concurrency: int = 1,
    refresh_slide_ids: set[int] | None = None,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
    asset_mode: str = "bundle",
    source_display: str = "hidden",
    note_context: str = "section",
    note_style: str = "article",
    note_strategy: str = "lecture-weave",
    note_depth: str = "detailed",
    note_language: str = "zh",
    term_policy: str = "bilingual",
    weave_dedup: str = "soft",
    page_neighborhood: int = 1,
    screenshot_policy: str = "fallback",
    figure_placement: str = "inline",
    section_plan: dict[str, Any] | None = None,
    deck_brief: dict[str, Any] | None = None,
    content_guard: dict[str, Any] | None = None,
) -> NoteGenerationResult:
    _validate_generation_options(
        asset_mode,
        source_display,
        note_context,
        note_style,
        note_strategy,
        note_depth,
        note_language,
        term_policy,
        weave_dedup,
        page_neighborhood,
        screenshot_policy,
        figure_placement,
    )
    asset_map, asset_warnings = _prepare_note_assets(deck, output_root, asset_mode, screenshot_policy=screenshot_policy)
    if use_llm:
        result = _generate_notes_with_llm(
            deck,
            output_root=output_root,
            provider=provider,
            model=model,
            api_key=api_key,
            base_url=base_url,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
            cache_mode=cache_mode,
            cache_dir=cache_dir,
            concurrency=concurrency,
            refresh_slide_ids=refresh_slide_ids,
            progress_callback=progress_callback,
            asset_map=asset_map,
            asset_mode=asset_mode,
            source_display=source_display,
            note_context=note_context,
            note_style=note_style,
            note_strategy=note_strategy,
            note_depth=note_depth,
            note_language=note_language,
            term_policy=term_policy,
            weave_dedup=weave_dedup,
            page_neighborhood=page_neighborhood,
            screenshot_policy=screenshot_policy,
            figure_placement=figure_placement,
            section_plan=section_plan,
            deck_brief=deck_brief,
            content_guard=content_guard,
        )
        result.asset_warnings = (result.asset_warnings or []) + asset_warnings + _validate_markdown_image_links(
            result.markdown, output_root
        )
        return result
    markdown = _generate_notes_locally(
        deck,
        asset_map=asset_map,
        source_display=source_display,
        note_style=note_style,
        screenshot_policy=screenshot_policy,
        figure_placement=figure_placement,
        section_plan=section_plan,
    )
    asset_warnings = asset_warnings + _validate_markdown_image_links(markdown, output_root)
    return NoteGenerationResult(markdown=markdown, asset_warnings=asset_warnings)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _validate_generation_options(
    asset_mode: str,
    source_display: str,
    note_context: str,
    note_style: str,
    note_strategy: str,
    note_depth: str,
    note_language: str,
    term_policy: str,
    weave_dedup: str,
    page_neighborhood: int,
    screenshot_policy: str,
    figure_placement: str,
) -> None:
    if asset_mode not in ASSET_MODES:
        raise ValueError(f"asset_mode must be one of: {', '.join(sorted(ASSET_MODES))}")
    if source_display not in SOURCE_DISPLAY_MODES:
        raise ValueError(f"source_display must be one of: {', '.join(sorted(SOURCE_DISPLAY_MODES))}")
    if note_context not in NOTE_CONTEXT_MODES:
        raise ValueError(f"note_context must be one of: {', '.join(sorted(NOTE_CONTEXT_MODES))}")
    if note_style not in NOTE_STYLES:
        raise ValueError(f"note_style must be one of: {', '.join(sorted(NOTE_STYLES))}")
    if note_strategy not in NOTE_STRATEGIES:
        raise ValueError(f"note_strategy must be one of: {', '.join(sorted(NOTE_STRATEGIES))}")
    if note_depth not in NOTE_DEPTHS:
        raise ValueError(f"note_depth must be one of: {', '.join(sorted(NOTE_DEPTHS))}")
    if note_language not in NOTE_LANGUAGES:
        raise ValueError(f"note_language must be one of: {', '.join(sorted(NOTE_LANGUAGES))}")
    if term_policy not in TERM_POLICIES:
        raise ValueError(f"term_policy must be one of: {', '.join(sorted(TERM_POLICIES))}")
    if weave_dedup not in WEAVE_DEDUP_MODES:
        raise ValueError(f"weave_dedup must be one of: {', '.join(sorted(WEAVE_DEDUP_MODES))}")
    if page_neighborhood not in {0, 1, 2}:
        raise ValueError("page_neighborhood must be one of: 0, 1, 2")
    if screenshot_policy not in SCREENSHOT_POLICIES:
        raise ValueError(f"screenshot_policy must be one of: {', '.join(sorted(SCREENSHOT_POLICIES))}")
    if figure_placement not in FIGURE_PLACEMENT_MODES:
        raise ValueError(f"figure_placement must be one of: {', '.join(sorted(FIGURE_PLACEMENT_MODES))}")
