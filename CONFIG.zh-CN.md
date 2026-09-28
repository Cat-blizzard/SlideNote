# SlideNote 配置指南

SlideNote 的普通用户入口是两个 preset：`lecture` 和 `local`。`build` 只公开少量常用选项；更细的图文锚定、Lecture-Weave、缓存与质量检查由流水线内部处理。

## 我该怎么跑？

正式生成讲义，使用默认 `lecture`：

```powershell
$env:DEEPSEEK_API_KEY="..."
$env:DASHSCOPE_API_KEY="..."
python -m slidenote build lecture.pdf --out outputs\lecture --provider deepseek --export markdown-zip
```

没有 API key，先确认课件能解析：

```powershell
python -m slidenote build lecture.pdf --out outputs\local --preset local --export markdown-zip
```

关闭视觉模型来写讲义（课件图片仍可能进入笔记，OCR 也可单独运行）：

```powershell
$env:DEEPSEEK_API_KEY="..."
python -m slidenote build lecture.pdf --out outputs\no-vision --provider deepseek --vision off
```

从已有笔记生成复习包：

```powershell
python -m slidenote study-pack outputs\lecture --question-count 12
```

把 PDF 教材解析成供后续检索使用的分块语料（当前尚无向量索引，也未接入笔记生成）：

```powershell
python -m slidenote textbook-index textbook.pdf --out outputs\textbook --ocr auto
```

生成 Word / PDF：

```powershell
python -m slidenote build lecture.pdf --out outputs\paper --export docx,pdf
```

分享 Markdown 给别人时，优先使用 `--export markdown-zip`。`notes.zip` 包含 `notes.md`；有图片资源时还包含 `notes.assets/`。对方解压后打开 `notes.md` 即可查看。

## Build 参数

`slidenote build` 的公开参数现在只保留这些：

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `input` | 必填 | 输入 `.pptx` / `.ppt` / `.pdf`；其他格式取决于可选外部 parser。 |
| `--out` | `outputs/slidenote` | 输出目录。 |
| `--preset` | `lecture` | `lecture` 走强质量 AI 讲义流程；`local` 不调用 API。 |
| `--provider` | `deepseek` | 文本模型 provider。支持 `deepseek`、`openai`、`qwen`、`doubao`、`glm`、`gemini`、`claude`。 |
| `--vision` | `auto` | `auto` 按需调用视觉模型；`off` 跳过视觉 API，但不删除课件图片。`local` preset 会强制关闭。 |
| `--ocr` | `auto` | `auto` 只处理低文本/扫描页；`off` 跳过 OCR；`all` 处理所有页。`local` preset 会强制关闭。 |
| `--export` | 无 | 额外导出：`markdown-zip`、`markdown-toc`、`docx`、`pdf`、`latex`、`all`。 |
| `--parser` | `auto` | 可选解析器入口，普通用户不用改。 |
| `--progress-json` | `<out>/progress.json` | GUI/自动化使用的进度文件。 |
| `--quiet` | 关闭 | 不打印实时进度，但仍写 `progress.json`。 |

## Textbook 参数

`textbook-index` 是独立教材库入口，当前不会把教材内容混入笔记生成。

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `input` | 必填 | 输入 PDF 教材。v1 只支持 `.pdf`。 |
| `--out` | `outputs/textbook` | 教材库输出目录。 |
| `--ocr` | `auto` | `auto` 只 OCR 扫描页/低文本页；`off` 只用原生文本；`all` 全页 OCR。 |
| `--quiet` | 关闭 | 不打印输出摘要。 |

## Preset

| Preset | 适合场景 | 行为 |
| --- | --- | --- |
| `lecture` | 正式学习、长期保存、需要图文理解；像老师重新讲一遍。 | 默认启用 LLM、OCR auto、Vision auto、图文锚定、Deck Brief、Content Guard、Lecture-Weave、teaching enrichment 和本地缓存。需要相应 API key。 |
| `local` | 首次预览、没有 API key、检查课件能否被读出。 | 不调用文本模型、视觉模型或 OCR API，只用本地规则生成基础 Markdown 和质量报告。 |

首次处理新课件可先用 `local` 检查解析；正式学习或分享笔记再用 `lecture`。图表较多时保留 `--vision auto`；只想关闭视觉模型时使用 `--vision off`。复习题由独立的 `study-pack` 命令生成。

## 环境变量

CLI 不再接收 `--api-key` / `--base-url` / `--model` 这类细节参数。请用环境变量配置 provider。GUI 用户可以直接在页面里填写 key，GUI 会只对当前运行注入环境变量。

| Provider | 文本 API key |
| --- | --- |
| `deepseek` | `DEEPSEEK_API_KEY` |
| `openai` | `OPENAI_API_KEY` |
| `qwen` | `QWEN_API_KEY` / `DASHSCOPE_API_KEY` |
| `doubao` | `DOUBAO_API_KEY` / `ARK_API_KEY` / `VOLCENGINE_API_KEY` |
| `glm` | `GLM_API_KEY` / `ZAI_API_KEY` / `ZHIPUAI_API_KEY` |
| `gemini` | `GEMINI_API_KEY` / `GOOGLE_API_KEY` |
| `claude` | `ANTHROPIC_API_KEY` / `CLAUDE_API_KEY` |

视觉理解默认使用 Qwen/DashScope：

```powershell
$env:DASHSCOPE_API_KEY="..."
```

扫描版 PDF 如果需要 OCR，默认使用百度 OCR 环境变量：

```powershell
$env:BAIDU_OCR_API_KEY="..."
$env:BAIDU_OCR_SECRET_KEY="..."
```

模型名和 base URL 仍可通过 provider 自己的环境变量覆盖，例如 `SLIDENOTE_MODEL`、`QWEN_MODEL`、`DEEPSEEK_MODEL`、`SLIDENOTE_BASE_URL`、`DASHSCOPE_BASE_URL`。

## 复习包

复习包已经从 `build` 参数中移出。先生成笔记，再对输出目录运行：

```powershell
python -m slidenote study-pack outputs\lecture --question-count 20
```

它会读取 `notes.md`、`content.json`、`sections.json`、`source_map.json`、`coverage.json` 等已有产物，并写出：

```text
study_pack.json
review.md
exam.md
exam.json
exam.html
section_study_pack.json
exam_review_pack.json
final_exam.md
final_exam.answers.md
wrong_answer_review_prompt.md
```

如果找到文本 provider API key，复习包会用 LLM 增强；没有 key 时会自动回退到本地规则。

## 旧参数迁移

这些旧的 `build` 参数已经删除：`--use-llm`、`--speed-mode`、`--concurrency`、各阶段 concurrency、`--cache`、`--global-cache-dir`、`--refresh-pages`、`--model`、`--api-key`、`--base-url`、`--max-output-tokens`、`--temperature`、`--ocr-*`、`--vision-provider`、`--vision-model`、`--vision-api-key`、`--figure-*`、`--note-*`、`--deck-brief`、`--content-guard`、`--section-detection`、`--semantic-layout`、`--asset-mode`、`--source-display`、`--screenshot-policy`、`--export-toc`、`--review-mode`、`--exam-mode`、`--exam-question-count`。

常见迁移方式：

| 旧写法 | 新写法 |
| --- | --- |
| `--use-llm --provider deepseek` | 默认就是 `lecture`，只保留 `--provider deepseek`。 |
| `--preset fast` / `--vision off` 本地预览 | `--preset local`。 |
| `--review-mode auto --exam-mode auto` | `slidenote study-pack <build输出目录>`。 |
| `--api-key ...` / `--vision-api-key ...` | 设置环境变量，或在 GUI 页面填写 key。 |
| `--asset-mode bundle --export markdown-zip` | 只保留 `--export markdown-zip`。 |

如果你之前依赖非常细的调参能力，建议先使用默认 `lecture` 路线跑通，再根据实际缺口考虑是否需要恢复为开发者配置，而不是重新暴露给普通用户。
