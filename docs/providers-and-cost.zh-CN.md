# Provider、OCR、Vision 与成本背景

`slidenote build` 已经不再暴露成本、并发、缓存和模型细节参数。普通用户只需要选择 `lecture` / `local`、provider、vision 和导出格式；其余走强质量默认值。

## 基础依赖

- Python `3.10+`。
- 简单入口：在仓库根目录运行 `.\install.ps1`，然后运行 `.\run_gui.ps1`。
- 本地解析：`python -m pip install -e ".[dev]"`。
- LLM / GUI：`python -m pip install -e ".[dev,llm,gui]"`。

可选外部软件：

| 软件 | 用途 |
| --- | --- |
| LibreOffice | PPT/PPTX 转 PDF、整页截图、PDF 导出兜底。 |
| Microsoft PowerPoint + `pywin32` | Windows 上导出 PPTX 整页截图。 |
| Pandoc | `notes.docx`、`notes.tex` 导出。 |

## LLM Provider

CLI 只保留 `--provider`，API key、模型名和 base URL 使用环境变量。

| Provider | CLI | 默认模型 | API key 环境变量 |
| --- | --- | --- | --- |
| DeepSeek | `--provider deepseek` | `deepseek-v4-flash` | `DEEPSEEK_API_KEY` |
| OpenAI | `--provider openai` | `gpt-4.1-mini` | `OPENAI_API_KEY` |
| Qwen | `--provider qwen` | `qwen-plus` | `QWEN_API_KEY` / `DASHSCOPE_API_KEY` |
| Doubao / Ark | `--provider doubao` | 需环境变量指定模型 | `DOUBAO_API_KEY` / `ARK_API_KEY` / `VOLCENGINE_API_KEY` |
| GLM | `--provider glm` | `glm-5.1` | `GLM_API_KEY` / `ZAI_API_KEY` / `ZHIPUAI_API_KEY` |
| Gemini | `--provider gemini` | `gemini-3-flash-preview` | `GEMINI_API_KEY` / `GOOGLE_API_KEY` |
| Claude | `--provider claude` | `claude-sonnet-4-20250514` | `ANTHROPIC_API_KEY` / `CLAUDE_API_KEY` |

示例：

```powershell
$env:DEEPSEEK_API_KEY="..."
$env:DASHSCOPE_API_KEY="..."
python -m slidenote build lecture.pptx --out outputs\lecture --provider deepseek --export markdown-zip
```

模型名和 base URL 可通过环境变量覆盖，例如：

```powershell
$env:SLIDENOTE_MODEL="qwen-plus"
$env:SLIDENOTE_BASE_URL="https://dashscope.aliyuncs.com/compatible-mode/v1"
```

## OCR

OCR 负责读出图片或扫描页里的文字。`lecture` preset 内部使用 OCR auto；`local` preset 不调用 OCR API。

默认 OCR provider 是百度 OCR：

```powershell
$env:BAIDU_OCR_API_KEY="..."
$env:BAIDU_OCR_SECRET_KEY="..."
```

相关输出只在实际运行 OCR 时出现：

```text
ocr.json
ocr_usage.json
```

## Vision

Vision 负责解释图、流程、趋势、布局和视觉关系。公开参数只保留：

```powershell
--vision auto
--vision off
```

`auto` 是默认质量路线，视觉 provider 固定使用 Qwen/DashScope 环境变量：

```powershell
$env:DASHSCOPE_API_KEY="..."
```

相关输出只在实际运行 Vision 时出现：

```text
visuals.json
vision_usage.json
figures.json
figure_usage.json
figure_grounding.json
```

## 缓存、并发和成本

这些现在是内部强默认，不再作为普通 CLI 参数暴露：

- 缓存默认开启并写入输出目录 `.cache/`。
- API 并发使用保守内部默认值。
- 视觉/OCR/figure target 上限使用质量优先默认值。

构建完成后仍会保留统计文件，供 GUI 和后续诊断使用：

```text
llm_usage.json
vision_usage.json
ocr_usage.json
figure_usage.json
run_summary.json
cost_report.json
cost_report.md
cost_dashboard.html
```

如果你需要重新开放成本或并发调参，请先确认默认 `lecture` 路线的真实瓶颈，再把它作为开发者配置处理，而不是直接恢复到普通用户界面。
