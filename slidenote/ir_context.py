from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from slidenote.models import Deck, SlidePage


@dataclass(slots=True)
class IRBuildContext:
    deck: Deck
    content_guard: dict[str, Any] | None = None
    coverage_report: dict[str, Any] | None = None
    content_guard_by_id: dict[str, dict[str, Any]] = field(init=False)
    coverage_by_id: dict[str, dict[str, Any]] = field(init=False)
    page_role_by_slide: dict[int, str] = field(init=False)

    def __post_init__(self) -> None:
        self.content_guard_by_id = _content_guard_index(self.content_guard)
        self.coverage_by_id = _coverage_index(self.coverage_report)
        self.page_role_by_slide = _page_role_index(self.content_guard)

    def semantic_by_id(self, page: SlidePage) -> dict[str, dict[str, Any]]:
        return {
            str(block.get("id")): block
            for block in page.semantic_blocks
            if isinstance(block, dict) and block.get("id")
        }

    def guard_item(self, element_id: str) -> dict[str, Any] | None:
        return self.content_guard_by_id.get(element_id)

    def coverage_item(self, element_id: str) -> dict[str, Any] | None:
        return self.coverage_by_id.get(element_id)

    def page_role(self, slide_id: int) -> str | None:
        return self.page_role_by_slide.get(slide_id)


def _content_guard_index(report: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not isinstance(report, dict):
        return {}
    items = report.get("items")
    if not isinstance(items, list):
        return {}
    return {
        str(item.get("element_id")): item
        for item in items
        if isinstance(item, dict) and item.get("element_id")
    }


def _coverage_index(report: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not isinstance(report, dict):
        return {}
    items = report.get("items")
    if not isinstance(items, list):
        return {}
    return {
        str(item.get("id")): item
        for item in items
        if isinstance(item, dict) and item.get("id")
    }


def _page_role_index(report: dict[str, Any] | None) -> dict[int, str]:
    if not isinstance(report, dict):
        return {}
    pages = report.get("pages")
    if not isinstance(pages, list):
        return {}
    roles: dict[int, str] = {}
    for page in pages:
        if not isinstance(page, dict) or page.get("slide_id") is None:
            continue
        role = page.get("page_role")
        if role:
            roles[int(page["slide_id"])] = str(role)
    return roles
