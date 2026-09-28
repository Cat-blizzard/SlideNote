"""Copy, embed and relink image assets referenced by the notes."""

from __future__ import annotations

import base64
import mimetypes
import re
import shutil
from pathlib import Path

from slidenote.image_ranking import sorted_images_by_importance
from slidenote.models import Deck, SlidePage


def _prepare_note_assets(deck: Deck, output_root: Path, asset_mode: str, screenshot_policy: str) -> tuple[dict[str, str], list[str]]:
    asset_map: dict[str, str] = {}
    warnings: list[str] = []
    seen_destinations: set[Path] = set()
    for rel_path, kind in _iter_note_asset_paths(deck, screenshot_policy=screenshot_policy):
        if rel_path in asset_map:
            continue
        source_path = _resolve_output_asset(output_root, rel_path)
        if not source_path.exists():
            warnings.append(f"Missing note asset: {rel_path}")
            continue
        if asset_mode == "absolute":
            asset_map[rel_path] = source_path.as_posix()
        elif asset_mode == "embed":
            embedded = _embed_asset(source_path)
            if embedded:
                asset_map[rel_path] = embedded
            else:
                warnings.append(f"Could not embed note asset: {rel_path}")
        else:
            destination = _bundled_asset_destination(output_root, rel_path, kind, seen_destinations)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, destination)
            asset_map[rel_path] = destination.relative_to(output_root).as_posix()
            seen_destinations.add(destination)
    return asset_map, warnings


def _iter_note_asset_paths(deck: Deck, screenshot_policy: str) -> list[tuple[str, str]]:
    paths: list[tuple[str, str]] = []
    for page in deck.pages:
        if _should_render_screenshot(page, screenshot_policy):
            paths.append((page.page_screenshot, "screenshots"))
        for image in sorted_images_by_importance(page.images):
            if not image.ignored:
                kind = "figures" if image.role in {"figure_crop", "composite_figure"} else "images"
                paths.append((image.path, kind))
    return paths


def _resolve_output_asset(output_root: Path, path: str) -> Path:
    asset_path = Path(path)
    if asset_path.is_absolute():
        return asset_path
    return (output_root / asset_path).resolve()


def _bundled_asset_destination(output_root: Path, rel_path: str, kind: str, seen_destinations: set[Path]) -> Path:
    source = Path(rel_path)
    subdir = "screenshots" if kind == "screenshots" else "figures" if kind == "figures" else "images"
    stem = source.stem or "asset"
    suffix = source.suffix or ".png"
    destination = output_root / "notes.assets" / subdir / f"{stem}{suffix}"
    counter = 2
    while destination in seen_destinations:
        destination = output_root / "notes.assets" / subdir / f"{stem}-{counter}{suffix}"
        counter += 1
    return destination


def _embed_asset(source_path: Path) -> str | None:
    try:
        data = source_path.read_bytes()
    except OSError:
        return None
    mime_type, _ = mimetypes.guess_type(source_path.name)
    if not mime_type:
        mime_type = "application/octet-stream"
    return f"data:{mime_type};base64,{base64.b64encode(data).decode('ascii')}"


def _asset_display_path(path: str, asset_map: dict[str, str]) -> str:
    return asset_map.get(path, path)


def _repair_markdown_image_links(markdown: str, output_root: Path, asset_map: dict[str, str]) -> str:
    if not markdown or not asset_map:
        return markdown
    exact, by_name = _asset_link_rewrite_maps(asset_map)

    def replace(match: re.Match[str]) -> str:
        alt = match.group(1)
        target = match.group(2)
        cleaned = _normalize_image_target(target)
        if not cleaned or cleaned.startswith(("data:", "http://", "https://")):
            return match.group(0)
        replacement = exact.get(cleaned) or exact.get(_strip_current_dir_prefix(cleaned))
        if replacement and replacement != cleaned:
            return f"![{alt}]({replacement})"
        if _image_target_exists(cleaned, output_root):
            return match.group(0)
        if replacement is None:
            replacement = by_name.get(_path_name(cleaned))
        if not replacement:
            return match.group(0)
        return f"![{alt}]({replacement})"

    return re.sub(r"!\[([^\]]*)]\(([^)]+)\)", replace, markdown)


def _asset_link_rewrite_maps(asset_map: dict[str, str]) -> tuple[dict[str, str], dict[str, str]]:
    exact: dict[str, str] = {}
    by_name_values: dict[str, set[str]] = {}
    for raw_path, display_path in asset_map.items():
        display = _normalize_image_target(display_path)
        if not display:
            continue
        for candidate in {raw_path, display_path, _normalize_image_target(raw_path), display}:
            key = _normalize_image_target(candidate)
            if key and not key.startswith(("data:", "http://", "https://")):
                exact[key] = display
                exact[_strip_current_dir_prefix(key)] = display
        by_name_values.setdefault(_path_name(raw_path), set()).add(display)
        by_name_values.setdefault(_path_name(display_path), set()).add(display)
    by_name = {name: next(iter(values)) for name, values in by_name_values.items() if name and len(values) == 1}
    return exact, by_name


def _strip_current_dir_prefix(path: str) -> str:
    """Remove leading ``./`` segments only; ``../`` must survive (unlike ``lstrip``)."""
    while path.startswith("./"):
        path = path[2:]
    return path


def _normalize_image_target(target: object) -> str:
    return str(target or "").strip().strip("<>").replace("\\", "/")


def _path_name(path: object) -> str:
    return _normalize_image_target(path).rstrip("/").rsplit("/", 1)[-1]


def _image_target_exists(target: str, output_root: Path) -> bool:
    path = Path(target)
    candidate = path if path.is_absolute() else output_root / path
    return candidate.exists()


def _should_render_screenshot(page: SlidePage, screenshot_policy: str) -> bool:
    if not page.page_screenshot:
        return False
    if screenshot_policy == "always":
        return True
    if screenshot_policy == "never":
        return False
    return not any(not image.ignored and image.role != "page_image" for image in page.images)


def _validate_markdown_image_links(markdown: str, output_root: Path) -> list[str]:
    warnings: list[str] = []
    for target in re.findall(r"!\[[^\]]*]\(([^)]+)\)", markdown):
        cleaned = target.strip().strip("<>")
        if not cleaned or cleaned.startswith(("data:", "http://", "https://")):
            continue
        path = Path(cleaned)
        if path.is_absolute():
            candidate = path
        else:
            candidate = output_root / path
        if not candidate.exists():
            warnings.append(f"Markdown image link target is missing: {cleaned}")
    for target in re.findall(r"`(!\[[^\]]*]\([^)]+\))`", markdown):
        warnings.append(f"Markdown image is wrapped as code and will not render: {target}")
    return warnings
