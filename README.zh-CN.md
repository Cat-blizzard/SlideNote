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
  <a href="CONFIG.zh-CN.md">配置参考</a> |
  <a href="ROADMAP.zh-CN.md">路线图</a>
</p>

---

## 目录

- [快速开始](#快速开始)
- [模式与工作流程](#模式与工作流程)
- [结果与复核](#结果与复核)
- [可选 GUI](#可选-gui)
- [教材分块](#教材分块)
- [起源](#起源)
- [配置与文档](#配置与文档)
- [许可证](#许可证)
- [致谢](#致谢)

## 快速开始

Windows / PowerShell 用户可以运行：

```powershell
git clone https://github.com/Cat-blizzard/SlideNote.git
cd SlideNote
.\install.ps1
.\run_gui.ps1
```

安装脚本会创建 `.venv`、安装 GUI 和模型相关依赖，并运行环境检查。GUI 支持在页面中临时填写 API key。先用本地模式检查课件能否解析、笔记能否生成：

```powershell
python -m slidenote build path\to\lecture.pdf --out outputs\local --preset local --export markdown-zip
```

需要模型辅助讲解和视觉理解时，再配置对应的 API key。例如，使用 DeepSeek 文本模型和默认视觉模型：

```powershell
$env:DEEPSEEK_API_KEY="..."
$env:DASHSCOPE_API_KEY="..."
python -m slidenote build path\to\lecture.pdf --out outputs\lecture --provider deepseek --export markdown-zip
```

主输出在 `outputs\lecture\notes.md`。模型生成的内容仍应对照课件核查。

手动安装可用 `python -m pip install -e "."`（本地模式）或 `python -m pip install -e ".[llm]"`（模型模式）；运行 GUI 时，`.\run_gui.ps1` 会按需补装 GUI 依赖。`dev` extra 主要用于项目测试，不是普通使用的前提。

## 模式与工作流程

| 模式 | 用途 | 行为 |
| --- | --- | --- |
| 默认 `lecture` | 生成模型辅助的详细讲义 | 使用文本模型，并按配置执行 OCR、视觉理解和 Lecture-Weave 写作；质量取决于课件与模型输出。 |
| `local` | 离线预览与解析检查 | 不调用文本、视觉或 OCR API；用本地规则生成基础笔记。 |

`--vision off` 只关闭视觉模型调用，并不禁止笔记引用或导出图片；需要调整 OCR 时可用 `--ocr off|auto|all`。参数及预设说明见 [配置参考](CONFIG.zh-CN.md)。

```text
Ingest -> Understand -> Write -> Guard -> Export
```

| 阶段 | 主要工作 | 主要产物 |
| --- | --- | --- |
| **Ingest** | 解析 PPT/PDF，提取页面、截图和图片资产。 | 页面与素材，供后续阶段使用 |
| **Understand** | OCR、视觉与结构理解，并整理结构化内容。 | `content.json`、`deck_understanding.json`、`page_understanding.json`；按配置生成 `content_guard.json` 等 |
| **Write** | 生成可阅读的学习笔记。 | `notes.md` |
| **Guard** | 生成来源映射、覆盖率和质量诊断。 | 最终 `element_ir.json`、`source_map.json`、`coverage.json`、`coverage.md`、`quality_report.json` |
| **Export** | 按需导出分享或阅读格式。 | `notes.zip`、`notes.docx`、`notes.pdf` 等 |

实现细节见 [Pipeline 文档](docs/pipeline.zh-CN.md)。

## 结果与复核

`notes.md` 是主输出。选择 `--export markdown-zip` 后会生成 `notes.zip`，其中包含笔记；笔记引用了图片时，还会包含 `notes.assets/` 中的相应文件。Word、PDF 等格式需要相应的外部工具，见 [配置参考](CONFIG.zh-CN.md)。复习和考试材料可在构建后单独生成：

```powershell
python -m slidenote study-pack outputs\lecture --question-count 20
```

覆盖率通过元素 ID 和正文标记提示可能漏写的来源；质量分数主要是启发式诊断。它们不能证明解释准确、内容完整或题目有效。分享笔记前，建议对照课件检查关键事实、图文位置、公式表格及导出版式。

## 可选 GUI

SlideNote Studio 提供上传 PPT/PDF、临时填写 API key、选择模式、查看进度和报告、逐页查看截图与笔记、下载结果等操作：

```powershell
.\run_gui.ps1
```

详情见 [GUI 使用说明](gui/README_GUI.zh-CN.md)。

## 教材分块

`textbook-index` 可把 PDF 教材解析为带目录与章节映射的分块语料，供后续检索功能使用。目前它不创建向量索引、不提供检索，也不会自动参与笔记生成。

```powershell
python -m slidenote textbook-index path\to\textbook.pdf --out outputs\textbook --ocr auto
```

电子版 PDF 可尝试 `--ocr off`；`auto` 会对扫描页或低文本页使用 OCR。

## 起源

我更习惯按自己的节奏阅读和复习，但课堂 PPT 往往只是讲课提示：逻辑分散，关键内容还可能藏在图、表、公式和老师的讲解里。课后从头整理笔记很耗时，也容易漏掉细节。

SlideNote 因此尝试把课件整理为有结构、保留图片、能回看来源的学习笔记，并用覆盖报告提示需要复核的地方。目标是让笔记更适合阅读和复习，而不是替代对原始课件的判断。

## 配置与文档

SlideNote 需要 Python 3.10 或更高版本，本地模式不需要 GPU。PPT 转换和整页截图可能需要 LibreOffice 或 PowerPoint；Word、PDF、LaTeX 导出可能需要 Pandoc 和 LibreOffice。Windows 安装脚本见上文，Linux/macOS 可使用相同的 `python -m slidenote ...` 命令。

更多内容见[文档中心](docs/index.zh-CN.md)、[配置参考](CONFIG.zh-CN.md)和[路线图](ROADMAP.zh-CN.md)。长期希望把课件、教材、复习题和个人笔记连成可追溯的学习流程；具体开发优先级以路线图为准。

## 许可证

SlideNote 采用双许可证结构：

- 源代码使用 GNU Affero General Public License v3.0 or later（`AGPL-3.0-or-later`）。完整文本见 [LICENSE](LICENSE)。
- 文档和示例教学材料使用 Creative Commons Attribution 4.0 International（`CC BY 4.0`）。完整文本见 [LICENSES/CC-BY-4.0.txt](LICENSES/CC-BY-4.0.txt)。

我们选择 AGPL，是因为 SlideNote 的核心价值不只是调用某个模型，而是课件解析、视觉理解、插图处理、笔记生成和学习包整理这一整套流程。我们希望这个能力保持开放：任何人都可以免费使用、学习、修改和改进它；如果有人基于 SlideNote 做了修改版并发布，或把修改版作为网络服务提供给用户，也应该把对应源码开放出来，让社区能看到并受益于这些改进。

AGPL 不禁止商业使用，也不限制个人、学生、老师或团队本地部署使用。它主要要求是：当你分发修改版，或以网络服务形式提供修改版时，需要遵守 AGPL 的源码开放义务。用户用 SlideNote 生成的笔记、复习资料和其它输出内容，不会因为使用了 SlideNote 就自动变成 AGPL。

SlideNote 名称、logo 和其它品牌素材不授权作独立复用。具体范围见 [NOTICE](NOTICE)。

## 致谢

- SlideNote 可选的 Review / Exam 复习包工作流在产品思路上受到 [WUBING2023/ExamPass-Assistant](https://github.com/WUBING2023/ExamPass-Assistant) 以及扩展版 [MIKUZ12/ExamPass-Assistant](https://github.com/MIKUZ12/ExamPass-Assistant) 启发。SlideNote 没有复用它们的代码、模板、prompt 或素材。
- 感谢 [hongzuoj-pixel](https://github.com/hongzuoj-pixel) 对 GUI 开发的贡献。
- 感谢 [MOm0-000](https://github.com/MOm0-000) 对测试工作的贡献。
- SlideNote 的 parser adapter、统一文档 IR 和外部解析器路线参考了 [Microsoft MarkItDown](https://github.com/microsoft/markitdown)、[Docling](https://github.com/docling-project/docling)、[Marker](https://github.com/datalab-to/marker)、[MinerU](https://github.com/opendatalab/MinerU) 和 [Unstructured](https://github.com/Unstructured-IO/unstructured) 等项目的思路。
- SlideNote 后续的检索、来源追踪和生成后质检方向也参考了 [RAGFlow](https://github.com/infiniflow/ragflow) 这类深度文档理解 / RAG 系统。这些项目是思路参考，不代表已作为依赖打包进 SlideNote。
- 感谢 [LEO690201](https://github.com/LEO690201) 为 SlideNote 修复 bug、提升项目稳定性所作的贡献。
- SlideNote 的开发也得到了 Codex、Claude Code 和 DeepSeek Harness 在代码分析、实现与调试方面的辅助。所有 AI 辅助改动仍须经过维护者审核和项目测试。
