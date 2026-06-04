# SlideNote Studio GUI

[English](README_GUI.md)

SlideNote Studio 是一个基于 Streamlit 的图形界面。它包装 `python -m slidenote build` 和 `python -m slidenote study-pack`，让普通用户不用面对大量底层参数。

## 它能做什么

- 上传 PPTX / PPT / PDF。
- 选择两个工作流之一：
  - `Lecture quality`：高质量讲义默认流程，需要 API key。
  - `Local preview`：本地预览，不调用 API。
- 在页面里临时填写 Text / Vision / OCR API key；key 只通过本次子进程环境变量传入，不写进命令行。
- 选择是否导出 `notes.zip`、目录 Markdown、Word、PDF 或 LaTeX。
- 进度、ETA、Doctor、用量和成本信息收在紧凑的诊断区里。
- 在 Notes workspace 基于已有输出目录生成复习包：`review.md`、`exam.md`、`exam.json`、`exam.html` 等。
- 下载 `notes.zip`、`notes.md`、`coverage.md`、导出文件或完整结果 ZIP。

分享 Markdown 时优先下载 `notes.zip`。压缩包里包含 `notes.md` 和 `notes.assets/`，别人解压后图片才能正常显示。

## 安装

推荐在仓库根目录运行：

```powershell
.\install.ps1
```

手动安装：

```bash
python -m pip install -e ".[dev,llm,gui]"
```

## 启动

```powershell
.\run_gui.ps1
```

手动启动：

```bash
streamlit run gui/app.py
```

## 第一次测试

先选择 **Local preview**，上传一个课件并运行。这个模式只测试本地解析和基础输出，不会调用 API。第一次验证通过的标准是 Notes workspace 能看到 `notes.md` 和可分享的 `notes.zip`。

正式生成时选择 **Lecture quality**，填写 Text API key；如果课件图表多，保留 Vision=`auto` 并填写 Qwen/DashScope key。扫描版 PDF 可能还需要 OCR key 和 secret。

## 复习包

`build` 完成后在 Notes workspace 打开 **Study pack**，点击 **Generate study pack**。GUI 会对已有输出目录运行：

```powershell
python -m slidenote study-pack <输出目录> --question-count 12
```

如果 Text provider key 可用，复习包会用 LLM 增强；否则自动回退本地规则。

## 导出

左侧 **Run** 面板里的 **Exports** 可以勾选：

- `notes.zip`：Markdown 笔记包，包含图片资源，不需要 Pandoc。
- `notes.toc.md`：带目录 Markdown，不需要 Pandoc。
- `notes.docx`：Word 文档，需要 Pandoc。
- `notes.pdf`：PDF 讲义，需要 Pandoc + LibreOffice。
- `notes.tex`：LaTeX 源码，需要 Pandoc。

导出状态写入 `export_report.json`。如果 Pandoc 或 LibreOffice 不在 PATH 中，GUI 会在运行前提示安装命令。
