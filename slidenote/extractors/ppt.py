from __future__ import annotations

from pathlib import Path

from slidenote.models import Deck
from slidenote.utils import find_executable, run_command


def extract_ppt(input_path: Path, output_root: Path) -> Deck:
    soffice = find_executable(["soffice", "libreoffice"])
    if not soffice:
        raise RuntimeError(".ppt input requires LibreOffice (`soffice`) so SlideNote can convert it to PDF.")

    converted_dir = output_root / "_converted"
    converted_dir.mkdir(parents=True, exist_ok=True)
    run_command(
        [
            soffice,
            "--headless",
            "--convert-to",
            "pdf",
            "--outdir",
            str(converted_dir),
            str(input_path),
        ],
        cwd=input_path.parent,
    )

    pdf_path = converted_dir / f"{input_path.stem}.pdf"
    if not pdf_path.exists():
        raise RuntimeError(f"LibreOffice did not create the expected PDF: {pdf_path}")

    from .pdf import extract_pdf

    deck = extract_pdf(pdf_path, output_root)
    deck.source_path = str(input_path)
    deck.source_type = "ppt"
    deck.warnings.append("Original .ppt was converted to PDF before extraction.")
    return deck

