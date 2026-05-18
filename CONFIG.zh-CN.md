# SlideNote 配置参考

> 本文档整理 `python -m slidenote build` 的主要参数、默认值和适用场景。README 负责快速上手，这里负责完整配置查阅。

## 推荐组合

质量优先，适合正式生成笔记：

```powershell
python -m slidenote build lecture.pdf `
  --out outputs\lecture `
  --use-llm `
  --provider deepseek `
  --vision-provider qwen
```

当前默认已经偏质量优先：

```text
--speed-mode quality
--vision auto
--vision-provider qwen
--note-strategy lecture-weave
--note-context section
--note-depth detailed
--deck-brief auto
--content-guard auto
--note-language zh
--term-policy bilingual
--weave-dedup soft
--page-neighborhood 1
--figure-crop auto
```

只想本地解析，不调用视觉模型：

```powershell
python -m slidenote build lecture.pdf --out outputs\lecture --vision off
```

调试覆盖率和来源映射：

```powershell
python -m slidenote build lecture.pdf `
  --out outputs\debug `
  --use-llm `
  --note-strategy direct `
  --note-context page `
  --note-style faithful `
  --source-display inline `
  --vision off
```

## 基础运行

| 参数 | 默认值 | 可选值 / 格式 | 说明 |
| --- | --- | --- | --- |
| `input` | 必填 | `.pptx` / `.ppt` / `.pdf` | 输入课程材料。 |
| `--out` | `outputs/slidenote` | 路径 | 输出目录。 |
| `--speed-mode` | `quality` | `fast` / `balanced` / `quality` / `debug` | 成本、速度和质量预设。只填充未显式设置的限额。 |
| `--concurrency` | `1` | 正整数 | OCR、Vision、Figure Crop 和 LLM 的并发 API 调用数。 |
| `--global-cache-dir` | 无 | 路径 | 多个输出目录共享缓存。 |
| `--refresh-pages` | 无 | `3,5-8` | 指定页绕过本地缓存重新生成。 |
| `--progress-json` | `<out>/progress.json` | 路径 | 进度 JSON 路径。 |
| `--quiet` | 关闭 | flag | 不打印实时进度，但仍写 `progress.json`。 |
| `--export` | 无 | `markdown-toc` / `docx` / `pdf` / `latex` / `all`，逗号分隔 | 额外导出格式。Markdown 目录不需要 Pandoc；Word/PDF/LaTeX 需要 Pandoc。 |
| `--export-toc` | `auto` | `auto` / `off` | `markdown-toc` 导出是否插入目录。 |

## Speed Mode 预设

| speed-mode | `max-output-tokens` | OCR targets | OCR edge | Figure targets | Vision targets | Vision edge | Vision output | Vision detail |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `fast` | `2500` | `40` | `1200` | `25` | `25` | `1000` | `800` | `low` |
| `balanced` | `4096` | `120` | `1800` | `80` | `80` | `1400` | `1200` | `low` |
| `quality` | `7000` | `0` 不限 | `2200` | `160` | `160` | `1800` | `2000` | `high` |
| `debug` | `4096` | `20` | `1400` | `20` | `20` | `1200` | `1000` | `low` |

## LLM 与笔记生成

| 参数 | 默认值 | 可选值 / 格式 | 说明 |
| --- | --- | --- | --- |
| `--use-llm` | 关闭 | flag | 开启大模型改写。默认不强制开启，避免无 API key 时无法本地解析。 |
| `--provider` | `openai` | `openai` / `deepseek` / `qwen` / `doubao` / `glm` / `gemini` / `claude` | 文本模型服务商。 |
| `--model` | provider 默认 | 模型名或 endpoint id | 手动指定文本模型。 |
| `--api-key` | 环境变量 | 字符串 | 手动传入文本模型 API key。 |
| `--base-url` | provider 默认 | URL | OpenAI-compatible 或代理接口地址。 |
| `--max-output-tokens` | `speed-mode` 决定 | 整数 | 每次文本生成的输出上限。 |
| `--temperature` | 不传 | 数值 | 不传时由服务商默认处理。 |
| `--note-strategy` | `lecture-weave` | `direct` / `lecture-weave` | `lecture-weave` 会先逐页深讲，再章节编织。 |
| `--note-context` | `section` | `auto` / `document` / `section` / `page` | 直接生成或编织阶段的一次上下文粒度。 |
| `--note-style` | `article` | `article` / `faithful` | 默认按知识点组织学习笔记；`faithful` 更贴近原顺序。 |
| `--note-depth` | `detailed` | `concise` / `balanced` / `detailed` | 默认详细讲义式讲解深度。 |
| `--deck-brief` | `auto` | `auto` / `off` / `force` | 是否在笔记生成前先生成全局课程脉络。`auto` 只在 `--use-llm --note-strategy lecture-weave` 时运行。 |
| `--content-guard` | `auto` | `auto` / `off` | 学习内容门禁。`auto` 会生成 `content_guard.json`；开启 `--use-llm` 时调用文本模型判断高置信关键内容，未开启时只做本地启发式审查。 |
| `--note-language` | `zh` | `auto` / `zh` / `en` | 笔记输出语言；可让英文课件生成中文笔记。 |
| `--term-policy` | `bilingual` | `preserve` / `translate` / `bilingual` | 专业术语处理方式；中文笔记默认保留关键英文术语。 |
| `--weave-dedup` | `soft` | `soft` / `normal` / `aggressive` | `lecture-weave` 编织阶段的去重强度。 |
| `--page-neighborhood` | `1` | `0` / `1` / `2` | 逐页深讲时可看到前后几页标题/摘要。 |
| `--section-detection` | `auto` | `auto` / `local` / `llm` | 章节边界识别方式。 |
| `--section-cache` | `on` | `on` / `off` / `refresh` | LLM 章节识别缓存模式。 |
| `--section-cache-dir` | `<out>/.cache/sections` | 路径 | LLM 章节识别缓存目录。 |

### `note-strategy`

```text
direct
```

直接把 `note-context` 选中的上下文交给模型生成笔记。成本较低，但可能在“细节”和“连贯”之间取舍明显。

```text
lecture-weave
```

先对每页做详细讲解，再把逐页讲解编织成章节笔记。质量更好，但调用次数和 token 成本更高。

### `note-language` 与 `term-policy`

`--note-language` 控制最终笔记语言，和课件原文语言不强绑定：

| 值 | 含义 |
| --- | --- |
| `zh` | 输出简体中文笔记。默认值，适合“英文课件、中文学习材料”的常见场景。 |
| `en` | 输出英文笔记。适合英语授课、英文复习资料或后续英文论文/报告引用。 |
| `auto` | 根据材料主体语言自动选择，并尽量保持全文一致。 |

`--term-policy` 控制术语怎么处理：

| 值 | 含义 |
| --- | --- |
| `bilingual` | 默认。关键术语首次出现时尽量写成“中文译名（English term/acronym）”，之后可用中文名或缩写。 |
| `preserve` | 尽量保留原始术语、英文缩写、协议名、算法名、API 名称和代码符号。 |
| `translate` | 在不破坏专业准确性的前提下尽量翻译术语；代码、公式、变量和公认英文名仍保持原样。 |

### `note-context`

| 值 | 含义 | 适合场景 |
| --- | --- | --- |
| `auto` | 短材料整份生成，长材料按章节/分组 | 通用懒人设置。 |
| `document` | 整份文件一个上下文 | 很短的课件。 |
| `section` | 按章节/小节生成或编织 | 当前质量优先默认。 |
| `page` | 每页一个上下文 | 调试覆盖率，或极端保真模式。 |

在 `lecture-weave` 下，第一阶段永远是逐页深讲，`note-context` 控制第二阶段如何编织。

### `deck-brief`

| 值 | 含义 | 适合场景 |
| --- | --- | --- |
| `auto` | 默认。只在启用 `--use-llm --note-strategy lecture-weave` 时，先生成全局课程脉络。 | 高质量正式生成。 |
| `off` | 完全关闭 Deck Brief。 | 想减少一次文本模型调用，或只做本地/调试流程。 |
| `force` | 即使不是 Lecture-Weave，也尝试生成 `deck_brief.json` / `deck_brief.md`。 | 想单独查看课件总体架构，或为后续 GUI/调试准备。 |

Deck Brief 不是最终摘要，也不会替代逐页覆盖。它记录课程主题、核心问题、章节脉络、关键概念、概念依赖、每页角色和跨页关联。逐页深讲阶段会明确约束：`deck_brief` 只能用于理解当前位置和减少重复，正文内容只能来自 `current_page`，不能因为全局脉络而省略当前页元素，也不能把后文内容提前写进当前页。

### `content-guard`

| 值 | 含义 | 适合场景 |
| --- | --- | --- |
| `auto` | 默认。先本地预筛候选学习内容；启用 `--use-llm` 时再用文本模型做语义分类。 | 正式生成，尤其是目录标题中混有定义、条件、公式、表格结论或图示说明的课件。 |
| `off` | 完全关闭学习内容门禁，不生成 `content_guard.json`。 | 只想复现旧行为，或希望减少一次额外文本模型调用。 |

`content_guard.json` 记录 `page_role`（`structural` / `content` / `mixed`）、元素级 `learning_role`、`must_explain`、`confidence`、分类原因、生成后的 `required_visible_coverage`、修复次数和残余风险。纯结构页可以只保留隐藏 source marker；混合页会删除目录/导航碎片，但 `must_explain=true` 且 `confidence >= 0.7` 的元素必须进入可见正文。

覆盖率上，`trace_coverage` 表示元素 ID 是否被 source marker 追踪到，`visible_coverage` 表示是否出现在可见正文中，`required_visible_coverage` 只统计 content guard 判定出的高置信关键学习内容。若 required 元素只出现在隐藏 marker 中，SlideNote 会最多运行一次 repair prompt，把缺失内容自然融入原段落；修复后仍缺失时，会写入 `content_guard.json` 和 `run_summary.json` 的 warning。

### `section-detection`

| 值 | 含义 | 适合场景 |
| --- | --- | --- |
| `auto` | 默认。不开 LLM 时用本地规则；启用章节式 LLM 笔记时用模型辅助识别章节。 | 推荐。 |
| `local` | 只用目录页、章节标题页和固定页数组合的本地规则。 | 离线、快速、可复现。 |
| `llm` | 强制调用文本模型识别章节边界。 | 课件结构混乱、标题页不明显时。 |

章节计划会写入 `sections.json`，其中包含每节的标题、起止页、页码列表、识别原因、缓存和 token 用量。`lecture-weave` 会用这份计划决定最终编织边界。

## 图片、来源与截图呈现

| 参数 | 默认值 | 可选值 | 说明 |
| --- | --- | --- | --- |
| `--asset-mode` | `bundle` | `bundle` / `absolute` / `embed` | Markdown 图片引用方式。 |
| `--source-display` | `hidden` | `hidden` / `footnote` / `inline` | 来源页码和元素 ID 的显示方式。 |
| `--screenshot-policy` | `fallback` | `fallback` / `always` / `never` | 整页截图是否进入 `notes.md`。 |
| `--image-ranking` | `local` | `off` / `local` | 是否给图片做学习价值排序。 |
| `--composite-figures` | `auto` | `off` / `auto` | 是否把多个嵌入小图片拼成的流程图/结构图裁成一个整体组合图。 |
| `--figure-grounding` | `auto` | `off` / `auto` / `vision` | 是否把有学习价值的图片锚定到附近文本/表格，并生成图文对齐报告。 |
| `--figure-placement` | `inline` | `inline` / `page-end` | 图片在笔记中的插入位置；`inline` 尽量靠近相关知识点。 |
| `--figure-audit` | `local` | `off` / `local` / `llm` | 图片是否缺失、缺解释或锚点置信度低的复查策略；当前稳定实现为本地审查。 |

`hidden` 会把来源写成 HTML 注释，正文干净，但 `coverage.md` 和 `source_map.json` 仍可追溯。

`image-ranking` 会写入 `image_importance.json`，记录每张图的分数、排名和原因。当前实现是本地启发式：优先局部裁剪图、面积合适的内容图、有 OCR/视觉摘要的图，惩罚装饰图、整页背景图、极细长图片和过小图片。

`composite-figures` 会写入 `composite_figures.json`。当老师用多个嵌入小图片拼出流程图、架构图或结构图时，系统会从整页截图裁出整体图，并把零散子图片标为 `composite_child`，避免笔记和视觉解析把它们拆成无意义碎片。

`figure-grounding` 会写入 `figure_grounding.json`，记录每张重要图的 `layout_order`、`anchor_element_ids`、`anchor_reason`、`grounding_confidence`、解释状态和复查状态。`auto` 总是运行本地 bbox/版面对齐；如果已经启用 vision/OCR，它会复用已有视觉摘要或 OCR 作为图片解释。`vision` 会在 `--vision off` 时也触发一次视觉解析，用于给重要图片补充解释。

## LLM 缓存

| 参数 | 默认值 | 可选值 / 格式 | 说明 |
| --- | --- | --- | --- |
| `--cache` | `on` | `on` / `off` / `refresh` | 文本 LLM 缓存模式。 |
| `--cache-dir` | `<out>/.cache/llm` | 路径 | 文本 LLM 缓存目录。 |

`lecture-weave` 会分开缓存逐页深讲和章节编织。局部 `--refresh-pages` 会刷新指定页的 page note，以及包含该页的 weave context。

## 导出格式

`notes.md` 始终是原始最终笔记。额外导出由 `--export` 显式开启：

| 格式 | 输出 | 依赖 |
| --- | --- | --- |
| `markdown-toc` | `notes.toc.md` | 无 |
| `docx` | `notes.docx` | Pandoc |
| `pdf` | `notes.pdf` | Pandoc 和本机 PDF 引擎；默认使用 `xelatex` |
| `latex` | `notes.tex` | Pandoc |
| `all` | 以上全部 | Pandoc 用于 docx/pdf/latex |

```powershell
python -m slidenote build lecture.pdf --out outputs\lecture --export markdown-toc,docx
```

`export_report.json` 会记录每个格式的状态、输出路径、Pandoc 命令、失败原因和 warning。请求 `docx`、`pdf` 或 `latex` 但未安装 Pandoc 时，构建仍会保留 `notes.md` 和其它基础产物，但命令返回非 0，避免误认为导出文件已经生成。

## OCR

| 参数 | 默认值 | 可选值 / 格式 | 说明 |
| --- | --- | --- | --- |
| `--ocr` | `off` | `off` / `auto` / `all` | 专门 OCR 阶段。 |
| `--ocr-provider` | `baidu` | `baidu` / `mathpix` / `google` | OCR 服务商。 |
| `--ocr-api-key` | 环境变量 | 字符串 | OCR API key 或 app id。 |
| `--ocr-secret-key` | 环境变量 | 字符串 | OCR secret key 或 app key。 |
| `--ocr-endpoint` | 无 | URL | 自定义 OCR endpoint。 |
| `--ocr-language` | `CHN_ENG` | `CHN_ENG` / `ENG` / `CHN` 等 | OCR 语言提示。 |
| `--ocr-cache` | `on` | `on` / `off` / `refresh` | OCR 缓存模式。 |
| `--ocr-cache-dir` | `<out>/.cache/ocr` | 路径 | OCR 缓存目录。 |
| `--ocr-max-targets` | `speed-mode` 决定 | 整数，`0` 表示不限 | 最大 OCR 目标数。 |
| `--ocr-min-text-chars` | `80` | 整数 | `auto` 模式下，低于该文本量的页更可能 OCR。 |
| `--ocr-min-area` | `120000` | 整数 | OCR 嵌入图最小面积。 |
| `--ocr-max-edge` | `speed-mode` 决定 | 整数 | OCR 前缩放图片长边。 |

## Figure Crop 局部图裁剪

| 参数 | 默认值 | 可选值 / 格式 | 说明 |
| --- | --- | --- | --- |
| `--figure-crop` | `auto` | `off` / `auto` / `vision` | 局部图裁剪策略。 |
| `--figure-max-targets` | `speed-mode` 决定 | 整数，`0` 表示不限 | 最多送多少页做 bbox 检测。 |
| `--figure-max-crops-per-page` | `3` | 整数 | 每页最多裁几张局部图。 |
| `--figure-min-confidence` | `0.45` | 0 到 1 | 接受 bbox 的最低置信度。 |
| `--figure-min-area` | `40000` | 整数 | 裁剪图最小面积。 |
| `--figure-cache` | `on` | `on` / `off` / `refresh` | 裁剪 bbox 缓存模式。 |
| `--figure-cache-dir` | `<out>/.cache/figure` | 路径 | Figure Crop 缓存目录。 |

`auto` 表示只有在启用 `--vision auto/all` 时才顺带裁剪；`vision` 表示即使不做视觉摘要，也强制调用视觉模型做 bbox 裁剪。

## Vision 视觉解析

| 参数 | 默认值 | 可选值 / 格式 | 说明 |
| --- | --- | --- | --- |
| `--vision` | `auto` | `off` / `auto` / `all` | 视觉解析模式。当前默认质量优先。 |
| `--vision-provider` | `qwen` | `openai` / `qwen` / `doubao` / `gemini` / `claude` | 视觉模型服务商。默认用 Qwen，适合国内用户配合 `DASHSCOPE_API_KEY` 使用。 |
| `--vision-model` | provider 默认 | 模型名或 endpoint id | 手动指定视觉模型。 |
| `--vision-api-key` | 环境变量 | 字符串 | 视觉模型 API key。 |
| `--vision-base-url` | provider 默认 | URL | 自定义视觉 API base URL。 |
| `--vision-cache` | `on` | `on` / `off` / `refresh` | 视觉解析缓存模式。 |
| `--vision-cache-dir` | `<out>/.cache/vision` | 路径 | 视觉解析缓存目录。 |
| `--vision-max-targets` | `speed-mode` 决定 | 整数，`0` 表示不限 | 最大视觉目标数。 |
| `--vision-min-area` | `120000` | 整数 | `auto` 选择嵌入图的最小面积。 |
| `--vision-max-edge` | `speed-mode` 决定 | 整数 | 视觉调用前缩放图片长边。 |
| `--vision-max-output-tokens` | `speed-mode` 决定 | 整数 | 每张图视觉输出上限。 |
| `--vision-temperature` | `0.0` | 数值 | 视觉模型温度。 |
| `--vision-detail` | `speed-mode` 决定 | `low` / `high` / `auto` | OpenAI image detail 参数。 |

## Provider 默认模型

| Provider | 文本默认模型 | 视觉默认模型 | API key 环境变量 |
| --- | --- | --- | --- |
| `openai` | `gpt-4.1-mini` | `gpt-4.1-mini` | `OPENAI_API_KEY` |
| `deepseek` | `deepseek-v4-flash` | 不支持视觉 | `DEEPSEEK_API_KEY` |
| `qwen` | `qwen-plus` | `qwen-vl-plus` | `QWEN_API_KEY` / `DASHSCOPE_API_KEY` |
| `doubao` | 需传 `--model` | 需传 `--vision-model` | `DOUBAO_API_KEY` / `ARK_API_KEY` / `VOLCENGINE_API_KEY` |
| `glm` | `glm-5.1` | 不支持视觉 | `GLM_API_KEY` / `ZAI_API_KEY` / `ZHIPUAI_API_KEY` |
| `gemini` | `gemini-3-flash-preview` | `gemini-3-flash-preview` | `GEMINI_API_KEY` / `GOOGLE_API_KEY` |
| `claude` | `claude-sonnet-4-20250514` | `claude-sonnet-4-20250514` | `ANTHROPIC_API_KEY` / `CLAUDE_API_KEY` |

## 输出文件

常见输出：

```text
content.json
page_modalities.json
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
images/
figures/
screenshots/
```

只有启用对应功能时，才会生成 `ocr_usage.json`、`vision_usage.json`、`figure_usage.json` 等文件。
