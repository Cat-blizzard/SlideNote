# 路线图设计笔记

这份文档承接旧版 ROADMAP 中较长的模块设计说明。它不是优先级列表；真正的优先级以 [ROADMAP.zh-CN.md](../../ROADMAP.zh-CN.md) 为准。

## 上下文、覆盖率与章节切分

### 上下文策略

SlideNote 需要在 page / section / document 三种上下文之间取舍：

- page：来源最清晰，适合调试和局部刷新，但容易有逐页拼接感。
- section：默认更适合正式笔记，逻辑更连贯，请求数也比逐页更少。
- document：适合短文件和全局摘要，长文件容易超上下文。

推荐路线是：逐页理解负责证据和局部讲解，section weave 负责最终正文。

### 覆盖率与逻辑重组

Coverage 的价值是“不漏”，不是决定正文形状。高质量笔记应该先按知识逻辑组织，再用 coverage 修补遗漏。

后续增强方向：

- coverage 报告按章节聚合。
- missing item 只触发局部修复。
- source marker 不污染正文阅读。
- 图表 coverage 单独显示插入位置、解释状态和复查建议。

### 章节切分

章节计划可以来自：

- PPT 标题页、目录页和页面标题。
- 版式变化和主题词变化。
- LLM 章节识别。
- 用户手动修正。

未来 GUI 可以支持用户拖动章节边界，再局部刷新受影响 section。

## 教材、知识库、GUI 与交互编辑

### 教材 / 参考书作为知识库

教材不应该直接替代课件内容，而应该作为可追溯的补充背景。推荐方式：

- 先把教材切成可引用片段。
- 只在课件证据不足但需要背景解释时引用。
- 明确区分“课件原文”和“帮助理解的背景知识”。

### GUI

GUI 的核心价值不是替代 CLI，而是降低复杂工作流的进入门槛：

- 上传文件和配置 API key。
- 选择 `fast|faithful|lecture` preset。
- 查看进度、ETA、token/cost。
- 逐页查看截图、元素、coverage、来源。
- 下载 notes、Word、PDF、LaTeX 和完整 ZIP。

### 交互式编辑与局部 revise

长期方向是让用户可以对某一页、某一节或某个概念发起局部修订，而不是每次重跑整份课件。

需要依赖：

- `element_ir.json` 的稳定元素结构。
- `source_map.json` 的笔记块映射。
- artifact registry 知道哪些阶段需要刷新。
- cache key 能区分页面、章节和写作参数。

## 进度、加速与成本调度

### 进度报告

`progress.json` 和 `run_summary.json` 应该持续承担 GUI 和 CLI 的可观测性入口：

- 当前阶段。
- 已完成 / 总任务数。
- ETA。
- 缓存命中。
- API 调用和错误摘要。
- 生成产物列表。

### 加速策略

主要耗时来源：

- OCR。
- Vision。
- LLM 逐页讲解。
- section weave。
- teaching enrichment 和 coverage repair。
- 导出转换。

推荐原则：

- 优先用缓存和并发提速。
- 不要默认关闭质量阶段来提速。
- 对大文件使用 `--global-cache-dir`。
- 用 `--refresh-pages` 局部刷新低质量页面。
- 并发从 `2` 或 `3` 开始，遇到限流再降低。

### 分阶段运行

未来可以把 build 拆成更显式的阶段命令，例如：

```text
parse -> understand -> write -> guard -> export
```

这会让 GUI、Agent backend 和失败恢复更清晰。

## 视觉、学科策略与 OCR

### 图片取舍

图片不是越多越好。需要区分：

- 装饰图、logo、背景碎片。
- 真正承载知识的图表、流程图、截图、公式图片。
- 需要裁剪的组合图。
- 需要 OCR 的扫描页。

后续质量提升重点是让图片参与正文逻辑，而不是只生成 caption。

### 学科策略

不同学科需要不同 prompt 和质量检查：

- 数学：公式变量、推导步骤、条件和常见错法。
- 医学：术语、流程、图像标注、鉴别点。
- 计算机：代码、架构图、输入输出、边界条件。
- 文科 / 商科：概念框架、案例、对比表、论证关系。

未来可以增加 domain profile，但不要让它变成太复杂的用户入口。用户侧仍应优先使用 preset。

### OCR

OCR 应继续和 Vision 分离：

- OCR 负责读文字。
- Vision 负责解释图像关系。
- 两者分开缓存，避免换笔记模型时重复 OCR。

## 导出、模板与 Agent 工作流

### 导出与模板

导出层应保持确定性：

- Markdown 是主输出。
- Word / PDF / LaTeX 是发布格式。
- `export_report.json` 记录成功和失败原因。
- 模板系统可以后续支持课程名、封面、页眉页脚、引用样式和主题。

### 多 Agent 工作流

多 Agent 不应该过早替代 pipeline。更适合的方向是让 agent 调用明确阶段：

- Parser agent 不决定内容，只调解析工具。
- Understanding agent 生成 deck/page understanding。
- Writer agent 写 section notes。
- Guard agent 检查 coverage、来源和质量。
- Repair agent 做局部修复。

共享状态应该是 artifact registry、IR、source map 和报告文件，而不是靠自由文本聊天记录。

## 学习资料衍生输出

后续可以继续扩展：

- `review.md`：考点清单、图表速查、易错点。
- `exam.md` / `exam.html`：自测题和可交互批改。
- `wrong_answer_review_prompt.md`：错题复盘。
- 期末整卷模式：跨章节组卷、答案解析、来源页引用。
- `section_study_pack.json` / `exam_review_pack.json`：给 GUI 和后续 agent 使用的结构化学习包。

题目质量应该进入 `quality_report.json`，包括题干清晰度、答案唯一性、解析覆盖、干扰项质量、来源页覆盖和图文就地嵌入。

## 来源显示与课程级整合

来源显示需要平衡阅读体验和可追溯性：

- 默认隐藏 source marker，保留 `source_map.json`。
- 调试时允许 inline source。
- 导出时可选脚注或页码提示。

课程级整合方向：

- 多 PPT 汇总成一门课的知识库。
- 支持持续输入和增量更新。
- 接入个人笔记，但明确区分原课件、个人补充和 AI 解释。
- 支持更多输入格式，未来通过 parser adapter 接入 Docling、Marker、MinerU 等外部解析器。

## 语言与重写可能性

Python 仍适合快速迭代解析、LLM 调度和 GUI。未来如果性能瓶颈集中在某些确定性模块，可以考虑 Rust 或其它语言重写局部组件，例如：

- 大量页面的布局计算。
- 图片预处理和裁剪。
- artifact 扫描和索引。

但在当前阶段，优先级仍是产品结构、质量闭环和可追溯工作流。
