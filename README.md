<p align="center">
  <img src="assets/slidenote-logo.png" alt="SlideNote" width="520">
</p>

<h1 align="center">SlideNote</h1>

<p align="center">
  <strong>Coverage-aware course notes from lecture slides</strong>
</p>

<p align="center">
  Turn PPT/PDF into readable, traceable notes with images, OCR/vision, Lecture-Weave writing, and coverage checks.
</p>

<p align="center">
  <em>Not just a slide summarizer — a faithful study-document pipeline.</em>
</p>

<p align="center">
  <img alt="Python" src="https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white">
  <img alt="PPT PDF" src="https://img.shields.io/badge/Input-PPTX%20%7C%20PDF-2F6FED">
  <img alt="LLM" src="https://img.shields.io/badge/LLM-Multi--provider-7C3AED">
  <img alt="Vision OCR" src="https://img.shields.io/badge/Vision%20%2B%20OCR-supported-0F766E">
  <img alt="Status" src="https://img.shields.io/badge/Status-MVP-F59E0B">
</p>

<p align="center">
  <a href="README.md">English</a> |
  <a href="README.zh-CN.md">中文</a> |
  <a href="CONFIG.zh-CN.md">Config</a> |
  <a href="ROADMAP.zh-CN.md">Roadmap</a>
</p>

---

## Contents

- [Quick Start](#quick-start)
- [Pipeline And Presets](#slidenote-pipeline)
- [Install And Configure](#local-requirements)
- [Usage, Outputs, And Study Modes](#basic-usage)
- [Speed And Partial Refresh](#speed-progress-and-partial-refresh)
- [Note Quality Controls](#note-rendering-options)
- [Vision, OCR, And Figures](#image-filtering-and-source-map)
- [License And Acknowledgements](#license)

## Quick Start

```powershell
git clone https://github.com/Cat-blizzard/SlideNote.git
cd SlideNote
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev,llm]"
python -m slidenote doctor
```

For text-only AI notes with one LLM key:

```powershell
$env:DEEPSEEK_API_KEY="..."
python -m slidenote build path\to\lecture.pdf --out outputs\lecture --use-llm --provider deepseek --vision off --figure-crop off
```

For image-aware notes, add a Qwen/DashScope key. Qwen is the default vision provider:

```powershell
$env:DASHSCOPE_API_KEY="..."
$env:DEEPSEEK_API_KEY="..."
python -m slidenote build path\to\lecture.pdf --out outputs\lecture --use-llm --provider deepseek
```

Open `outputs\lecture\notes.md`. Images are bundled under `outputs\lecture\notes.assets\`.

## Optional GUI

SlideNote Studio is a Streamlit wrapper around the same CLI pipeline. It lets users upload PPT/PDF files, configure API keys in the page, run presets, monitor progress/ETA, inspect token and cost reports, review page-level source traces, and download the generated results.

```powershell
python -m pip install -e ".[dev,llm,gui]"
streamlit run gui/app.py
```

GUI highlights:

- **API setup without command-line env edits:** API keys entered in the page are passed to the build subprocess through environment variables and are not written to source code or command-line arguments.
- **Speed and cost controls:** exposes `--speed-mode`, global `--concurrency`, `--llm-concurrency`, `--vision-concurrency`, `--ocr-concurrency`, `--figure-concurrency`, shared global cache, OCR/Vision target caps, and page-level `--refresh-pages`.
- **Runtime visibility:** shows readable API states (`Off`, `Missing key`, `Ready`), progress, ETA estimate, stage output, `run_summary.json`, usage files, and token/cost dashboards.
- **Doctor panel:** runs the same environment checks as `slidenote doctor` and shows missing dependencies/API setup hints in the GUI.
- **Review workspace:** provides page explorer views that link original page screenshots, parsed elements, generated notes, page modality, coverage quality, and a manual modality correction manifest.
- **Save and export UX:** supports the default `gui_runs/outputs` workspace or a custom output folder. The sidebar can request `notes.toc.md`, Word `notes.docx`, PDF `notes.pdf`, and LaTeX `notes.tex`, and the results area offers one-click downloads for generated exports and the full result ZIP. Word/LaTeX require Pandoc; PDF is generated from the Word document through LibreOffice for more reliable Chinese/CJK layout. The GUI warns before the run when Pandoc or LibreOffice is missing.

See [gui/README_GUI.md](gui/README_GUI.md) for details.

> GUI export note: Markdown TOC export does not need extra tools. Word and LaTeX use Pandoc, while PDF is produced by converting `notes.docx` with LibreOffice; `export_report.json` records success/failure details. PDF export prefers DOCX → LibreOffice PDF instead of Markdown → LaTeX PDF, because this is much more stable for Chinese/CJK notes. LaTeX remains available as a technical source export.

## SlideNote Pipeline

SlideNote is organized as a five-stage product pipeline. The low-level modules stay granular for caching, debugging, and partial refresh, but the user-facing workflow is meant to feel like a small set of presets instead of a bag of switches.

| Stage | What it does | Current artifacts / capabilities |
| --- | --- | --- |
| **1. Ingest** | Deterministically parses the source file and preserves traceable material. | `.pptx` / `.pdf` input, `.ppt` via LibreOffice conversion, `content.json`, `element_ir.json`, `source_map.json`, screenshots, extracted assets, `notes.assets/`, cache and progress files. |
| **2. Understand** | Lets local rules, OCR, vision, and LLM passes explain what the deck is about. | Page modality routing, `semantic_layout.json`, `sections.json`, `table_understanding.json`, `image_importance.json`, composite figures, figure crops, `figure_grounding.json`, optional `deck_brief.json` / `deck_brief.md`. |
| **3. Write** | Turns structured material into readable study notes. | `notes.md`, `--note-context`, `--note-style`, `--note-profile`, `--note-depth`, Lecture-Weave page notes, section weave, optional teaching enrichment, configurable language and term policy. |
| **4. Guard** | Checks whether the generated note is faithful and useful. | `coverage.json` / `coverage.md`, `content_guard.json`, required visible coverage repair, source markers, `quality_report.json`, review/exam study pack checks. |
| **5. Export** | Publishes the result and reports what happened. | `notes.toc.md`, `notes.docx`, `notes.pdf`, `notes.tex`, `review.md`, `exam.md`, `exam.json`, `exam.html`, `run_summary.json`, usage/cost reports, SlideNote Studio GUI. |

Parser IDs, image paths, cache keys, cost accounting, and export conversion stay deterministic. LLMs are used where semantic judgment helps most: section planning, page roles, figure/table meaning, lecture-style writing, review questions, and quality repair.

## User Presets

The top-level `--preset` is the product workflow selector. It maps to lower-level options such as `--note-profile`, `--note-strategy`, `--deck-brief`, and `--content-guard`, while explicit lower-level flags still override the preset.

| Preset | Use it when | Behind the scenes |
| --- | --- | --- |
| `fast` | You want a quick draft or a low-cost local-first run. | Uses `--speed-mode fast`, direct writing, local section detection, and disables extra deck brief/content guard/teaching passes unless you explicitly re-enable them. |
| `faithful` | You care most about traceability and coverage. | Uses faithful writing, Lecture-Weave, section context, Deck Brief auto, and Content Guard auto. |
| `lecture` | You want detailed teacher-style notes. | Maps to `--note-profile lecture-notes`, Lecture-Weave, section context, Deck Brief auto, Content Guard auto, and teaching enrichment auto. |

```powershell
python -m slidenote build lecture.pdf --out outputs\fast --preset fast
python -m slidenote build lecture.pdf --out outputs\faithful --preset faithful --use-llm --provider deepseek
python -m slidenote build lecture.pdf --out outputs\lecture --preset lecture --use-llm --provider deepseek
```

## Origin

SlideNote started from a very personal learning problem.

I have never been the kind of student who learns best by simply listening to lectures. Sometimes I cannot fully follow a teacher's explanation in real time, and I usually learn more efficiently by reading. Reading lets me slow down, go back, skip ahead, and control the pace of understanding by myself.

But lecture slides are not the same as readable notes. After class, reading the PPT directly often feels incomplete: the bullets are fragmented, the logic is implicit, and many important details live in diagrams, screenshots, formulas, or the teacher's spoken explanation. Manually rewriting everything into notes is possible, but it is time-consuming, hard to keep complete, and not always pleasant to revisit later.

So I wanted to build a tool that could turn course slides into structured, readable, traceable notes: not just a summary, but a faithful learning document that preserves images, keeps page references, checks coverage, and helps convert lecture materials into something I can actually study from.

That idea became SlideNote.

## Local Requirements

SlideNote does not require a local GPU. The system is layered: the local parser can run with only Python dependencies, while LLM rewriting, OCR, and vision extraction require API keys for the providers you choose.

### Required Environment

- Python `3.10` or newer.
- A virtual environment is recommended.
- Core Python dependencies are managed by `pyproject.toml`:
  - `python-pptx`: parses `.pptx` structure, text, tables, and embedded images.
  - `PyMuPDF`: parses `.pdf` files and renders PDF page screenshots.
  - `Pillow`: processes, resizes, and saves images.

### Optional But Recommended Software

| Software | Required? | Purpose |
| --- | --- | --- |
| LibreOffice | Recommended | Converts `.ppt` / `.pptx` to PDF and enables full-slide screenshots when PowerPoint is unavailable. |
| Microsoft PowerPoint | Optional | On Windows, can export PPTX full-slide screenshots through COM automation. |
| WPS Office | Manual fallback | The current CLI does not automate WPS, but you can manually export a PPT to PDF with WPS and then process the PDF with SlideNote. |

For Windows users without PowerPoint, the recommended path is to install the Windows version of LibreOffice. LibreOffice is not Linux-only. A common installation path is:

```powershell
C:\Program Files\LibreOffice\program\soffice.exe
```

The current code looks for `soffice` or `libreoffice` on your system PATH. After installation, check:

```powershell
soffice --version
```

If the command is not found, add this directory to your Windows PATH:

```text
C:\Program Files\LibreOffice\program
```

The PowerPoint route requires `pywin32`:

```powershell
python -m pip install pywin32
```

If neither LibreOffice nor PowerPoint is available:

- `.pdf` files can still be parsed.
- `.pptx` files can still yield text, tables, and embedded images, but full-slide screenshots may be missing.
- Old `.ppt` files are usually not handled directly; export them to PDF first with WPS, PowerPoint, or LibreOffice.

### API Key Configuration

LLM, OCR, and vision extraction are optional. You only need API keys for the features you enable.

Common environment variables:

```powershell
# LLM
$env:OPENAI_API_KEY="..."
$env:DEEPSEEK_API_KEY="..."
$env:DASHSCOPE_API_KEY="..."
$env:ARK_API_KEY="..."
$env:GLM_API_KEY="..."
$env:GEMINI_API_KEY="..."
$env:ANTHROPIC_API_KEY="..."

# OCR
$env:BAIDU_OCR_API_KEY="..."
$env:BAIDU_OCR_SECRET_KEY="..."
$env:MATHPIX_APP_ID="..."
$env:MATHPIX_APP_KEY="..."
$env:GOOGLE_VISION_API_KEY="..."
```

PowerShell `$env:...="..."` values only apply to the current terminal session. For regular use, configure them as Windows system environment variables or set them before each run.

### Configuration By Goal

| Goal | Requirements |
| --- | --- |
| Parse PDF / PPTX and generate a local rule-based draft | Python 3.10+ and core dependencies |
| Generate polished notes with an LLM | Install `.[llm]` and configure the chosen LLM API key |
| Process scanned PDFs or image-only slides | Configure an OCR API and run with `--ocr auto` |
| Understand diagrams, screenshots, charts, and visual layouts | Configure a vision model API and run with `--vision auto` |
| Preserve PPTX full-slide screenshots | Install LibreOffice, or install PowerPoint + `pywin32` on Windows |
| Process old `.ppt` files | Recommended: install LibreOffice; fallback: manually export to PDF |
| Export Word or LaTeX notes | Install Pandoc and run with `--export docx,latex` |
| Export PDF notes | Install Pandoc + LibreOffice and run with `--export pdf`; PDF is converted from `notes.docx` for better CJK layout |

## Installation

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev,llm]"
```

If you only need the local rule-based draft mode:

```powershell
python -m pip install -e ".[dev]"
```

## Basic Usage

Rule-based draft:

```powershell
python -m slidenote build path\to\lecture.pptx --out outputs\lecture --vision off
```

LLM rewriting:

```powershell
python -m slidenote build path\to\lecture.pdf --out outputs\lecture --use-llm --provider openai
```

Output structure:

```text
outputs/lecture/
  content.json
  page_modalities.json
  table_understanding.json
  semantic_layout.json
  element_ir.json
  image_importance.json
  sections.json
  deck_brief.md
  deck_brief.json
  content_guard.json
  notes.md
  page_notes.md
  page_notes.json
  weave_report.json
  llm_usage.json
  composite_figures.json
  figures.json
  figure_usage.json
  figure_grounding.json
  ocr.json
  ocr_usage.json
  visuals.json
  vision_usage.json
  coverage.json
  coverage.md
  source_map.json
  export_report.json
  notes.toc.md
  notes.docx
  notes.pdf
  notes.tex
  progress.json
  run_summary.json
  notes.assets/
  figures/
  images/
  screenshots/
```

By default, `notes.md` references bundled image copies under `notes.assets/`. If you move or package `notes.md` together with `notes.assets/`, images should continue to render.

`page_modalities.json` records the local page-type detector. It helps later stages choose the cheaper stable path:

- `native_text`: use extracted text directly.
- `mixed`: use extracted text plus embedded images.
- `image_only`: prefer page OCR, figure cropping, and page-level vision.
- `shape_diagram`: use extracted labels plus page screenshot cropping, because the diagram may be built from PPT shapes.
- `decorative`: low priority unless the user explicitly refreshes it.

`image_importance.json` records per-image study-value scores and reasons. Vision `auto` uses that ranking to choose the best local figure crop or embedded image before falling back to a full-page screenshot.

`table_understanding.json` records local table summaries, conclusions, and key rows. Note generation uses these fields as the primary study signal, so tables are explained by what they mean rather than by mechanically repeating every cell.

`semantic_layout.json` records page-level semantic blocks, groups, and relations. In `--semantic-layout auto`, SlideNote starts with local rules and only asks the vision model to refine dense mixed layouts, code/output pairs, cause/fix annotations, and other low-confidence pages. The file also records the winning method, confidence, reason, warnings, and any validated vision enhancement so later stages can keep related elements together.

`element_ir.json` is the normalized Page IR / Element IR consumed by prompts, coverage, source maps, and future GUI/agent workflows. Each element has a stable `element_id`, `kind`, raw `bbox`, normalized `bbox_normalized`, primary `role`, detailed `roles`, `confidence`, `reading_order`, `coverage_state`, `evidence`, and `source_ids`, so later GUI editing, local revise flows, and block-level source tracing can read one format instead of many dataclass-specific fields. The build writes an initial IR before note generation, then refreshes it after coverage so the final file includes actual covered/missing/marker-only states.

`composite_figures.json` records local detections where a diagram was assembled from multiple embedded picture pieces. SlideNote crops the whole visual region as one `composite_figure`, marks the small pieces as `composite_child`, and keeps their IDs in hidden source refs instead of inserting them separately.

`figure_grounding.json` records where each study-value figure belongs in the note: layout order, nearby text/table anchors, semantic-group anchors, grounding confidence, explanation status, and whether the figure needs manual review. `notes.md` uses this metadata to place figures near the relevant paragraph instead of dumping all images at the end.

`sections.json` records the section plan used by `--note-context section` and `lecture-weave`. In `--section-detection auto`, SlideNote uses local rules without LLM notes, and switches to LLM-assisted section detection when section-based LLM notes are enabled.

`deck_brief.json` is generated when `--deck-brief auto` runs with `--use-llm --note-strategy lecture-weave` (or when `--deck-brief force` is set). It stores the deck's topic, core questions, concept map, page roles, and cross-page links. Later page-note prompts treat it as a navigation map only: the current page remains the only source for each page explanation, and coverage checks still use original text/table/image IDs.

`content_guard.json` is generated by default when `--content-guard auto` is enabled. Without `--use-llm`, it records a local heuristic review. With `--use-llm`, SlideNote first preselects candidate tables, formulas, definitions, conditions, OCR text, visual summaries, and non-decorative figures, then asks the text model to classify page roles and element learning roles. Only high-confidence `must_explain` items count toward `required_visible_coverage` and can trigger one natural repair pass; low-confidence items remain audit information.

Extra exports are opt-in. `--export markdown-toc` writes `notes.toc.md` without Pandoc. `--export docx,latex` uses Pandoc to write `notes.docx` and `notes.tex`; `--export pdf` first builds `notes.docx` with Pandoc and then converts it to `notes.pdf` with LibreOffice; conversion status and any Pandoc errors are written to `export_report.json`.

Review/exam mode is also opt-in. It turns the finished notes into a study pack: `review.md` contains an exam-oriented checklist with importance labels, logic chains, pitfalls, figure/table quick notes, and page references; `exam.md`, `exam.json`, and `exam.html` contain self-test questions. The build also writes `section_study_pack.json`, `exam_review_pack.json`, `final_exam.md`, `final_exam.answers.md`, and `wrong_answer_review_prompt.md`. `exam.html` can generate a wrong-answer review prompt after grading, so learners can continue from "I got this wrong" to "what concept did I miss?". Local mode is deterministic and does not call an API:

```powershell
python -m slidenote build path\to\lecture.pdf --out outputs\lecture --vision off --review-mode local --exam-mode local
```

LLM/auto mode uses the same text provider settings as note generation and produces stronger questions:

```powershell
$env:DEEPSEEK_API_KEY="..."
python -m slidenote build path\to\lecture.pdf --out outputs\lecture --use-llm --provider deepseek --review-mode auto --exam-mode auto --exam-question-count 20
```

Study-pack questions are checked locally for question quality. `quality_report.json` includes `question_quality_score`, choice-distractor quality, explanation/pitfall coverage, source-ref coverage, question type mix, and whether figure/table questions keep images near the relevant question.

## Environment Check

If you are not sure what your machine is missing, run:

```powershell
python -m slidenote doctor
```

It checks:

- Python version.
- Core dependencies: PyMuPDF, python-pptx, Pillow.
- Optional dependencies: OpenAI SDK, pywin32.
- External tools: LibreOffice / `soffice`, Pandoc.
- Common LLM/OCR API key environment variables.
- Per-check impact, fix suggestions, and GUI-readable readiness flags.

You can also write the report as JSON:

```powershell
python -m slidenote doctor --json doctor.json
```

## Speed, Progress, And Partial Refresh

For the complete configuration reference, see [CONFIG.zh-CN.md](CONFIG.zh-CN.md).

Large decks can take time, especially with OCR, vision extraction, and LLM rewriting enabled. SlideNote now writes:

```text
progress.json      # Current or most recent run progress
run_summary.json   # Final run overview
```

The CLI also prints live stage progress. To suppress terminal progress while still writing `progress.json`:

```powershell
python -m slidenote build lecture.pdf --out outputs\lecture --quiet
```

Speed modes do not enable OCR or LLM by themselves. Vision is `auto` by the quality-first default and can be disabled with `--vision off`. Speed modes fill unset limits:

```powershell
--speed-mode fast      # Fewer OCR/vision targets and smaller output budgets
--speed-mode balanced  # Cost/time tradeoff
--speed-mode quality   # Default: higher image resolution and output budgets
--speed-mode debug     # Small target counts for debugging
```

Example:

```powershell
python -m slidenote build lecture.pdf `
  --out outputs\lecture-fast `
  --speed-mode fast `
  --vision auto `
  --vision-provider qwen `
  --use-llm `
  --provider deepseek
```

OCR, vision, figure crop, and LLM note contexts can run concurrently. For no-loss acceleration on large decks, keep the quality stages enabled and raise API concurrency explicitly:

```powershell
python -m slidenote build lecture.pdf `
  --out outputs\lecture `
  --use-llm `
  --speed-mode quality `
  --concurrency 3 `
  --global-cache-dir .slidenote-cache
```

`--concurrency` is the fallback for all API classes. You can tune them independently with `--llm-concurrency`, `--vision-concurrency`, `--ocr-concurrency`, and `--figure-concurrency`. Start with `3` for large files; if the provider rate-limits, use `2` instead of disabling quality stages.

To reuse cache across different output directories, set a global cache root:

```powershell
python -m slidenote build lecture.pdf `
  --out outputs\lecture-v2 `
  --global-cache-dir .slidenote-cache `
  --use-llm `
  --provider deepseek
```

To force selected slides to bypass local cache while other slides still reuse cache:

```powershell
--refresh-pages 3,5-8
```

Note: `--refresh-pages` currently means "bypass local cache for these slides", not "only output these slides".

## Note Rendering Options

The default output is a detailed lecture-style study note: it is organized by concepts instead of slide-by-slide translation, while keeping depth for definitions, formulas, examples, conditions, and figure/table conclusions. Source element IDs are hidden from the visible body, and images without OCR/vision summaries are inserted without noisy "image not parsed" explanations:

```powershell
--preset auto             # Default: keep the explicit lower-level options below
--note-style article       # Default: organize as study notes, not a summary
--note-profile auto        # Default: keep current article + lecture-weave behavior
--source-display hidden    # Default: store source refs in HTML comments and source_map.json
--asset-mode bundle        # Default: copy images into notes.assets/
--note-context section     # Default: weave notes by section
--note-strategy lecture-weave  # Default: explain each page, then weave sections
--note-depth detailed      # Default: detailed lecture-note depth
--deck-brief auto          # Default: build a global map before Lecture-Weave only
--content-guard auto       # Default: protect high-confidence required learning content
--note-language zh         # Default: write Simplified Chinese notes
--term-policy bilingual    # Default: preserve key English academic terms in Chinese notes
```

Use `--preset fast|faithful|lecture` for the normal product workflows. Use the lower-level options in this section when you want to tune or override a preset. `--preset` is the workflow bundle; `--note-profile` only controls the writing route inside the Write stage.

`lecture-weave` is the default LLM note strategy. This mode is more expensive, but it better matches the "explain this slide" workflow: first SlideNote can build a Deck Brief for global navigation, then each page is explained in detail, and finally those page notes are woven into coherent sections. The Deck Brief is explicitly guarded so it cannot replace current-page evidence or make page explanations shorter.

For quality-first teacher-style notes, use `lecture-notes`. This keeps coverage as the final QA layer, but asks the writer to reconstruct the material as a teachable section: core question, background intuition, detailed explanation, figure/formula interpretation, pitfalls, summary, and self-test questions.

```powershell
python -m slidenote build lecture.pdf `
  --out outputs\lecture_notes `
  --use-llm `
  --provider deepseek `
  --preset lecture `
  --max-output-tokens 12000
```

`--preset lecture` maps to `--note-profile lecture-notes`, section-context Lecture-Weave, Deck Brief auto, Content Guard auto, and teaching enrichment auto. `lecture-notes` automatically uses `--note-depth very-detailed` unless you explicitly choose another depth. The build writes `quality_report.json` for local checks such as explanation depth, figure integration, self-test/pitfall presence, and mechanical page-listing risk.

`--content-guard auto` is on by default. It prevents the model from treating prompts as a compiler by giving the note prompt explicit `learning_items` and then checking whether required items appear in visible prose, not only hidden source markers. Use `--content-guard off` when you want the older behavior or need to minimize extra LLM calls.

Language controls are independent of the slide language. For English courseware and Chinese notes, use the default `--note-language zh --term-policy bilingual`; key terms are prompted as `中文译名（English term/acronym）` on first mention. For English notes, use `--note-language en`. Use `--term-policy preserve` when you want the source terminology kept as much as possible, or `--term-policy translate` when you prefer translated terms where safe.

```powershell
python -m slidenote build lecture.pdf `
  --out outputs\lecture `
  --use-llm `
  --provider deepseek `
  --weave-dedup soft
```

`lecture-weave` also writes `deck_brief.json`, `deck_brief.md`, `page_notes.json`, `page_notes.md`, and `weave_report.json`. When teaching enrichment runs, it also writes `teaching_enrichment.json`. These are intermediate/debug artifacts; `notes.md` remains the final readable note. Every build writes `quality_report.json` as a local learning-quality check.

To show compact page references in the note body:

```powershell
--source-display footnote
```

For strict debugging, use page context and inline source references:

```powershell
--note-context page --source-display inline --note-style faithful
```

Full-page screenshots are now a fallback by default. If a page already has an embedded image or a local figure crop, `notes.md` does not insert the full-page screenshot unless you opt back into it:

```powershell
--screenshot-policy always
```

## Image Filtering And Source Map

PDF/PPT files often contain logos, tiny icons, background fragments, and decorative image resources. SlideNote keeps the raw files, but marks likely decorative images in `content.json`:

```json
{
  "role": "decorative",
  "ignored": true,
  "ignore_reason": "tiny_area"
}
```

Ignored images are skipped by default in notes, coverage checks, OCR fallback, and standalone vision targets. Full-page screenshots are still preserved in `screenshots/` as the visual fallback.

## Local Figure Cropping

Some lecture materials do not store diagrams as independent image objects. A page may be a scanned image, or a diagram may be made from PowerPoint shapes, arrows, text boxes, or many small picture objects. SlideNote first runs a local composite-figure detector for picture-piece diagrams, then can ask a vision model to locate other meaningful local figure regions on the full-page screenshot:

```powershell
--composite-figures auto # Default: crop clustered embedded picture pieces as one figure
--composite-figures off  # Disable local composite-figure detection
--figure-crop auto     # Default: only calls the vision model when --vision is enabled
--figure-crop vision   # Force bbox detection even if --vision is off
--figure-crop off      # Disable local figure cropping
```

Outputs:

```text
figures/
composite_figures.json
figures.json
figure_usage.json
```

Default limits:

```powershell
--figure-max-targets 80
--figure-max-crops-per-page 3
--figure-min-confidence 0.45
--figure-min-area 40000
--figure-cache on
```

Figure cropping is best-effort. The model returns bounding boxes, and SlideNote validates, filters, deduplicates, and crops them locally. If no reliable local figure is found, notes fall back to the full-page screenshot.

When `semantic_layout.json` is available, figure cropping also sees the page's semantic groups. That makes code snippets plus output panes, boxed explanations, and arrow-annotated teaching scenes more likely to be cropped as one complete learning unit instead of as isolated fragments.

## Semantic Layout

Before figure grounding and note writing, SlideNote groups related text, tables, images, and diagram fragments into page-level semantic blocks and relations:

```powershell
--semantic-layout auto   # Default: local rules first, then vision on dense/mixed/low-confidence pages
--semantic-layout local  # Local rules only; no vision call
--semantic-layout vision # Force multimodal enhancement on candidate pages
```

`auto` favors note quality. It reuses the normal vision settings (`--vision-provider`, `--vision-model`, `--vision-cache`, `--vision-cache-dir`, `--vision-concurrency`) and only spends a vision call on pages where local layout is likely too weak: code-plus-output examples, mixed diagram/text teaching pages, causal annotations, and other visually dense layouts. With `--vision off` or `--semantic-layout local`, SlideNote still writes the local layout without any API call.

Vision responses may only reference existing text/table/image element IDs. Invalid references are dropped, warnings stay in `semantic_layout.json`, and the local layout remains the fallback so generation can continue safely.

## Figure Grounding

After OCR/vision and before note writing, SlideNote anchors non-decorative figures to nearby text or table elements. The default is local and deterministic:

```powershell
--figure-grounding auto   # Default: local layout anchoring, reusing existing OCR/vision summaries
--figure-placement inline # Default: insert figures near their anchored concept
--figure-audit local      # Report missing explanations or low-confidence anchors
```

Use `--figure-grounding vision` when you want image explanations even if `--vision off`; this sends the page screenshot, element boxes, semantic layout groups, and OCR/vision summaries through the normal vision extraction path for important visual targets. The result can add `anchor_element_ids`, `anchor_group_id`, `figure_explanation`, `grounding_confidence`, and `anchor_reason`, but low-confidence or invalid outputs fall back to the local anchor instead of blocking generation. Coverage reports now include a figure section showing which images were inserted, where they were anchored, and which ones need review.

`source_map.json` records the mapping between note blocks and source elements:

```text
note block -> PPT/PDF page -> text/table/image element id
```

By default, visible notes use hidden comments such as `<!-- slidenote-source: p4:s4_t1,s4_t2 -->`, while `source_map.json` keeps the full mapping. This keeps reading clean without losing coverage checks or GUI traceability.

## LLM Providers

OpenAI-compatible providers use the OpenAI SDK after installing `.[llm]`. Gemini and Claude use native REST calls and do not require extra SDKs.

The note-writing step is text-first by default. It does not automatically send image bytes to the note model. For non-vision models such as DeepSeek, SlideNote passes text blocks, tables, image paths, element IDs, and any existing OCR/vision summaries. Image understanding is handled as a separate vision step, so a vision-capable model can create `visual_summary` fields that a cheaper text model can later reuse.

| Provider | Usage | Default Model | API Key Env Vars | Base URL |
| --- | --- | --- | --- | --- |
| ChatGPT/OpenAI | `--provider openai` | `gpt-4.1-mini` | `OPENAI_API_KEY` | OpenAI SDK default |
| DeepSeek | `--provider deepseek` | `deepseek-v4-flash` | `DEEPSEEK_API_KEY` | `https://api.deepseek.com` |
| Qwen | `--provider qwen` | text: `qwen-plus`; vision: `qwen-vl-plus` | `QWEN_API_KEY` or `DASHSCOPE_API_KEY` | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| Doubao / Volcengine Ark | `--provider doubao` | pass `--model`; vision needs `--vision-model` or `ARK_VISION_MODEL` | `DOUBAO_API_KEY` / `ARK_API_KEY` / `VOLCENGINE_API_KEY` | `https://ark.cn-beijing.volces.com/api/v3` |
| GLM / Zhipu | `--provider glm` | `glm-5.1` | `GLM_API_KEY` / `ZAI_API_KEY` / `ZHIPUAI_API_KEY` | `https://open.bigmodel.cn/api/paas/v4/` |
| Gemini | `--provider gemini` | `gemini-3-flash-preview` | `GEMINI_API_KEY` or `GOOGLE_API_KEY` | `https://generativelanguage.googleapis.com/v1beta` |
| Claude | `--provider claude` | `claude-sonnet-4-20250514` | `ANTHROPIC_API_KEY` or `CLAUDE_API_KEY` | `https://api.anthropic.com` |

Examples:

```powershell
$env:DEEPSEEK_API_KEY="..."
python -m slidenote build lecture.pptx --out outputs\lecture --use-llm --provider deepseek
```

```powershell
$env:DASHSCOPE_API_KEY="..."
python -m slidenote build lecture.pptx --out outputs\lecture --use-llm --provider qwen --model qwen-plus
```

```powershell
$env:ARK_API_KEY="..."
python -m slidenote build lecture.pptx --out outputs\lecture --use-llm --provider doubao --model ep-xxxxxxxx
```

Common generation controls:

```powershell
python -m slidenote build lecture.pptx `
  --out outputs\lecture `
  --use-llm `
  --provider glm `
  --model glm-5.1 `
  --max-output-tokens 6000 `
  --temperature 0.2 `
  --cache on
```

For proxy gateways, private gateways, or different regions, override the base URL:

```powershell
python -m slidenote build lecture.pptx --use-llm --provider qwen --base-url https://dashscope-intl.aliyuncs.com/compatible-mode/v1
```

You can also override model and base URL with environment variables:

```powershell
$env:SLIDENOTE_MODEL="qwen-plus"
$env:SLIDENOTE_BASE_URL="https://dashscope.aliyuncs.com/compatible-mode/v1"
```

## Dedicated OCR

SlideNote now separates OCR from visual understanding.

```text
OCR = read text from an image
Vision = explain diagrams, layout, trends, flows, and visual relationships
```

Dedicated OCR is useful for scanned PDFs, image-only PPT slides, screenshots, and scanned textbook pages. It runs before vision extraction and note generation, then writes recognized text back into `content.json`:

```json
{
  "page_ocr_text": "...",
  "page_ocr_status": "parsed",
  "images": [
    {
      "id": "s12_img1",
      "ocr_text": "...",
      "ocr_status": "parsed"
    }
  ]
}
```

OCR is off by default:

```powershell
--ocr off
```

Recommended Chinese OCR setup with Baidu OCR:

```powershell
$env:BAIDU_OCR_API_KEY="..."
$env:BAIDU_OCR_SECRET_KEY="..."

python -m slidenote build lecture.pdf `
  --out outputs\lecture `
  --ocr auto `
  --ocr-provider baidu
```

`--ocr auto` does not OCR every page. It first uses local text extraction. Only pages with too little extracted text, or pages that look like scanned/image-only pages, are sent to OCR.

Supported OCR providers:

| Provider | Usage | Required credentials |
| --- | --- | --- |
| Baidu OCR | `--ocr-provider baidu` | `BAIDU_OCR_API_KEY` + `BAIDU_OCR_SECRET_KEY` |
| Mathpix | `--ocr-provider mathpix` | `MATHPIX_APP_ID` + `MATHPIX_APP_KEY` |
| Google Vision OCR | `--ocr-provider google` | `GOOGLE_VISION_API_KEY` or `GOOGLE_API_KEY` |

Examples:

```powershell
$env:MATHPIX_APP_ID="..."
$env:MATHPIX_APP_KEY="..."
python -m slidenote build math_notes.pdf --out outputs\math --ocr auto --ocr-provider mathpix
```

```powershell
$env:GOOGLE_VISION_API_KEY="..."
python -m slidenote build lecture.pdf --out outputs\lecture --ocr auto --ocr-provider google
```

OCR controls:

```powershell
--ocr auto                 # OCR only pages with little extracted text
--ocr all                  # OCR every page screenshot when possible
--ocr-max-targets 120
--ocr-min-text-chars 80
--ocr-max-edge 1800
--ocr-language CHN_ENG
--ocr-cache on
```

OCR outputs:

```text
ocr.json
ocr_usage.json
```

`ocr_usage.json` records selected targets, cache hits, API calls, and recognized character counts. OCR results are cached separately from vision summaries, so changing the note model or vision model does not force OCR to run again.

## Vision Extraction

Many lecture slides are image-driven: diagrams, screenshots, formula images, charts, flowcharts, and layout cues may carry the real teaching content. SlideNote can run a separate vision extraction step before note generation. The vision step writes OCR text and visual summaries back into `content.json` and also outputs:

```text
visuals.json
vision_usage.json
```

Vision extraction is `auto` by default because SlideNote now favors note quality over token savings. The default vision provider is Qwen, so image-aware runs need `DASHSCOPE_API_KEY` or `QWEN_API_KEY` unless you choose another `--vision-provider`. Disable vision when you only want local parsing or text-only LLM rewriting:

```powershell
--vision off
```

Recommended China-friendly setup: use the default Qwen-VL vision path and DeepSeek for text rewriting.

```powershell
$env:DASHSCOPE_API_KEY="..."
$env:DEEPSEEK_API_KEY="..."

python -m slidenote build lecture.pptx `
  --out outputs\lecture `
  --vision auto `
  --vision-provider qwen `
  --use-llm `
  --provider deepseek
```

This does not create isolated captions only. The vision prompt includes the current slide title, text blocks, and table preview, so the generated `visual_summary` can describe how the image relates to the surrounding slide text. The final note-writing prompt also asks the text model to merge `visual_summary` with related text/table elements into coherent knowledge paragraphs.

Doubao / Volcengine Ark vision is also supported, but you usually need to create a vision model endpoint in Ark and pass the endpoint/model ID via `--vision-model` or `ARK_VISION_MODEL`:

```powershell
$env:ARK_API_KEY="..."
$env:ARK_VISION_MODEL="ep-xxxxxxxx"
$env:DEEPSEEK_API_KEY="..."
python -m slidenote build lecture.pptx --out outputs\lecture --vision auto --vision-provider doubao --use-llm --provider deepseek
```

Selection modes:

```powershell
--vision auto   # Recommended: parse high-value screenshots/images only
--vision all    # Parse every page screenshot when possible; highest cost
--vision off    # Disable vision extraction
```

By default, `auto` prioritizes local figure crops when they exist, then large embedded images, and only falls back to full-page screenshots when no better local visual target is available. This keeps visual summaries focused on the actual diagram instead of the entire slide.

Cost controls:

```powershell
--vision-max-targets 80
--vision-min-area 120000
--vision-max-edge 1400
--vision-detail low
--vision-cache on
```

Conservative low-cost example:

```powershell
python -m slidenote build lecture.pptx `
  --out outputs\lecture `
  --vision auto `
  --vision-provider qwen `
  --vision-max-targets 30 `
  --vision-max-edge 1000 `
  --use-llm `
  --provider deepseek
```

If the deck is highly image-driven and quality matters more than cost:

```powershell
python -m slidenote build lecture.pptx --out outputs\lecture --vision all --vision-provider openai --use-llm --provider openai
```

Full vision parsing is not recommended as the default. A better workflow is: run `auto`, inspect `vision_usage.json` and `coverage.md`, then selectively refresh low-quality or missing pages.

Vision results are written into page/image fields:

```json
{
  "page_ocr_text": "...",
  "page_visual_summary": "...",
  "images": [
    {
      "id": "s12_img1",
      "ocr_text": "...",
      "visual_summary": "..."
    }
  ]
}
```

Text-only models such as DeepSeek can then use these textualized vision results without seeing the image pixels directly.

## LLM Cache And Usage Reports

LLM rewriting uses local caching by default. Each note context cache key is based on:

```text
structured note context + prompt version + note strategy + provider + model + base_url + temperature + max-output-tokens + figure/screenshot rendering options
```

If the same context and parameters are generated again, SlideNote reuses the local cache instead of calling the model. Cache hit metadata is not inserted into the note body; it is written to `llm_usage.json` for GUI and debugging use.

In `lecture-weave` mode, page-note caches and weave caches are separate. Refreshing one slide with `--refresh-pages 12` reruns that slide's page explanation and the weave context that contains it, while unrelated page explanations can still hit cache.

Deck Brief uses the same LLM cache directory with `generation_stage="deck_brief"`, so an unchanged deck and section plan can reuse the global map without calling the model again.

Cache modes:

```powershell
--cache on       # Default: read/write local cache
--cache refresh  # Ignore old cache, call the model, and overwrite cache
--cache off      # Disable local cache
```

Custom cache directory:

```powershell
python -m slidenote build lecture.pptx --use-llm --provider deepseek --cache-dir .slidenote-cache\llm
```

`llm_usage.json` records:

- Per-context `cache_status`: `local_hit`, `miss`, `refresh`, or `disabled`
- Per-context `cache_key` and `cache_file`
- Actual LLM call count and cache hit count
- `deck_brief`, `page_note_calls`, and `weave_calls` when `--note-strategy lecture-weave` is enabled
- Provider-reported input/output/total tokens
- Provider-side cached input tokens, when returned by the API

## Design Principle

SlideNote deliberately avoids this shortcut:

```text
PPT -> LLM -> Summary
```

Instead, it follows:

```text
PPT/PDF -> structured extraction -> source inventory -> note generation -> coverage check -> export
```

The local rule-based draft is only a baseline for debugging extraction and coverage. Production notes should use `--use-llm`, while coverage checks still rely on element IDs so the model cannot silently summarize away details.

Internally, the build is being organized around explicit pipeline stages with named dependencies and artifacts. `run_summary.json` includes the registered artifact map, while `element_ir.json` is the shared contract for prompt payloads, coverage, `source_map.json`, and future agent-style workflows. The IR layer is split into a build context, standard field normalization, and source-map projection, keeping the CLI behavior stable while making GUI features and partial-revise work less brittle.

## License

SlideNote uses a dual-license structure:

- Source code is licensed under the GNU Affero General Public License v3.0 or later (`AGPL-3.0-or-later`). See [LICENSE](LICENSE).
- Documentation and example educational materials are licensed under Creative Commons Attribution 4.0 International (`CC BY 4.0`). See [LICENSES/CC-BY-4.0.txt](LICENSES/CC-BY-4.0.txt).

The SlideNote name, logo, and other brand assets are not licensed for standalone reuse. See [NOTICE](NOTICE) for the exact scope.

## Acknowledgements

- SlideNote's optional review/exam study-pack workflow was conceptually inspired by [WUBING2023/ExamPass-Assistant](https://github.com/WUBING2023/ExamPass-Assistant) and the extended [MIKUZ12/ExamPass-Assistant](https://github.com/MIKUZ12/ExamPass-Assistant) fork. SlideNote does not reuse their code, templates, prompts, or assets.
- GUI development contributions from [hongzuoj-pixel](https://github.com/hongzuoj-pixel).
- Testing contributions from [MOm0-000](https://github.com/MOm0-000).
- SlideNote's parser-adapter and document-IR roadmap is informed by prior art such as [Microsoft MarkItDown](https://github.com/microsoft/markitdown), [Docling](https://github.com/docling-project/docling), [Marker](https://github.com/datalab-to/marker), [MinerU](https://github.com/opendatalab/MinerU), and [Unstructured](https://github.com/Unstructured-IO/unstructured).
- SlideNote's future retrieval, source tracing, and post-generation QA direction is informed by systems such as [RAGFlow](https://github.com/infiniflow/ragflow). These projects are references and inspirations, not bundled dependencies unless explicitly listed elsewhere.

## References

- [OpenAI Chat Completions API](https://platform.openai.com/docs/api-reference/chat/create)
- [OpenAI Images and vision](https://developers.openai.com/api/docs/guides/images-vision)
- [DeepSeek API](https://api-docs.deepseek.com/)
- [Alibaba Cloud Model Studio OpenAI-compatible API](https://help.aliyun.com/zh/model-studio/compatibility-of-openai-with-dashscope)
- [Volcengine Ark OpenAI SDK compatibility](https://www.volcengine.com/docs/82379/1330626)
- [Zhipu GLM OpenAI compatibility](https://docs.bigmodel.cn/cn/guide/develop/openai/introduction)
- [Baidu OCR API](https://ai.baidu.com/ai-doc/REFERENCE/4kru2vqdg)
- [Mathpix OCR API](https://docs.mathpix.com/reference/post-v3-text)
- [Google Cloud Vision OCR](https://cloud.google.com/vision/docs/ocr)
- [Gemini generateContent API](https://ai.google.dev/gemini-api/docs/text-generation)
- [Gemini image understanding](https://ai.google.dev/gemini-api/docs/image-understanding)
- [Claude Messages API](https://docs.anthropic.com/en/api/messages)
- [Claude Vision](https://platform.claude.com/docs/en/build-with-claude/vision)
