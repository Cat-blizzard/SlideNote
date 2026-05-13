# SlideNote

> English README. 中文版说明: [README.zh-CN.md](README.zh-CN.md). Future roadmap: [ROADMAP.zh-CN.md](ROADMAP.zh-CN.md)

SlideNote is a coverage-aware course note generator MVP. It ingests lecture PPTX/PPT/PDF files, converts them into page-level structured JSON, then generates Markdown notes with source page references, preserved images, vision summaries, LLM usage metadata, and coverage checks.

## Features

- Supports `.pptx` and `.pdf`; `.ppt` is handled by attempting a LibreOffice conversion to PDF.
- Extracts titles, text blocks, tables, embedded images, and slide/page screenshots.
- Produces `content.json` as the source inventory.
- Produces `notes.md` with source page numbers and element IDs.
- Produces `coverage.json` / `coverage.md` to flag elements that may be missing from the notes.
- Optional vision extraction writes OCR text and visual summaries back into the structured content.
- Optional LLM generation supports OpenAI/ChatGPT, DeepSeek, Qwen, Doubao/Volcengine Ark, GLM, Gemini, and Claude.
- Local caching and usage reports make token cost visible and reusable by a future GUI.

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
python -m slidenote build path\to\lecture.pptx --out outputs\lecture
```

LLM rewriting:

```powershell
python -m slidenote build path\to\lecture.pdf --out outputs\lecture --use-llm --provider openai
```

Output structure:

```text
outputs/lecture/
  content.json
  notes.md
  llm_usage.json
  ocr.json
  ocr_usage.json
  visuals.json
  vision_usage.json
  coverage.json
  coverage.md
  source_map.json
  progress.json
  run_summary.json
  images/
  screenshots/
```

## Environment Check

If you are not sure what your machine is missing, run:

```powershell
python -m slidenote doctor
```

It checks:

- Python version.
- Core dependencies: PyMuPDF, python-pptx, Pillow.
- Optional dependencies: OpenAI SDK, pywin32.
- External tools: LibreOffice / `soffice`.
- Common LLM/OCR API key environment variables.

You can also write the report as JSON:

```powershell
python -m slidenote doctor --json doctor.json
```

## Speed, Progress, And Partial Refresh

Large decks can take time, especially with OCR, vision extraction, and LLM rewriting enabled. SlideNote now writes:

```text
progress.json      # Current or most recent run progress
run_summary.json   # Final run overview
```

The CLI also prints live stage progress. To suppress terminal progress while still writing `progress.json`:

```powershell
python -m slidenote build lecture.pdf --out outputs\lecture --quiet
```

Speed modes do not enable OCR, vision, or LLM by themselves. They only fill unset limits:

```powershell
--speed-mode fast      # Fewer OCR/vision targets and smaller output budgets
--speed-mode balanced  # Default tradeoff
--speed-mode quality   # Higher image resolution and output budgets
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

OCR, vision, and page-level LLM calls can run concurrently. Higher concurrency can be faster, but may hit provider rate limits. Start with `2` or `3`:

```powershell
--concurrency 3
```

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

`source_map.json` records the mapping between note blocks and source elements:

```text
note block -> PPT/PDF page -> text/table/image element id
```

This lets future GUI and exporters choose strict, compact, or hidden source display without losing traceability.

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

Vision extraction is off by default because it uses extra multimodal tokens:

```powershell
--vision off
```

Recommended China-friendly setup: use Qwen-VL for vision and DeepSeek for text rewriting.

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

By default, `auto` prioritizes full-page screenshots. A page screenshot often captures diagrams, arrows, grouped shapes, embedded images, and spatial relationships in one call. If no page screenshot is available, SlideNote falls back to large embedded images by area.

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

LLM rewriting uses local caching by default. Each page cache key is based on:

```text
structured page content + prompt version + provider + model + base_url + temperature + max-output-tokens
```

If the same page and parameters are generated again, SlideNote reuses the local cache instead of calling the model. Cache hit metadata is not inserted into the note body; it is written to `llm_usage.json` for GUI and debugging use.

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

- Per-page `cache_status`: `local_hit`, `miss`, `refresh`, or `disabled`
- Per-page `cache_key` and `cache_file`
- Actual LLM call count and cache hit count
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
