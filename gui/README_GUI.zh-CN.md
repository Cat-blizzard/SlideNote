# SlideNote Studio GUI

[English](README_GUI.md)

SlideNote Studio 是一个基于 Streamlit 的图形界面。它包装现有的 `python -m slidenote build` 流程，不替代也不修改核心解析、OCR、视觉和 LLM 笔记生成 pipeline。

## 它能做什么

- 不用写命令行，直接上传 PPTX / PPT / PDF。
- 在页面里临时填写 API key；key 会通过子进程环境变量传给本次运行，不写入源码，也不放进命令行参数。
- 选择常用工作流预设：
  - Fast API draft：快速 API 草稿
  - Balanced study notes：平衡版学习笔记
  - Quality detailed notes：高质量详细笔记
  - Local safe preview：本地安全预览
- 控制运行速度和成本：
  - `--speed-mode`
  - 总并发 `--concurrency`
  - LLM / Vision / OCR / Figure 四类 API 独立并发
  - shared global cache
  - OCR / Vision 最大目标数量
  - `direct` 或 `lecture-weave` 笔记策略
  - deck brief / content guard 开关
  - 按页 `--refresh-pages` 局部刷新
- 更直观的运行状态卡片：Text API / Vision API 会显示 `Off`、`Missing key` 或 `Ready`，不会再显示难懂的 `n...`。
- Doctor 面板：复用 `slidenote doctor` 的环境检查，展示 Python、依赖包、可选工具和 API key readiness。
- Live run：显示进度、ETA 估算、当前阶段和控制台日志。
- 结果审阅工作台：
  - quality / coverage 总览
  - missing element 修复队列
  - Page explorer：联动查看原页截图、解析元素、页面类型和对应笔记
  - 页面类型人工修正清单 `page_modalities.overrides.json`
  - token/cost dashboard
  - `notes.md`、`coverage.md`、`run_summary.json`、usage 文件和原始输出文件
- 支持默认 `gui_runs/outputs` 工作区，也支持自定义本地输出目录。
- 页面内一键下载 `notes.zip`、`notes.md`、`coverage.md`、`cost_report.md` 或完整结果 ZIP；分享 Markdown 时优先发 `notes.zip`，笔记和图片资源都在压缩包里。
- 可选生成并下载复习/自测包：`review.md`、`exam.md`、`exam.json` 和可交互批改的 `exam.html`。

## 安装

在仓库根目录下，推荐直接运行：

```powershell
.\install.ps1
```

手动安装方式：

```bash
python -m pip install -e .
python -m pip install -r requirements-gui.txt
```

如果要使用 LLM provider，建议安装 LLM 依赖：

```bash
python -m pip install -e ".[llm]"
```

也可以一次安装开发、LLM 和 GUI 依赖：

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

启动后在浏览器中打开 Streamlit 给出的本地地址，上传课件并选择运行参数即可。

## 第一次安全测试

建议先用 **Local safe preview**：

- Use text LLM: off
- OCR mode: off
- Vision mode: off
- Save: Default workspace 或 Custom folder

这个模式只测试本地解析和输出展示，不会调用 API，也不会产生 token 成本。

## API key

GUI 会把 API key 通过子进程环境变量传给本次运行，不会把 key 放进命令行参数里。这样用户不需要手动配置终端环境变量，也能避免 key 暴露在进程 argv 中。

不要把 API key 提交到 GitHub。

## 加速建议

第一次测试 API 建议用 **Fast API draft**，并先关闭 OCR 和 Vision。

想减少运行时间，可以优先这样调：

1. 快速预览时使用 `direct`，需要高质量连贯讲义时再用 `lecture-weave`。
2. 使用 `vision=auto`，不要默认使用 `vision=all`。
3. 使用 `ocr=auto`，只有扫描版 PDF 或低文本页很多时再考虑 `ocr=all`。
4. 测试时把 `Vision max targets` 和 `OCR max targets` 设小一点。
5. 保持缓存开启，并使用 shared global cache。
6. API 服务商限流允许时，把并发提高到 3 到 6；如果遇到限流，先降到 2 或 3。
7. 如果只改少数页面，用 `Refresh only these pages` 做局部重跑，不要整份课件重跑。

## Token 和成本报告

构建成功后，GUI 会尝试生成并展示：

- `cost_report.json`
- `cost_report.md`
- `cost_dashboard.html`

成本估算会读取 `pricing.template.json`。模板里的价格需要你按官方价格页面手动更新；默认价格可能是 0，所以默认报告更适合作为统计框架，而不是准确账单。


## Review / Exam 复习包

GUI 侧边栏的 **5. Review / Exam** 可以在 `notes.md` 完成后继续生成复习包：

- `review.md`：带重要程度标签、逻辑链、易错点和页码来源的考点清单。
- `exam.md`：带答案和解析的自测题。
- `exam.json`：结构化题目数据，便于后续接题库或 Anki。
- `exam.html`：可交互自测页面，选择/判断题支持一键批改。

`local` 模式不调用 API；`auto` / `llm` 模式会复用文本 provider，题目和解析通常更强。

## 导出 Markdown / Word / PDF / LaTeX

GUI 侧边栏的 **6. Exports** 可以直接勾选额外导出格式：

- `notes.toc.md`：带目录 Markdown，不需要 Pandoc。
- `notes.zip`：Markdown 笔记包，包含 `notes.md` 和 `notes.assets/`，不需要 Pandoc。
- `notes.docx`：Word 文档，需要 Pandoc。
- `notes.pdf`：PDF 讲义，优先用 LibreOffice 将 `notes.docx` 转成 PDF，中文/CJK 和图片排版更稳定；需要 Pandoc + LibreOffice。
- `notes.tex`：LaTeX 源码，需要 Pandoc。

导出完成后，结果区会出现 **Exports** 标签页，并在顶部下载区显示可用的 Markdown ZIP / Word / PDF / LaTeX 下载按钮。所有导出状态会写入 `export_report.json`。如果 Pandoc 或 LibreOffice 不在 PATH 中，GUI 会在运行前提示安装命令，build 仍可继续生成基础 `notes.md`。
