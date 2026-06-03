# Provider、OCR、Vision、缓存与成本

这份文档承接 README 中迁出的配置细节。完整 CLI 参数仍以 [CONFIG.zh-CN.md](../CONFIG.zh-CN.md) 为准。

## 基础依赖

最低运行环境：

- Python `3.10+`。
- 简单入口：在仓库根目录运行 `.\install.ps1`，然后运行 `.\run_gui.ps1`。
- `python -m pip install -e ".[dev]"`：本地解析、规则草稿、测试依赖。
- `python -m pip install -e ".[dev,llm]"`：LLM provider 支持。

可选外部软件：

| 软件 | 用途 |
| --- | --- |
| LibreOffice | PPT/PPTX 转 PDF、整页截图、PDF 导出兜底。 |
| Microsoft PowerPoint + `pywin32` | Windows 上导出 PPTX 整页截图。 |
| Pandoc | `notes.docx`、`notes.tex` 导出。 |

## LLM Provider

OpenAI-compatible provider 使用 OpenAI SDK。Gemini 和 Claude 走原生 REST。

| Provider | 用法 | 默认模型 | API Key 环境变量 | Base URL |
| --- | --- | --- | --- | --- |
| OpenAI | `--provider openai` | `gpt-4.1-mini` | `OPENAI_API_KEY` | SDK 默认 |
| DeepSeek | `--provider deepseek` | `deepseek-v4-flash` | `DEEPSEEK_API_KEY` | `https://api.deepseek.com` |
| Qwen | `--provider qwen` | 文本 `qwen-plus`，视觉 `qwen-vl-plus` | `QWEN_API_KEY` 或 `DASHSCOPE_API_KEY` | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| Doubao / Ark | `--provider doubao` | 需传 `--model` | `DOUBAO_API_KEY` / `ARK_API_KEY` / `VOLCENGINE_API_KEY` | `https://ark.cn-beijing.volces.com/api/v3` |
| GLM | `--provider glm` | `glm-5.1` | `GLM_API_KEY` / `ZAI_API_KEY` / `ZHIPUAI_API_KEY` | `https://open.bigmodel.cn/api/paas/v4/` |
| Gemini | `--provider gemini` | `gemini-3-flash-preview` | `GEMINI_API_KEY` 或 `GOOGLE_API_KEY` | `https://generativelanguage.googleapis.com/v1beta` |
| Claude | `--provider claude` | `claude-sonnet-4-20250514` | `ANTHROPIC_API_KEY` 或 `CLAUDE_API_KEY` | `https://api.anthropic.com` |

示例：

```powershell
$env:DEEPSEEK_API_KEY="..."
python -m slidenote build lecture.pptx --out outputs\lecture --use-llm --provider deepseek
```

如果使用代理、私有网关或不同地域，可以覆盖 base URL：

```powershell
python -m slidenote build lecture.pptx --use-llm --provider qwen --base-url https://dashscope-intl.aliyuncs.com/compatible-mode/v1
```

也可以用环境变量覆盖模型和 base URL：

```powershell
$env:SLIDENOTE_MODEL="qwen-plus"
$env:SLIDENOTE_BASE_URL="https://dashscope.aliyuncs.com/compatible-mode/v1"
```

## OCR

OCR 只负责读出图片里的文字，适合扫描版 PDF、图片型 PPT、截图和扫描教材页。它和视觉理解分开缓存。

```powershell
--ocr off   # 默认关闭
--ocr auto  # 只 OCR 抽取文字较少的页面
--ocr all   # 尽量 OCR 每页截图
```

已支持：

| Provider | 用法 | 凭据 |
| --- | --- | --- |
| 百度 OCR | `--ocr-provider baidu` | `BAIDU_OCR_API_KEY` + `BAIDU_OCR_SECRET_KEY` |
| Mathpix | `--ocr-provider mathpix` | `MATHPIX_APP_ID` + `MATHPIX_APP_KEY` |
| Google Vision OCR | `--ocr-provider google` | `GOOGLE_VISION_API_KEY` 或 `GOOGLE_API_KEY` |

OCR 输出：

```text
ocr.json
ocr_usage.json
```

## Vision

Vision 负责解释图、流程、趋势、布局和视觉关系。它会把 `visual_summary` 写回结构化内容，再交给文本模型写笔记。

```powershell
--vision auto   # 推荐：优先解析高价值图片/页面
--vision all    # 尽量解析每页截图，成本最高
--vision off    # 不做视觉解析
```

推荐的中低成本组合：

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

成本控制：

```powershell
--vision-max-targets 80
--vision-min-area 120000
--vision-max-edge 1400
--vision-detail low
--vision-cache on
```

建议先用 `auto`，再看 `vision_usage.json` 和 `coverage.md` 决定是否局部刷新，而不是默认 `all`。

## Figure Crop 与 Figure Grounding

`--figure-crop auto` 会在需要时裁剪局部图，让视觉模型看真正的图表，而不是整页幻灯片。

`--figure-grounding auto` 会把非装饰图片锚定到相关文字或表格附近，让图表进入正文逻辑，而不是都堆在页尾。

相关产物：

```text
figures.json
figure_usage.json
figure_grounding.json
```

## 缓存与并发

LLM、OCR、Vision、Figure Crop 都有各自缓存。默认建议保留缓存：

```powershell
--cache on
--cache refresh
--cache off
```

全局缓存目录适合跨输出目录复用：

```powershell
python -m slidenote build lecture.pdf `
  --out outputs\lecture-v2 `
  --global-cache-dir .slidenote-cache `
  --use-llm `
  --provider deepseek
```

并发建议从较小值开始：

```powershell
--concurrency 3
--llm-concurrency 3
--vision-concurrency 2
--ocr-concurrency 2
--figure-concurrency 2
```

如果遇到限流，优先降低并发，不要靠关闭质量阶段来提速。

## 输出与成本报告

常见报告：

```text
llm_usage.json
vision_usage.json
ocr_usage.json
figure_usage.json
run_summary.json
```

这些文件会记录目标页、缓存命中、实际 API 调用、token、成本估计和阶段耗时，供 GUI 和后续调参使用。
