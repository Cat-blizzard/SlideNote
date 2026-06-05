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
  <a href="docs/index.zh-CN.md">文档中心</a> |
  <a href="#实验分支claude-code-backend">Claude Backend</a> |
  <a href="CONFIG.zh-CN.md">配置参考</a> |
  <a href="ROADMAP.zh-CN.md">路线图</a>
</p>

---

## 目录

- [快速开始](#快速开始)
- [可选 GUI](#可选-gui)
- [实验分支：Claude Backend](#实验分支claude-code-backend)
- [Pipeline 与 Preset](#slidenote-pipeline)
- [起源](#起源)
- [环境与安装](#环境与安装)
- [常用工作流](#常用工作流)
- [技术文档](#技术文档)
- [未来展望](#未来展望)
- [许可证与致谢](#许可证)

## 快速开始

```powershell
git clone https://github.com/Cat-blizzard/SlideNote.git
cd SlideNote
.\install.ps1
.\run_gui.ps1
```

安装脚本会创建 `.venv`、安装带 GUI/LLM 的依赖，并运行 `slidenote doctor`。GUI 可以在页面里临时填写 API key，所以新手不需要先理解终端环境变量。

也可以手动安装：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev,llm]"
python -m slidenote doctor
```

如果只想先本地预览，确认课件能正常解析：

```powershell
python -m slidenote build path\to\lecture.pdf --out outputs\local --preset local --export markdown-zip
```

第一次安装后建议先跑这条 Local preview 命令，确认 `notes.md` 和可分享的 `notes.zip` 都能生成，再切换到 `lecture` 质量模式。

如果想生成带图片理解的高质量讲义式笔记：

```powershell
$env:DASHSCOPE_API_KEY="..."
$env:DEEPSEEK_API_KEY="..."
python -m slidenote build path\to\lecture.pdf --out outputs\lecture --provider deepseek --export markdown-zip
```

生成后打开 `outputs\lecture\notes.md`。图片默认会复制到 `outputs\lecture\notes.assets\`。

## 可选 GUI

SlideNote Studio 是套在同一条 CLI pipeline 外面的 Streamlit 图形界面。它支持上传 PPT/PDF、在页面内配置 API key、选择运行 preset、查看进度和 ETA、查看 token / 成本报告、逐页检查来源，并下载生成结果。

```powershell
.\run_gui.ps1
```

GUI 详情见 [gui/README_GUI.zh-CN.md](gui/README_GUI.zh-CN.md) 和 [gui/README_GUI.md](gui/README_GUI.md)。

## 教材库

SlideNote 也可以先把 PDF 教材构建成 RAG-ready 文档库。这个入口只做教材解析、目录识别、章节映射和 chunk 切片；当前不会自动参与笔记生成。

```powershell
python -m slidenote textbook-index path\to\textbook.pdf --out outputs\textbook --ocr auto
```

`--ocr auto` 会先抽取 PDF 原生文本，只对扫描页或低文本页调用 OCR。可复制文字的电子教材可以使用 `--ocr off`。

## 实验分支：Claude Code Backend

当前 `experiment/claude-backend` 分支正在验证一条 Claude Code 后端旁路。稳定的 `slidenote build` 不被直接改成 Claude-first；SlideNote 继续负责解析、图片资产、source id、coverage、source map、落盘和报告，Claude Code 负责基于受限 agent pack 写讲义、组织图文，并按 coverage 结果修订 section。

```powershell
python -m slidenote agent-pack lecture.pdf --out outputs\agent-pack
python -m slidenote agent-build lecture.pdf --out outputs\agent-build
python -m slidenote agent-eval lecture.pdf --out outputs\agent-eval
```

本机需要能运行官方 `claude` 命令，或通过 `--claude-command` 指定路径，并且 Claude Code 的认证由本机环境负责。Claude 只通过 stdout 返回 JSON，不直接写仓库或输出文件。详细说明见 [Claude Backend](docs/claude-backend.zh-CN.md)。

## SlideNote Pipeline

SlideNote 按五个产品阶段组织。底层模块可以继续保持细粒度，方便缓存、调试和局部刷新；但用户侧应该先看到一条清楚的流水线，而不是一长串彼此独立的开关。

```text
Ingest -> Understand -> Write -> Guard -> Export
```

| 阶段 | 作用 | 主要产物 |
| --- | --- | --- |
| **1. Ingest** | 稳定解析 PPT/PDF，并保留可追溯来源。 | `content.json`、`element_ir.json`、`source_map.json`、截图、图片资产、parser adapter |
| **2. Understand** | 理解课件在讲什么，每页/每图/每表起什么作用。 | `deck_understanding.json`、`page_understanding.json`、`sections.json`、`deck_brief.json`、图表理解 |
| **3. Write** | 把结构化材料写成可读学习笔记。 | `notes.md`、Lecture-Weave 逐页讲解、teaching enrichment |
| **4. Guard** | 检查保真、覆盖率和学习质量。 | `coverage.json`、`coverage.md`、`content_guard.json`、`quality_report.json` |
| **5. Export** | 发布最终结果和运行报告。 | `notes.zip`、`notes.toc.md`、`notes.docx`、`notes.pdf`、`notes.tex`；复习/考试包由 `study-pack` 另行生成 |

详细说明见 [SlideNote Pipeline](docs/pipeline.zh-CN.md)。

## 用户侧 Preset

顶层 `--preset` 是用户侧工作流入口。现在普通用户只需要理解两个模式：默认 `lecture` 和无 API 的 `local`。

| Preset | 适合场景 | 背后行为 |
| --- | --- | --- |
| `lecture` | 想要“像老师重新讲一遍”的详细讲义。 | 默认启用 LLM、OCR auto、Vision auto、Lecture-Weave、Deck Brief、Content Guard 和 teaching enrichment。 |
| `local` | 没有 API key、离线预览、检查解析是否正常。 | 不调用文本模型、视觉模型或 OCR API，只用本地规则生成基础 Markdown。 |

```powershell
python -m slidenote build lecture.pdf --out outputs\lecture --provider deepseek
python -m slidenote build lecture.pdf --out outputs\local --preset local
```

详细说明见 [用户侧 Preset](docs/presets.zh-CN.md)。

## 起源

SlideNote 来自一个个人的学习困境。

我一直不是那种特别适合“只靠听课”学习的人。有时候老师讲得很快，或者表达方式不太适合我，我在课堂上并不能完全跟上。相比听课，我更喜欢阅读：文字可以反复看，可以停下来想，也可以按照自己的节奏跳转、回看和整理。

但课下直接读 PPT，我又总觉得差点意思。PPT 本质上更像是老师讲课时的提示板，而不是一份真正适合阅读和复习的笔记。很多内容都是零散的，逻辑藏在老师的讲解里，关键知识还经常出现在图、表、流程图、公式截图和页面布局中。

当然，我也试过自己整理笔记，但这件事既耗时间，也很难保证不遗漏。而且手写笔记的字迹和排版有时会让我自己都不太想回头看。

所以我想做一个工具，把课程 PPT/PDF 转换成结构清晰、内容完整、保留图片、可追溯到原页码的课程笔记。它不只是总结课件，而是尽量把展示材料变成真正适合学习的文字材料。

于是就有了 SlideNote。

## 环境与安装

SlideNote 不需要本机 GPU。基础解析只需要 Python 依赖；LLM 改写、OCR 和视觉理解按需配置对应 provider 的 API key。

最低环境：

- Python `3.10` 或更高版本。
- 推荐使用虚拟环境。
- 新手可以直接运行 `.\install.ps1`，然后运行 `.\run_gui.ps1`。
- 本地解析：`python -m pip install -e ".[dev]"`。
- LLM provider：`python -m pip install -e ".[dev,llm]"`。

可选软件：

| 软件 | 用途 |
| --- | --- |
| LibreOffice | 将 `.ppt` / `.pptx` 转 PDF，并在没有 PowerPoint 时生成整页截图。 |
| Microsoft PowerPoint + `pywin32` | Windows 上的 PPTX 整页截图导出路线。 |
| Pandoc | Word 和 LaTeX 导出。 |
| LibreOffice + Pandoc | PDF 导出会优先从 `notes.docx` 转换，中文/CJK 排版更稳。 |

配置指南见 [CONFIG.zh-CN.md](CONFIG.zh-CN.md)。现在 `build` 入口已经简化，provider、OCR、Vision 和缓存细节主要通过强默认和环境变量处理。

## 常用工作流

本地规则草稿：

```powershell
python -m slidenote build path\to\lecture.pptx --out outputs\local --preset local --export markdown-zip
```

教师讲义式笔记：

```powershell
python -m slidenote build path\to\lecture.pdf `
  --out outputs\lecture-notes `
  --provider deepseek `
  --export markdown-zip
```

复习 / 考试包：

```powershell
python -m slidenote build path\to\lecture.pdf `
  --out outputs\lecture-review `
  --provider deepseek
python -m slidenote study-pack outputs\lecture-review --question-count 20
```

纯文本讲义：

```powershell
python -m slidenote build path\to\lecture.pdf `
  --out outputs\text-only `
  --provider deepseek `
  --vision off
```

## 技术文档

README 现在只作为项目首页。细节放到文档中心：

| 主题 | 链接 |
| --- | --- |
| 文档导航 | [docs/index.zh-CN.md](docs/index.zh-CN.md) |
| 五阶段 Pipeline | [docs/pipeline.zh-CN.md](docs/pipeline.zh-CN.md) |
| 用户侧 Preset | [docs/presets.zh-CN.md](docs/presets.zh-CN.md) |
| Coverage、Content Guard、Quality Report、复习/考试包 | [docs/quality-and-guard.zh-CN.md](docs/quality-and-guard.zh-CN.md) |
| Element IR、Source Map、图片资产 | [docs/ir-and-source-map.zh-CN.md](docs/ir-and-source-map.zh-CN.md) |
| LLM Provider、OCR、Vision、缓存与成本 | [docs/providers-and-cost.zh-CN.md](docs/providers-and-cost.zh-CN.md) |
| 实验性 Claude Backend | [docs/claude-backend.zh-CN.md](docs/claude-backend.zh-CN.md) |
| 路线图设计笔记 | [docs/roadmap/extension-notes.zh-CN.md](docs/roadmap/extension-notes.zh-CN.md) |

主输出是 `notes.md`。如果要把 Markdown 笔记发给别人，推荐导出 `notes.zip`，里面包含 `notes.md` 和 `notes.assets/` 图片资源。根据选项不同，SlideNote 还会写出 `content.json`、`deck_understanding.json`、`page_understanding.json`、`element_ir.json`、`source_map.json`、`coverage.md`、`quality_report.json`、`review.md`、`exam.md`、`exam.json`、`exam.html`、`notes.docx`、`notes.pdf` 等报告和导出文件。

## 未来展望

SlideNote 带着一个乐观前提在建设：未来 AI 会更强、更快、更便宜，也会更容易通过成熟的开源智能体框架来组织复杂工作流。如果这件事发生，SlideNote 不应该只是“用更低成本跑同一套 prompt”，而应该让项目上限被真正抬高。

以 DeepSeek 这类强调性价比、可获得性和开放生态的模型 / 服务为例，它让人看到一种很值得期待的方向：当高质量 API 的价格、速度和可用性继续改善，多 pass 的高质量流程就不再只是少数重型场景才能负担的奢侈品。SlideNote 可以把更深的课件理解、逐页视觉推理、教师讲义式写作、teaching enrichment、coverage repair、考试题生成、错题复盘和来源校验变成更自然的默认能力。

这件事之所以重要，是因为 SlideNote 的难点不只是“模型能不能总结一页 PPT”。真正难的是在解析、视觉理解、写作、图文锚定、质量检查和局部修订之间保持协调，同时不丢失可追溯性。所以项目会持续投入 `element_ir.json`、`source_map.json`、coverage、artifact registry、preset、cache key 和 review/exam 学习包这些工程结构。它们让 SlideNote 能吃到未来模型进步的红利，而不是被某一个模型、某一家 provider 或某一种 agent runtime 绑死。

长期愿景是：

> SlideNote 从课件转换器，成长为课程学习操作系统。

在这个愿景里，课件、教材、个人笔记、图表、公式、测验、错题和局部修订都处在同一条可检查、可追溯、可复习的学习工作流里。

## 设计原则

SlideNote 不走 `PPT -> LLM -> 总结` 的捷径，而是：

```text
PPT/PDF -> 结构化解析 -> 内容清单 -> 笔记生成 -> 覆盖率校验 -> 导出
```

本地规则草稿只负责把结构化内容“保底写出来”，方便调试解析和覆盖率。正式笔记默认使用 `lecture` preset，但覆盖率检查仍然依靠元素 ID 做硬校验，避免模型把细节悄悄总结掉。

## 许可证

SlideNote 采用双许可证结构：

- 源代码使用 GNU Affero General Public License v3.0 or later（`AGPL-3.0-or-later`）。完整文本见 [LICENSE](LICENSE)。
- 文档和示例教学材料使用 Creative Commons Attribution 4.0 International（`CC BY 4.0`）。完整文本见 [LICENSES/CC-BY-4.0.txt](LICENSES/CC-BY-4.0.txt)。

我们选择 AGPL，是因为 SlideNote 的核心价值不只是调用某个模型，而是课件解析、视觉理解、插图处理、笔记生成和学习包整理这一整套流程。我们希望这个能力保持开放：任何人都可以免费使用、学习、修改和改进它；如果有人基于 SlideNote 做了修改版并发布，或把修改版作为网络服务提供给用户，也应该把对应源码开放出来，让社区能看到并受益于这些改进。

AGPL 不禁止商业使用，也不限制个人、学生、老师或团队本地部署使用。它主要要求是：当你分发修改版，或以网络服务形式提供修改版时，需要遵守 AGPL 的源码开放义务。用户用 SlideNote 生成的笔记、复习资料和其它输出内容，不会因为使用了 SlideNote 就自动变成 AGPL。

SlideNote 名称、logo 和其它品牌素材不授权作独立复用。具体范围见 [NOTICE](NOTICE)。

## 致谢

- SlideNote 可选的 Review / Exam 复习包工作流在产品思路上受到 [WUBING2023/ExamPass-Assistant](https://github.com/WUBING2023/ExamPass-Assistant) 以及扩展版 [MIKUZ12/ExamPass-Assistant](https://github.com/MIKUZ12/ExamPass-Assistant) 启发。SlideNote 没有复用它们的代码、模板、prompt 或素材。
- `experiment/claude-backend` 分支中将整体方案迁移到 Claude 式智能体工作流的思路受到 [koriyoshi2041](https://github.com/koriyoshi2041) 启发。
- 感谢 [hongzuoj-pixel](https://github.com/hongzuoj-pixel) 对 GUI 开发的贡献。
- 感谢 [MOm0-000](https://github.com/MOm0-000) 对测试工作的贡献。
- SlideNote 的 parser adapter、统一文档 IR 和外部解析器路线参考了 [Microsoft MarkItDown](https://github.com/microsoft/markitdown)、[Docling](https://github.com/docling-project/docling)、[Marker](https://github.com/datalab-to/marker)、[MinerU](https://github.com/opendatalab/MinerU) 和 [Unstructured](https://github.com/Unstructured-IO/unstructured) 等项目的思路。
- SlideNote 后续的检索、来源追踪和生成后质检方向也参考了 [RAGFlow](https://github.com/infiniflow/ragflow) 这类深度文档理解 / RAG 系统。这些项目是思路参考，不代表已作为依赖打包进 SlideNote。

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
