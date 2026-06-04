# SlideNote Studio GUI

[中文](README_GUI.zh-CN.md)

SlideNote Studio is a Streamlit interface for `python -m slidenote build` and `python -m slidenote study-pack`. It keeps the everyday UI small instead of exposing every internal tuning knob.

## What It Does

- Upload PPTX / PPT / PDF files.
- Choose one of two workflows:
  - `Lecture quality`: strong default note generation, requires API keys.
  - `Local preview`: local parsing preview, no API calls.
- Enter Text / Vision / OCR API keys on the page. Keys are passed only through the child-process environment, not command-line flags.
- Select extra exports: Markdown ZIP, TOC Markdown, Word, PDF, or LaTeX.
- Monitor progress, ETA, Doctor readiness, quality / coverage / source map / page explorer / cost reports.
- Generate a study pack from an existing output directory: `review.md`, `exam.md`, `exam.json`, `exam.html`, and related files.
- Download `notes.zip`, `notes.md`, `coverage.md`, export files, or the complete output ZIP.

For sharing Markdown notes, prefer `notes.zip`; it contains both `notes.md` and `notes.assets/` so images render on another computer.

## Install

Recommended:

```powershell
.\install.ps1
```

Manual:

```bash
python -m pip install -e ".[dev,llm,gui]"
```

## Run

```powershell
.\run_gui.ps1
```

Manual:

```bash
streamlit run gui/app.py
```

## First Test

Start with **Local preview**. It checks parsing and basic output without any API calls.

For production notes, use **Lecture quality**, enter a Text API key, keep Vision=`auto` for image-heavy slides, and add a Qwen/DashScope vision key. Scanned PDFs may also need OCR credentials.

## Study Pack

After a build finishes, open the **Study pack** tab in the results area and click **Generate study pack from this output**. The GUI runs:

```powershell
python -m slidenote study-pack <output-dir> --question-count 12
```

It uses the Text provider key when available and falls back to local rules otherwise.

## Exports

The **Exports** sidebar section can generate:

- `notes.zip`: Markdown package with image assets, no Pandoc needed.
- `notes.toc.md`: TOC Markdown, no Pandoc needed.
- `notes.docx`: Word document, requires Pandoc.
- `notes.pdf`: PDF handout, requires Pandoc + LibreOffice.
- `notes.tex`: LaTeX source, requires Pandoc.

Export status is written to `export_report.json`. If Pandoc or LibreOffice is missing, the GUI shows install hints before the run.
