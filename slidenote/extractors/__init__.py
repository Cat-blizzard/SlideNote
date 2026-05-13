from __future__ import annotations

from pathlib import Path

from slidenote.models import Deck


def extract_deck(input_path: Path, output_root: Path) -> Deck:
    suffix = input_path.suffix.lower()
    if suffix == ".pptx":
        from .pptx import extract_pptx

        return extract_pptx(input_path, output_root)
    if suffix == ".pdf":
        from .pdf import extract_pdf

        return extract_pdf(input_path, output_root)
    if suffix == ".ppt":
        from .ppt import extract_ppt

        return extract_ppt(input_path, output_root)
    raise ValueError(f"Unsupported input format: {input_path.suffix}")

