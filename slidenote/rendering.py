from __future__ import annotations

from pathlib import Path

from slidenote.models import normalize_rel_path
from slidenote.utils import find_executable, run_command


def render_pptx_screenshots(input_path: Path, screenshots_dir: Path, output_root: Path) -> tuple[dict[int, str], list[str]]:
    screenshots_dir.mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []

    exported = _render_with_powerpoint(input_path, screenshots_dir, output_root)
    if exported:
        return exported, warnings

    soffice = find_executable(["soffice", "libreoffice"])
    if not soffice:
        warnings.append("LibreOffice/PowerPoint not found; PPTX full-slide screenshots were skipped.")
        return {}, warnings

    converted_dir = output_root / "_converted"
    converted_dir.mkdir(parents=True, exist_ok=True)
    try:
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
    except Exception as exc:
        warnings.append(f"LibreOffice conversion failed; PPTX screenshots were skipped: {exc}")
        return {}, warnings

    pdf_path = converted_dir / f"{input_path.stem}.pdf"
    if not pdf_path.exists():
        warnings.append("LibreOffice conversion did not produce a PDF; PPTX screenshots were skipped.")
        return {}, warnings

    return _render_pdf_pages(pdf_path, screenshots_dir, output_root, warnings), warnings


def _render_pdf_pages(pdf_path: Path, screenshots_dir: Path, output_root: Path, warnings: list[str]) -> dict[int, str]:
    try:
        import fitz
    except ImportError:
        warnings.append("PyMuPDF is not installed; converted PPTX screenshots were skipped.")
        return {}

    result: dict[int, str] = {}
    doc = fitz.open(pdf_path)
    for page_index, page in enumerate(doc, start=1):
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
        target = screenshots_dir / f"slide{page_index}.png"
        pix.save(target)
        result[page_index] = normalize_rel_path(target, output_root)
    doc.close()
    return result


def _render_with_powerpoint(input_path: Path, screenshots_dir: Path, output_root: Path) -> dict[int, str]:
    try:
        import win32com.client  # type: ignore[import-not-found]
    except Exception:
        return {}

    powerpoint = None
    presentation = None
    try:
        powerpoint = win32com.client.Dispatch("PowerPoint.Application")
        presentation = powerpoint.Presentations.Open(str(input_path.resolve()), WithWindow=False)
        result: dict[int, str] = {}
        for slide_index in range(1, presentation.Slides.Count + 1):
            target = screenshots_dir / f"slide{slide_index}.png"
            presentation.Slides(slide_index).Export(str(target.resolve()), "PNG")
            result[slide_index] = normalize_rel_path(target, output_root)
        return result
    except Exception:
        return {}
    finally:
        if presentation is not None:
            try:
                presentation.Close()
            except Exception:
                pass
        if powerpoint is not None:
            try:
                powerpoint.Quit()
            except Exception:
                pass

