# SlideNote Studio GUI

This GUI wraps the existing `python -m slidenote build` pipeline with a Streamlit page. It does not replace or modify the core parser/LLM pipeline.

## What it adds

- Upload PPTX / PPT / PDF without touching the command line.
- Paste API keys inside the page for one run; keys are not written to source code.
- Choose workflow presets:
  - Fast API draft
  - Balanced study notes
  - Quality detailed notes
  - Local safe preview
- Control runtime speed:
  - concurrency
  - global cache
  - OCR/Vision max target limits
  - direct vs lecture-weave note strategy
  - deck brief/content guard switches
- Watch `progress.json` during the run.
- View `notes.md`, `coverage.md`, `run_summary.json`, usage files, and token/cost reports.

## Install

```bash
python -m pip install -e .
python -m pip install -r requirements-gui.txt
```

If you use LLM providers:

```bash
python -m pip install -e ".[llm]"
```

## Run

```bash
streamlit run gui/app.py
```

## API keys

The GUI can pass API keys directly to one run using CLI arguments and environment variables. This avoids asking users to set terminal environment variables manually.

Do not commit keys to GitHub.

## Speed tips

For first tests, use **Local safe preview** or **Fast API draft**.

To reduce runtime:

1. Use `direct` note strategy instead of `lecture-weave`.
2. Use `vision=auto`, not `vision=all`.
3. Use `ocr=auto`, not `ocr=all`.
4. Set `Vision max targets` and `OCR max targets` to small numbers for testing.
5. Keep cache enabled and use the shared global cache.
6. Increase concurrency to 3-6 only when the API provider rate limit allows it.

## Token and cost report

After a successful run, the GUI generates:

- `cost_report.json`
- `cost_report.md`
- `cost_dashboard.html`

Prices are read from `pricing.template.json`. Keep the prices updated manually using official provider pricing pages.
