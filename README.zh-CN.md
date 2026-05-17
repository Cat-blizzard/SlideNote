<p align="center">
  <img src="assets/slidenote-logo.png" alt="SlideNote" width="520">
</p>

<h1 align="center">SlideNote</h1>

<p align="center">
  <strong>面向课程幻灯片的覆盖率感知笔记生成系统</strong>
</p>

<p align="center">
  把 PPT/PDF 转换成结构清晰、可追溯、保留图片、支持 OCR/视觉解析和 Lecture-Weave 改写的课程笔记。
</p>

<p align="center">
  <em>不只是总结课件，而是把展示材料整理成真正适合学习的文字材料。</em>
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
  <a href="CONFIG.zh-CN.md">配置参考</a> |
  <a href="ROADMAP.zh-CN.md">路线图</a>
</p>

---

## 快速开始

```powershell
git clone https://github.com/Cat-blizzard/SlideNote.git
cd SlideNote
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev,llm]"
python -m slidenote doctor
```

如果只想用一个 LLM API key 生成纯文本 AI 笔记：

```powershell
$env:DEEPSEEK_API_KEY="..."
python -m slidenote build path\to\lecture.pdf --out outputs\lecture --use-llm --provider deepseek --vision off --figure-crop off
```

如果想生成带图片理解的高质量笔记，再配置 Qwen/DashScope key。现在默认视觉 provider 是 Qwen：

```powershell
$env:DASHSCOPE_API_KEY="..."
$env:DEEPSEEK_API_KEY="..."
python -m slidenote build path\to\lecture.pdf --out outputs\lecture --use-llm --provider deepseek
```

生成后打开 `outputs\lecture\notes.md`。图片会默认打包在 `outputs\lecture\notes.assets\` 里。

## 功能

- 支持 `.pptx` 和 `.pdf`，`.ppt` 会尝试通过 LibreOffice 转成 PDF 后解析。
- 逐页抽取标题、正文文本块、表格、嵌入图片。
- 自动识别页面类型：原生文字页、图文混合页、整页图片页、形状图页、装饰页，并据此路由 OCR、视觉解析和局部图裁剪。
- 自动给图片做学习价值排序，让视觉调用和笔记优先使用图表、流程图、局部裁剪图和高信息量图片。
- 生成 `sections.json`；开启 LLM 时，`--section-detection auto` 可以用模型辅助修正章节边界，再交给 Lecture-Weave 编织。
- 在高质量 Lecture-Weave 模式下生成 `deck_brief.json` / `deck_brief.md`：先形成全局课程脉络，但只作为导航，不替代逐页覆盖。
- 每页尽量保存页面截图：PDF 原生支持；PPTX 需要本机安装 LibreOffice 或 PowerPoint COM 可用。
- 生成 `content.json` 作为原始内容清单。
- 生成 `notes.md`，默认隐藏来源标记，也可选择显示简洁页码或详细元素 ID。
- 生成 `coverage.json` / `coverage.md`，检查哪些元素没有出现在笔记中。
- 支持多家 LLM：ChatGPT/OpenAI、DeepSeek、通义千问、豆包、GLM、Gemini、Claude。
- 支持 `lecture-weave` 高质量笔记策略：先逐页深讲，再按章节编织成连贯笔记。
- 支持控制笔记输出语言和术语策略：英文课件可以生成中文或英文笔记，中文笔记可保留英文专业术语。

## 起源

SlideNote 来自一个个人的学习困境。

我一直不是那种特别适合“只靠听课”学习的人。有时候老师讲得很快，或者表达方式不太适合我，我在课堂上并不能完全跟上。相比听课，我更喜欢阅读：文字可以反复看，可以停下来想，也可以按照自己的节奏跳转、回看和整理。

但课下直接读 PPT，我又总觉得差点意思。PPT 本质上更像是老师讲课时的提示板，而不是一份真正适合阅读和复习的笔记。很多内容都是零散的，逻辑藏在老师的讲解里，关键知识还经常出现在图、表、流程图、公式截图和页面布局中。

当然，我也试过自己整理笔记，但这件事既耗时间，也很难保证不遗漏。而且手写笔记的字迹和排版有时会让我自己都不太想回头看。

所以我想做一个工具，把课程 PPT/PDF 转换成结构清晰、内容完整、保留图片、可追溯到原页码的课程笔记。它不只是总结课件，而是尽量把展示材料变成真正适合学习的文字材料。

于是就有了 SlideNote。

## 本机环境要求

SlideNote 不需要本机 GPU。基础解析、OCR、视觉理解和大模型改写可以分层启用：你只用本地规则草稿时，环境很轻；启用 LLM/OCR/视觉时，需要配置对应平台的 API key。

### 必需环境

- Python `3.10` 或更高版本。
- 推荐使用虚拟环境 `.venv`。
- 基础 Python 依赖由 `pyproject.toml` 管理：
  - `python-pptx`：解析 `.pptx` 结构、文本、表格和嵌入图片。
  - `PyMuPDF`：解析 `.pdf`，渲染 PDF 页面截图。
  - `Pillow`：处理、缩放和保存图片。

### 可选但强烈推荐的软件

| 软件 | 是否必需 | 用途 |
| --- | --- | --- |
| LibreOffice | 推荐 | 在没有 PowerPoint 时，把 `.ppt` / `.pptx` 转成 PDF，并生成整页截图。 |
| Microsoft PowerPoint | 可选 | Windows 上可通过 COM 导出 PPTX 整页截图。 |
| WPS | 手动可用 | 当前 CLI 不会自动调用 WPS，但可以用 WPS 手动把 PPT 导出成 PDF 后再交给 SlideNote。 |

Windows 用户如果没有 PowerPoint，推荐安装 Windows 版 LibreOffice。常见路径是：

```powershell
C:\Program Files\LibreOffice\program\soffice.exe
```

当前代码会从系统 PATH 里查找 `soffice` 或 `libreoffice`。安装后可以用下面命令检查：

```powershell
soffice --version
```

如果提示找不到命令，可以把下面目录加入 Windows 的 PATH：

```text
C:\Program Files\LibreOffice\program
```

PowerPoint 路线需要额外安装 `pywin32`：

```powershell
python -m pip install pywin32
```

如果 LibreOffice 和 PowerPoint 都不可用：

- `.pdf` 仍然可以正常解析。
- `.pptx` 仍然可以提取文本、表格和嵌入图片，但整页截图可能缺失。
- `.ppt` 老格式通常无法直接处理，建议先用 WPS、PowerPoint 或 LibreOffice 手动导出为 PDF。

### API Key 配置

LLM、OCR 和视觉解析都是可选能力。不开启对应功能时，不需要配置 API key。

常见环境变量示例：

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

PowerShell 里的 `$env:...="..."` 只对当前终端会话生效。长期使用时，可以把这些变量配置到 Windows 系统环境变量里，或者每次运行前重新设置。

### 按功能选择配置

| 目标 | 需要什么 |
| --- | --- |
| 只解析 PDF / PPTX 并生成本地规则草稿 | Python 3.10+ 和基础依赖 |
| 使用 LLM 改写正式笔记 | 安装 `.[llm]`，配置对应 LLM API key |
| 处理扫描 PDF、图片型 PPT | 配置 OCR API，运行时加 `--ocr auto` |
| 理解流程图、截图、图表等视觉信息 | 配置视觉模型 API，运行时加 `--vision auto` |
| 让 PPTX 保留整页截图 | 安装 LibreOffice，或 Windows 上安装 PowerPoint + `pywin32` |
| 处理 `.ppt` 老格式 | 推荐安装 LibreOffice；或先手动导出 PDF |

## 安装

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev,llm]"
```

如果只需要本地规则草稿，不需要 LLM：

```powershell
python -m pip install -e ".[dev]"
```

## 基本使用

本地规则草稿：

```powershell
python -m slidenote build path\to\lecture.pptx --out outputs\lecture --vision off
```

LLM 正式改写：

```powershell
python -m slidenote build path\to\lecture.pdf --out outputs\lecture --use-llm --provider openai
```

输出目录结构：

```text
outputs/lecture/
  content.json
  page_modalities.json
  image_importance.json
  sections.json
  deck_brief.md
  deck_brief.json
  notes.md
  page_notes.md
  page_notes.json
  weave_report.json
  llm_usage.json
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
  progress.json
  run_summary.json
  notes.assets/
  figures/
  images/
  screenshots/
```

默认情况下，`notes.md` 会引用 `notes.assets/` 中的图片副本，所以把 `notes.md` 和 `notes.assets/` 一起移动或打包时，图片仍然能显示。

`page_modalities.json` 会记录本地页面类型检测结果，用来让后续步骤选择更省钱、更稳定的处理路线：

- `native_text`：优先直接使用可提取文本。
- `mixed`：使用可提取文本，同时保留和解析嵌入图片。
- `image_only`：优先对整页截图做 OCR、局部图裁剪和整页视觉解析。
- `shape_diagram`：说明图可能由 PPT 形状/箭头/文本框拼成，适合从整页截图中裁剪局部图。
- `decorative`：低优先级页面，除非用户显式刷新或需要保留。

`image_importance.json` 会记录每张图片的学习价值分数、排序和原因。`--vision auto` 会优先选择排序靠前的局部裁剪图或嵌入图，再回退到整页截图。

`figure_grounding.json` 会记录每张重要图片应该靠近哪段文字或表格：包括版面顺序、锚点元素、锚定原因、置信度、图片解释状态和是否需要人工复查。`notes.md` 会用这份信息把图片尽量插到相关知识点附近，而不是全部堆在页尾。

`sections.json` 会记录最终采用的章节计划。不开 LLM 时使用本地规则；在 `--section-detection auto` 且启用章节式 LLM 笔记时，会先调用文本模型辅助识别章节边界。

`deck_brief.json` 会在 `--deck-brief auto` 且同时启用 `--use-llm --note-strategy lecture-weave` 时生成，也可以用 `--deck-brief force` 强制生成。它记录课程主题、核心问题、章节脉络、关键概念、每页角色和跨页关联。后续逐页深讲只把它当作“全局导航图”：正文内容仍只能来自当前页，覆盖率检查仍按原始 text/table/image 元素 ID 执行。

## 环境检测

如果不确定本机缺什么，可以先运行：

```powershell
python -m slidenote doctor
```

它会检查：

- Python 版本。
- 核心依赖：PyMuPDF、python-pptx、Pillow。
- 可选依赖：OpenAI SDK、pywin32。
- 外部软件：LibreOffice / `soffice`。
- 常见 LLM/OCR API key 环境变量。
- 每个检查项的影响范围、修复建议和 GUI 可读取的 readiness 状态。

也可以把结果写成 JSON：

```powershell
python -m slidenote doctor --json doctor.json
```

## 速度、进度与局部刷新

完整参数说明见 [CONFIG.zh-CN.md](CONFIG.zh-CN.md)。

长 PPT/PDF 可能需要较长时间，尤其是开启 OCR、视觉解析和 LLM 改写时。SlideNote 会默认写入：

```text
progress.json      # 当前/最近一次运行进度
  run_summary.json   # 运行完成后的总览报告
```

CLI 也会显示阶段进度。想关闭终端进度输出但保留 `progress.json`：

```powershell
python -m slidenote build lecture.pdf --out outputs\lecture --quiet
```

速度模式不会自动开启 OCR 或 LLM。当前默认是质量优先，视觉解析默认为 `auto`；如果只想本地解析，可以显式加 `--vision off`。速度模式会调整未显式设置的限额：

```powershell
--speed-mode fast      # 更少视觉/OCR targets，更短输出预算
--speed-mode balanced  # 成本/速度折中
--speed-mode quality   # 默认：更高图片分辨率和输出预算
--speed-mode debug     # 小目标数，适合调试
```

例如：

```powershell
python -m slidenote build lecture.pdf `
  --out outputs\lecture-fast `
  --speed-mode fast `
  --vision auto `
  --vision-provider qwen `
  --use-llm `
  --provider deepseek
```

OCR、Vision 和 LLM 笔记上下文可以并发调用 API。并发能加速，但也更容易触发服务商限速；建议先从 `2` 或 `3` 开始：

```powershell
--concurrency 3
```

如果希望不同输出目录共用缓存，可以设置全局缓存目录：

```powershell
python -m slidenote build lecture.pdf `
  --out outputs\lecture-v2 `
  --global-cache-dir .slidenote-cache `
  --use-llm `
  --provider deepseek
```

也可以只强制某些页绕过缓存重新生成，其它页仍然尽量命中缓存：

```powershell
--refresh-pages 3,5-8
```

注意：当前 `--refresh-pages` 是“绕过这些页的本地缓存”，不是只输出这些页。它适合在同一份材料上局部刷新 OCR、视觉或 LLM 结果。

## 笔记呈现选项

默认输出更偏“可直接阅读的文章式笔记”，不会在正文里反复显示元素 ID，也不会为没有视觉解析的图片写大段说明：

```powershell
--note-style article       # 默认：把 bullet 改写成较连贯的笔记
--source-display hidden    # 默认：来源写入 HTML 隐藏注释和 source_map.json
--asset-mode bundle        # 默认：图片复制到 notes.assets/
--note-context section     # 默认：按章节/分组编织最终笔记
--note-strategy lecture-weave  # 默认：先逐页深讲，再章节编织
--note-depth detailed      # 默认：尽量保留逐页讲解细节
--deck-brief auto          # 默认：只在 Lecture-Weave 高质量模式下生成全局脉络
--note-language zh         # 默认：输出简体中文笔记
--term-policy bilingual    # 默认：中文笔记中保留关键英文专业术语
```

`lecture-weave` 现在是默认 LLM 笔记策略。这个模式会更耗时、更耗 token，但更接近“逐页问 AI：请你讲讲这一页”的效果：先可生成 Deck Brief 作为全局导航，再为每页生成详细讲解，最后把逐页讲解编织成连贯章节。Deck Brief 有明确约束：不能替代当前页证据，也不能让逐页讲解变短。

输出语言和课件语言是分开的。英文课件想生成中文笔记时，默认的 `--note-language zh --term-policy bilingual` 会要求模型在关键术语首次出现时尽量写成“中文译名（English term/acronym）”；想要英文笔记可以用 `--note-language en`。如果希望尽量保留原始术语，用 `--term-policy preserve`；如果希望尽量翻译术语，用 `--term-policy translate`。

```powershell
python -m slidenote build lecture.pdf `
  --out outputs\lecture `
  --use-llm `
  --provider deepseek `
  --weave-dedup soft
```

`lecture-weave` 会额外输出 `deck_brief.json`、`deck_brief.md`、`page_notes.json`、`page_notes.md` 和 `weave_report.json`。这些是中间产物和调试报告；最终阅读仍以 `notes.md` 为准。

如果希望正文显示简洁来源页码，可以用：

```powershell
--source-display footnote
```

如果要调试严格覆盖率，可以回到逐页上下文和详细来源：

```powershell
--note-context page --source-display inline --note-style faithful
```

整页截图默认只作兜底：如果某页已经有嵌入图片或局部裁剪图，`notes.md` 默认不再插入整页截图。需要改回强证据风格时可以用：

```powershell
--screenshot-policy always
```

## 图片过滤与来源映射

PDF/PPT 里经常包含 logo、小图标、背景碎片等装饰性图片。SlideNote 会保留原始图片文件，但会在 `content.json` 中给疑似装饰图打标：

```json
{
  "role": "decorative",
  "ignored": true,
  "ignore_reason": "tiny_area"
}
```

被标记为 `ignored` 的图片默认不会进入笔记、覆盖率检查、OCR fallback 或独立视觉解析目标。整页截图仍会保留在 `screenshots/`，用于兜底保存视觉信息。

## 局部图裁剪

有些课件不是把图作为独立图片存储，而是把整页做成扫描图，或用 PPT 形状、箭头、文本框拼出一张“看起来像图”的区域。SlideNote 支持在笔记生成前尝试把这类局部图从整页截图中裁出来：

```powershell
--figure-crop auto     # 默认：只有开启 --vision 时才会额外调用视觉模型定位局部图
--figure-crop vision   # 即使 --vision off，也强制用视觉模型做 bbox 定位
--figure-crop off      # 关闭局部图裁剪
```

裁剪结果会写入：

```text
figures/
figures.json
figure_usage.json
```

默认参数：

```powershell
--figure-max-targets 80
--figure-max-crops-per-page 3
--figure-min-confidence 0.45
--figure-min-area 40000
--figure-cache on
```

局部图裁剪使用视觉模型返回 bbox，然后由本地程序裁剪图片。它不是强保证：如果视觉模型没有找到可靠局部图，系统会回退到整页截图。

## 图文对齐

在 OCR/vision 之后、写笔记之前，SlideNote 会把非装饰图片锚定到同页附近的文字或表格。默认走本地版面判断：

```powershell
--figure-grounding auto   # 默认：本地版面对齐，复用已有 OCR/vision 摘要
--figure-placement inline # 默认：图片尽量插入对应概念附近
--figure-audit local      # 报告缺解释、低置信度或需复查的图片
```

如果希望即使 `--vision off` 也给重要图片补解释，可以用 `--figure-grounding vision`，这会触发正常的视觉解析流程。`coverage.md` 现在也会单独列出图片覆盖情况：哪些图进入了笔记、锚定到哪里、解释是否缺失、是否需要人工复查。

`source_map.json` 会记录笔记块和原始元素之间的映射，方便未来 GUI、LaTeX/Word/HTML 导出和来源显示策略使用：

```text
note block -> PPT/PDF 页码 -> text/table/image element id
```

默认正文会把来源写入类似 `<!-- slidenote-source: p4:s4_t1,s4_t2 -->` 的隐藏注释，并在 `source_map.json` 中保存完整映射。这样阅读时不被元素 ID 打断，覆盖率检查和 GUI 溯源仍然可用。

## LLM Provider

OpenAI-compatible 的平台使用 OpenAI SDK，所以安装 `.[llm]` 后即可使用。Gemini 和 Claude 走原生 REST，不需要额外 SDK。

当前 LLM 改写步骤默认是文本调用：不会把图片二进制直接传给模型。对 DeepSeek 这类非多模态模型，SlideNote 会只提供文本块、表格、图片路径和元素 ID；提示词会要求模型保留图片引用，但不能猜测图片内容。图片理解应作为独立步骤完成，例如先用 OCR 或视觉模型生成 `visual_summary`，再交给 DeepSeek 做文字改写。

| Provider | 用法 | 默认模型 | API Key 环境变量 | Base URL |
| --- | --- | --- | --- | --- |
| ChatGPT/OpenAI | `--provider openai` | `gpt-4.1-mini` | `OPENAI_API_KEY` | OpenAI SDK 默认 |
| DeepSeek | `--provider deepseek` | `deepseek-v4-flash` | `DEEPSEEK_API_KEY` | `https://api.deepseek.com` |
| 通义千问 | `--provider qwen` | 文本默认 `qwen-plus`，视觉默认 `qwen-vl-plus` | `QWEN_API_KEY` 或 `DASHSCOPE_API_KEY` | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| 豆包/火山方舟 | `--provider doubao` | 需传 `--model`；视觉需传 `--vision-model` 或设 `ARK_VISION_MODEL` | `DOUBAO_API_KEY` / `ARK_API_KEY` / `VOLCENGINE_API_KEY` | `https://ark.cn-beijing.volces.com/api/v3` |
| GLM/智谱 | `--provider glm` | `glm-5.1` | `GLM_API_KEY` / `ZAI_API_KEY` / `ZHIPUAI_API_KEY` | `https://open.bigmodel.cn/api/paas/v4/` |
| Gemini | `--provider gemini` | `gemini-3-flash-preview` | `GEMINI_API_KEY` 或 `GOOGLE_API_KEY` | `https://generativelanguage.googleapis.com/v1beta` |
| Claude | `--provider claude` | `claude-sonnet-4-20250514` | `ANTHROPIC_API_KEY` 或 `CLAUDE_API_KEY` | `https://api.anthropic.com` |

示例：

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

```powershell
$env:GEMINI_API_KEY="..."
python -m slidenote build lecture.pdf --out outputs\lecture --use-llm --provider gemini
```

```powershell
$env:ANTHROPIC_API_KEY="..."
python -m slidenote build lecture.pdf --out outputs\lecture --use-llm --provider claude
```

通用覆盖参数：

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

如果你使用代理、私有网关或不同地域，可以覆盖 Base URL：

```powershell
python -m slidenote build lecture.pptx --use-llm --provider qwen --base-url https://dashscope-intl.aliyuncs.com/compatible-mode/v1
```

也可以用环境变量覆盖模型和 Base URL：

```powershell
$env:SLIDENOTE_MODEL="qwen-plus"
$env:SLIDENOTE_BASE_URL="https://dashscope.aliyuncs.com/compatible-mode/v1"
```

## 专门 OCR

SlideNote 现在把 OCR 和视觉理解分开：

```text
OCR = 读出图片里的文字
视觉理解 = 解释图、流程、趋势、布局和视觉关系
```

专门 OCR 适合扫描版 PDF、图片型 PPT、截图和扫描教材页。OCR 会在视觉理解和笔记生成之前运行，把识别结果写回 `content.json`：

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

OCR 默认关闭：

```powershell
--ocr off
```

中文场景推荐先用百度 OCR：

```powershell
$env:BAIDU_OCR_API_KEY="..."
$env:BAIDU_OCR_SECRET_KEY="..."

python -m slidenote build lecture.pdf `
  --out outputs\lecture `
  --ocr auto `
  --ocr-provider baidu
```

`--ocr auto` 不会 OCR 每一页。系统会先做本地文本抽取；只有抽取文字太少、像扫描件或图片型页面的页，才会被送去 OCR。

已支持 OCR provider：

| Provider | 用法 | 需要的凭据 |
| --- | --- | --- |
| 百度 OCR | `--ocr-provider baidu` | `BAIDU_OCR_API_KEY` + `BAIDU_OCR_SECRET_KEY` |
| Mathpix | `--ocr-provider mathpix` | `MATHPIX_APP_ID` + `MATHPIX_APP_KEY` |
| Google Vision OCR | `--ocr-provider google` | `GOOGLE_VISION_API_KEY` 或 `GOOGLE_API_KEY` |

示例：

```powershell
$env:MATHPIX_APP_ID="..."
$env:MATHPIX_APP_KEY="..."
python -m slidenote build math_notes.pdf --out outputs\math --ocr auto --ocr-provider mathpix
```

```powershell
$env:GOOGLE_VISION_API_KEY="..."
python -m slidenote build lecture.pdf --out outputs\lecture --ocr auto --ocr-provider google
```

OCR 控制参数：

```powershell
--ocr auto                 # 只 OCR 抽取文字较少的页面
--ocr all                  # 尽量 OCR 每页截图
--ocr-max-targets 120
--ocr-min-text-chars 80
--ocr-max-edge 1800
--ocr-language CHN_ENG
--ocr-cache on
```

OCR 输出：

```text
ocr.json
ocr_usage.json
```

`ocr_usage.json` 会记录目标页、缓存命中、API 调用次数和识别字符数。OCR 结果和视觉摘要分开缓存，所以换笔记模型或视觉模型时，不会强制重新 OCR。

## 视觉解析

很多课程 PPT 的关键信息在图、流程图、截图、公式图片和页面布局里。SlideNote 支持在笔记生成前先跑一个独立视觉解析步骤，把图片中的文字和视觉摘要写回 `content.json`，同时输出：

```text
visuals.json
vision_usage.json
```

视觉解析现在默认是 `auto`，因为当前默认策略更重视笔记质量而不是节省 token。默认视觉 provider 是 Qwen，所以带图片理解的运行需要配置 `DASHSCOPE_API_KEY` 或 `QWEN_API_KEY`；如果只想本地解析或纯文本改写，可以关闭：

```powershell
--vision off
```

推荐先用自动选择模式。中国区默认推荐 Qwen-VL 做视觉解析，再用 DeepSeek 做正文改写：

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

这条命令的含义是：先用 Qwen-VL 解析高价值图片，再用 DeepSeek 做正文改写。视觉解析不是生成孤立 caption，而是会带上本页标题和文字上下文，让视觉摘要尽量说明图片与文字之间的关系；最终笔记生成也会要求把 `visual_summary` 和相关 text/table 合并成同一个知识段落。

豆包/火山方舟也支持视觉解析，但通常需要在方舟控制台创建视觉模型推理接入点，然后把 endpoint/model id 传给 `--vision-model` 或设环境变量 `ARK_VISION_MODEL`：

```powershell
$env:ARK_API_KEY="..."
$env:ARK_VISION_MODEL="ep-xxxxxxxx"
$env:DEEPSEEK_API_KEY="..."
python -m slidenote build lecture.pptx --out outputs\lecture --vision auto --vision-provider doubao --use-llm --provider deepseek
```

图片取舍策略：

```powershell
--vision auto   # 推荐：优先解析整页截图，跳过明显低价值小图
--vision all    # 尽量解析每一页截图；成本最高
--vision off    # 不做视觉解析
```

默认 `auto` 会优先解析已经裁剪出的局部图，其次是较大的嵌入图片，最后才回退到整页截图。这样视觉摘要更聚焦在真正的图、表、流程图或截图上，而不是描述整张 PPT。

成本控制参数：

```powershell
--vision-max-targets 80       # 最多解析多少张图/页截图；0 表示不限
--vision-min-area 120000      # auto fallback 时跳过太小的嵌入图
--vision-max-edge 1400        # 传给模型前缩小长边，降低成本
--vision-detail low           # OpenAI 低细节模式，适合先跑粗解析
--vision-cache on             # 默认开启视觉缓存
```

更保守的低成本配置：

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

如果你确定课件高度依赖图片，可以用：

```powershell
python -m slidenote build lecture.pptx --out outputs\lecture --vision all --vision-provider openai --use-llm --provider openai
```

但不建议默认全量读图。更好的产品策略是：先 `auto`，看 `vision_usage.json` 和 `coverage.md`，再对低质量页面或遗漏页面局部 `refresh`。

视觉结果会写回页面字段：

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

后续 DeepSeek、GLM、千问等文本模型会读取这些文字化视觉结果，而不是直接看图片。

## LLM 缓存与用量报告

LLM 改写默认开启本地缓存。每个笔记上下文会根据以下信息生成缓存 key：

```text
结构化笔记上下文 + prompt version + note strategy + provider + model + base_url + temperature + max-output-tokens + 局部图/截图呈现选项
```

同一上下文内容和同一参数再次生成时，会直接复用缓存，不再调用模型。缓存命中信息不会污染正文，而是写入 `llm_usage.json`，方便后续 GUI 直接展示。

在 `lecture-weave` 模式下，逐页深讲缓存和章节编织缓存是分开的。比如 `--refresh-pages 12` 会重跑第 12 页的逐页讲解，以及包含第 12 页的编织上下文；其它页仍然可以命中缓存。

Deck Brief 也复用同一个 LLM 缓存目录，并使用独立的 `generation_stage="deck_brief"`。如果课件、章节计划和参数没有变化，全局脉络可以直接命中缓存，不必再次调用模型。

缓存模式：

```powershell
--cache on       # 默认：命中就复用，未命中就调用并写入缓存
--cache refresh  # 不读旧缓存，重新调用模型并覆盖缓存
--cache off      # 完全关闭缓存
```

也可以指定缓存目录：

```powershell
python -m slidenote build lecture.pptx --use-llm --provider deepseek --cache-dir .slidenote-cache\llm
```

`llm_usage.json` 会记录：

- 每个上下文的 `cache_status`：`local_hit`、`miss`、`refresh` 或 `disabled`
- 每个上下文的 `cache_key` 和 `cache_file`
- 实际调用次数、缓存命中次数
- `lecture-weave` 模式下的 Deck Brief、`page_note_calls` 和 `weave_calls`
- provider 返回的 input/output/total tokens
- provider 侧 cached input tokens，如果该平台返回此字段

## 设计原则

SlideNote 不走 `PPT -> LLM -> 总结` 的捷径，而是：

```text
PPT/PDF -> 结构化解析 -> 内容清单 -> 笔记生成 -> 覆盖率校验 -> 导出
```

本地规则草稿只负责把结构化内容“保底写出来”，方便调试解析和覆盖率。正式笔记建议使用 `--use-llm`，但覆盖率检查仍然依靠元素 ID 做硬校验，避免模型把细节悄悄总结掉。

## 参考文档

- [OpenAI Chat Completions API](https://platform.openai.com/docs/api-reference/chat/create)
- [OpenAI Images and vision](https://developers.openai.com/api/docs/guides/images-vision)
- [DeepSeek API](https://api-docs.deepseek.com/)
- [阿里云百炼 OpenAI 兼容接口](https://help.aliyun.com/zh/model-studio/compatibility-of-openai-with-dashscope)
- [火山方舟 OpenAI SDK 兼容](https://www.volcengine.com/docs/82379/1330626)
- [智谱 GLM OpenAI 兼容](https://docs.bigmodel.cn/cn/guide/develop/openai/introduction)
- [百度 OCR API](https://ai.baidu.com/ai-doc/REFERENCE/4kru2vqdg)
- [Mathpix OCR API](https://docs.mathpix.com/reference/post-v3-text)
- [Google Cloud Vision OCR](https://cloud.google.com/vision/docs/ocr)
- [Gemini generateContent API](https://ai.google.dev/gemini-api/docs/text-generation)
- [Gemini image understanding](https://ai.google.dev/gemini-api/docs/image-understanding)
- [Claude Messages API](https://docs.anthropic.com/en/api/messages)
- [Claude Vision](https://platform.claude.com/docs/en/build-with-claude/vision)
