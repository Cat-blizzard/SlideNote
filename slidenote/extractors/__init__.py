from __future__ import annotations

from pathlib import Path

from slidenote.models import Deck
from slidenote.parser_adapters import (
    available_parser_choices,
    extract_deck as _extract_deck_via_adapter,
    parser_adapter_infos,
    resolve_parser_adapter,
)


def extract_deck(input_path: Path, output_root: Path, parser: str = "auto") -> Deck:
    return _extract_deck_via_adapter(input_path, output_root, parser=parser)


__all__ = ["available_parser_choices", "extract_deck", "parser_adapter_infos", "resolve_parser_adapter"]
