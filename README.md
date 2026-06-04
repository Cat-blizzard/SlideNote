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
  <em>Not just a slide summarizer, but a faithful study-document pipeline.</em>
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
  <a href="docs/index.zh-CN.md">Docs</a> |
  <a href="#experimental-claude-code-backend">Claude Backend</a> |
  <a href="CONFIG.zh-CN.md">Config</a> |
  <a href="ROADMAP.zh-CN.md">Roadmap</a>
</p>

---

## Contents

- [Quick Start](#quick-start)
- [Optional GUI](#optional-gui)
- [Experimental Claude Backend](#experimental-claude-code-backend)
- [Pipeline And Presets](#slidenote-pipeline)
- [Origin](#origin)
- [Setup](#setup)
- [Common Workflows](#common-workflows)
- [Technical Docs](#technical-docs)
- [Future Outlook](#future-outlook)
- [License And Acknowledgements](#license)

## Quick Start

```powershell
git clone https://github.com/Cat-blizzard/SlideNote.git
cd SlideNote
.\install.ps1
.\run_gui.ps1
```

The setup script creates `.venv`, installs SlideNote with GUI/LLM extras, and runs `slidenote doctor`. The GUI lets you paste API keys in the page for a single run, so you do not have to set terminal environment variables first.

Manual setup is still available:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev,llm]"
python -m slidenote doctor
```

For a text-first LLM note run:

```powershell
$env:DEEPSEEK_API_KEY="..."
python -m slidenote build path\to\lecture.pdf --out outputs\lecture --use-llm --provider deepseek --vision off --figure-crop off
```

For higher-quality notes with visual understanding:

```powershell
$env:DASHSCOPE_API_KEY="..."
$env:DEEPSEEK_API_KEY="..."
python -m slidenote build path\to\lecture.pdf --out outputs\lecture --preset lecture --use-llm --provider deepseek
```

Open `outputs\lecture\notes.md` after generation. Images are copied into `outputs\lecture\notes.assets\` by default.

## Optional GUI

SlideNote Studio is a Streamlit interface around the same CLI pipeline. It supports uploading PPT/PDF files, entering API keys in the page, selecting presets, watching progress and ETA, reviewing token/cost reports, checking page-level sources, and downloading generated results.

```powershell
.\run_gui.ps1
```

See [gui/README_GUI.md](gui/README_GUI.md) and [gui/README_GUI.zh-CN.md](gui/README_GUI.zh-CN.md) for GUI details.

## Experimental Claude Code Backend

This branch, `experiment/claude-backend`, tests a Claude Code backend beside the stable `slidenote build` pipeline. SlideNote keeps the deterministic parts: parsing, image assets, source IDs, coverage, source maps, file writes, and reports. Claude Code receives a bounded agent pack, writes section notes, and can repair them based on coverage feedback.

```powershell
python -m slidenote agent-pack lecture.pdf --out outputs\agent-pack
python -m slidenote agent-build lecture.pdf --out outputs\agent-build
python -m slidenote agent-eval lecture.pdf --out outputs\agent-eval
```

The official `claude` command must be available and authenticated locally, or passed with `--claude-command`. Claude returns JSON through stdout and does not write repository or output files directly. Details: [Claude Backend](docs/claude-backend.zh-CN.md).

## SlideNote Pipeline

SlideNote is organized as a five-stage product pipeline. Low-level modules can stay fine-grained for caching, debugging, and partial refresh; the user-facing workflow should remain simple.

```text
Ingest -> Understand -> Write -> Guard -> Export
```

| Stage | Purpose | Main artifacts |
| --- | --- | --- |
| **1. Ingest** | Parse PPT/PDF into stable, traceable structure. | `content.json`, `element_ir.json`, `source_map.json`, screenshots, assets, parser adapters |
| **2. Understand** | Decide what the courseware is teaching. | `deck_understanding.json`, `page_understanding.json`, `sections.json`, `deck_brief.json`, figure/table understanding |
| **3. Write** | Turn structured material into readable study notes. | `notes.md`, Lecture-Weave page notes, teaching enrichment |
| **4. Guard** | Check faithfulness, coverage, and study quality. | `coverage.json`, `coverage.md`, `content_guard.json`, `quality_report.json` |
| **5. Export** | Publish notes and reports. | `notes.zip`, `notes.toc.md`, `notes.docx`, `notes.pdf`, `notes.tex`, review/exam packs |

More detail: [SlideNote Pipeline](docs/pipeline.zh-CN.md).

## User Presets

Use top-level `--preset` for product workflows. It maps to lower-level options such as `--note-profile`, `--note-strategy`, `--deck-brief`, and `--content-guard`; explicit user options still override preset defaults.

| Preset | Best for | Behavior |
| --- | --- | --- |
| `fast` | Quick drafts, low cost, local-first runs. | Fewer heavy stages and shorter outputs. |
| `faithful` | Source traceability and coverage-first notes. | Lecture-Weave, section context, deck brief, content guard. |
| `lecture` | Teacher-style detailed lecture notes. | `--note-profile lecture-notes`, Lecture-Weave, deck brief, content guard, teaching enrichment. |

```powershell
python -m slidenote build lecture.pdf --out outputs\fast --preset fast
python -m slidenote build lecture.pdf --out outputs\faithful --preset faithful --use-llm --provider deepseek
python -m slidenote build lecture.pdf --out outputs\lecture --preset lecture --use-llm --provider deepseek
```

More detail: [User Presets](docs/presets.zh-CN.md).

## Origin

SlideNote started from a very personal learning problem.

I have never been the kind of student who learns best by simply listening to lectures. Sometimes I cannot fully follow a teacher's explanation in real time, and I usually learn more efficiently by reading. Reading lets me slow down, go back, skip ahead, and control the pace of understanding by myself.

But lecture slides are not the same as readable notes. After class, reading the PPT directly often feels incomplete: the bullets are fragmented, the logic is implicit, and many important details live in diagrams, screenshots, formulas, or the teacher's spoken explanation. Manually rewriting everything into notes is possible, but it is time-consuming, hard to keep complete, and not always pleasant to revisit later.

So I wanted to build a tool that could turn course slides into structured, readable, traceable notes: not just a summary, but a faithful learning document that preserves images, keeps page references, checks coverage, and helps convert lecture materials into something I can actually study from.

That idea became SlideNote.

## Setup

SlideNote does not require a local GPU. The local parser can run with only Python dependencies; LLM rewriting, OCR, and visual understanding require API keys for the providers you choose.

Minimum setup:

- Python `3.10` or newer.
- A virtual environment is recommended.
- New users can run `.\install.ps1` and then `.\run_gui.ps1`.
- `python -m pip install -e ".[dev]"` for local parsing.
- `python -m pip install -e ".[dev,llm]"` for LLM providers.

Optional software:

| Software | Purpose |
| --- | --- |
| LibreOffice | Converts `.ppt` / `.pptx` to PDF and enables full-slide screenshots when PowerPoint is unavailable. |
| Microsoft PowerPoint + `pywin32` | Windows-only PPTX screenshot export route. |
| Pandoc | Word and LaTeX export. |
| LibreOffice + Pandoc | PDF export from `notes.docx`, usually more stable for CJK layout. |

Configuration details live in [CONFIG.zh-CN.md](CONFIG.zh-CN.md) and [Providers, OCR, Vision, Cache, And Cost](docs/providers-and-cost.zh-CN.md).

## Common Workflows

Local rule-based draft:

```powershell
python -m slidenote build path\to\lecture.pptx --out outputs\lecture --vision off
```

Teacher-style lecture notes:

```powershell
python -m slidenote build path\to\lecture.pdf `
  --out outputs\lecture-notes `
  --preset lecture `
  --use-llm `
  --provider deepseek `
  --max-output-tokens 12000
```

Review and exam pack:

```powershell
python -m slidenote build path\to\lecture.pdf `
  --out outputs\lecture-review `
  --use-llm `
  --provider deepseek `
  --review-mode auto `
  --exam-mode auto `
  --exam-question-count 20
```

Faster reruns with shared cache and modest concurrency:

```powershell
python -m slidenote build path\to\lecture.pdf `
  --out outputs\lecture `
  --use-llm `
  --provider deepseek `
  --concurrency 3 `
  --global-cache-dir .slidenote-cache
```

## Technical Docs

README is intentionally kept as a landing page. Detailed behavior lives in the docs:

| Topic | Link |
| --- | --- |
| Documentation index | [docs/index.zh-CN.md](docs/index.zh-CN.md) |
| Pipeline stages | [docs/pipeline.zh-CN.md](docs/pipeline.zh-CN.md) |
| Presets | [docs/presets.zh-CN.md](docs/presets.zh-CN.md) |
| Coverage, content guard, quality report, review/exam packs | [docs/quality-and-guard.zh-CN.md](docs/quality-and-guard.zh-CN.md) |
| Element IR, source map, assets | [docs/ir-and-source-map.zh-CN.md](docs/ir-and-source-map.zh-CN.md) |
| LLM providers, OCR, vision, cache, cost | [docs/providers-and-cost.zh-CN.md](docs/providers-and-cost.zh-CN.md) |
| Experimental Claude backend | [docs/claude-backend.zh-CN.md](docs/claude-backend.zh-CN.md) |
| Roadmap design notes | [docs/roadmap/extension-notes.zh-CN.md](docs/roadmap/extension-notes.zh-CN.md) |

The main output is `notes.md`. To share Markdown notes with images, export `notes.zip`; it contains `notes.md` and the `notes.assets/` image folder. Depending on options, SlideNote can also write `content.json`, `deck_understanding.json`, `page_understanding.json`, `element_ir.json`, `source_map.json`, `coverage.md`, `quality_report.json`, `review.md`, `exam.md`, `exam.json`, `exam.html`, `notes.docx`, `notes.pdf`, and other reports.

## Future Outlook

SlideNote is built with a hopeful assumption: future AI systems will become stronger, faster, cheaper, and easier to orchestrate through mature open-source agent frameworks. If that happens, this project should not merely run the same prompts for less money. Its ceiling should rise.

Models and providers such as DeepSeek are one example of the direction that makes this exciting: better price/performance, broader access, and a more open ecosystem can make high-quality multi-pass workflows practical for ordinary study materials. When API latency drops and agent frameworks become more reliable, SlideNote can afford to run richer stages by default: deeper deck understanding, page-level visual reasoning, teacher-style section writing, teaching enrichment, coverage repair, exam generation, wrong-answer review, and source verification.

The reason this matters is that SlideNote's bottleneck is not only "can the model summarize a slide?" The harder problem is coordinating parsing, vision, writing, grounding, quality checks, and revision without losing traceability. That is why the project invests in `element_ir.json`, `source_map.json`, coverage reports, artifact registries, presets, cache keys, and review/exam packs. Those structures let SlideNote absorb future model gains without being tied to one model, provider, or agent runtime.

The long-term vision is:

> SlideNote should grow from a courseware converter into a course learning operating system.

In that version, slides, readings, personal notes, figures, formulas, quizzes, mistakes, and revisions all live in one traceable learning workflow.

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

## License

SlideNote uses a dual-license structure:

- Source code is licensed under the GNU Affero General Public License v3.0 or later (`AGPL-3.0-or-later`). See [LICENSE](LICENSE).
- Documentation and example educational materials are licensed under Creative Commons Attribution 4.0 International (`CC BY 4.0`). See [LICENSES/CC-BY-4.0.txt](LICENSES/CC-BY-4.0.txt).

The SlideNote name, logo, and other brand assets are not licensed for standalone reuse. See [NOTICE](NOTICE) for the exact scope.

## Acknowledgements

- SlideNote's optional review/exam study-pack workflow was conceptually inspired by [WUBING2023/ExamPass-Assistant](https://github.com/WUBING2023/ExamPass-Assistant) and the extended [MIKUZ12/ExamPass-Assistant](https://github.com/MIKUZ12/ExamPass-Assistant) fork. SlideNote does not reuse their code, templates, prompts, or assets.
- GUI development contributions from [hongzuoj-pixel](https://github.com/hongzuoj-pixel).
- Testing contributions from [MOm0-000](https://github.com/MOm0-000).
- The Claude-oriented agent workflow explored on the `experiment/claude-backend` branch was inspired by [koriyoshi2041](https://github.com/koriyoshi2041).
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
