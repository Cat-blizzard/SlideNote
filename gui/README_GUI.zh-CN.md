# SlideNote Studio GUI

[English](README_GUI.md)

SlideNote Studio 是一个基于 Streamlit 的图形界面。它包装现有的 `python -m slidenote build` 流程，不替代也不修改核心解析、OCR、视觉和 LLM 笔记生成 pipeline。

## 它能做什么

- 不用写命令行，直接上传 PPTX / PPT / PDF。
- 在页面里临时填写 API key；key 不会写入源码。
- 选择常用工作流预设：
  - Fast API draft：快速 API 草稿
  - Balanced study notes：平衡版学习笔记
  - Quality detailed notes：高质量详细笔记
  - Local safe preview：本地安全预览
- 控制运行速度和成本：
  - 总并发
  - LLM / Vision / OCR / Figure 四类 API 独立并发
  - 全局缓存目录
  - OCR / Vision 最大目标数量
  - `direct` 或 `lecture-weave` 笔记策略
  - deck brief / content guard 开关
- 运行时查看 `progress.json` 进度。
- 在页面里预览 `notes.md`、`coverage.md`、`run_summary.json`、usage 文件和 token/cost 报告。

## 安装

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

```bash
streamlit run gui/app.py
```

启动后在浏览器中打开 Streamlit 给出的本地地址，上传课件并选择运行参数即可。

## API key

GUI 会把 API key 通过子进程环境变量传给本次运行，不会把 key 放进命令行参数里。这样用户不需要手动配置终端环境变量，也能避免 key 暴露在进程 argv 中。

不要把 API key 提交到 GitHub。

## 加速建议

第一次测试建议使用 **Local safe preview** 或 **Fast API draft**。

想减少运行时间，可以优先这样调：

1. 快速预览时使用 `direct`，需要高质量连贯讲义时再用 `lecture-weave`。
2. 使用 `vision=auto`，不要默认使用 `vision=all`。
3. 使用 `ocr=auto`，只有扫描版 PDF 或低文本页很多时再考虑 `ocr=all`。
4. 测试时把 `Vision max targets` 和 `OCR max targets` 设小一点。
5. 保持缓存开启，并使用 shared global cache。
6. API 服务商限流允许时，把并发提高到 3 到 6；如果遇到限流，先降到 2 或 3，不要直接关闭质量阶段。

## Token 和成本报告

构建成功后，GUI 会尝试生成：

- `cost_report.json`
- `cost_report.md`
- `cost_dashboard.html`

成本估算会读取 `pricing.template.json`。模板里的价格需要你按官方价格页面手动更新；默认价格可能是 0，所以默认报告更适合作为统计框架，而不是准确账单。
