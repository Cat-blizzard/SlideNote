# SlideNote Pipeline

SlideNote 的目标不是把所有功能堆在 CLI 参数里，而是把底层能力收束成一条产品流水线：

```text
Ingest -> Understand -> Write -> Guard -> Export
```

底层模块可以保持细粒度，方便缓存、调试和局部刷新；用户侧和 LLM 工作流应该看到清楚的阶段边界。

## 阶段总览

| 阶段 | 目标 | 典型产物 |
| --- | --- | --- |
| Ingest | 把 PPT/PDF 变成稳定、可追溯、可复现的结构化材料。 | `content.json`、`element_ir.json`、`source_map.json`、截图、图片资产、parser adapter |
| Understand | 理解课件主题、章节结构、页面角色、图表含义和关键元素。 | `deck_understanding.json`、`page_understanding.json`、`sections.json`、`deck_brief.json`、`semantic_layout.json`、`table_understanding.json`、`figure_grounding.json` |
| Write | 生成可读学习笔记，而不是机械逐页搬运。 | `notes.md`、`page_notes.json`、`weave_report.json`、`teaching_enrichment.json` |
| Guard | 检查是否漏掉关键内容、是否有来源、是否像讲义。 | `coverage.json`、`coverage.md`、`content_guard.json`、`quality_report.json` |
| Export | 输出阅读和复习材料。 | `notes.toc.md`、`notes.docx`、`notes.pdf`、`notes.tex`、`review.md`、`exam.html` |

## 什么不交给 LLM

这些部分应该尽量保持确定性：

- 元素 ID 分配。
- 图片路径和资产目录。
- `source_map.json` 生成。
- 缓存 key、并发控制和成本统计。
- Word、PDF、LaTeX 等导出转换。

这些能力需要稳定、可复现、方便调试，不适合让生成模型临场决定。

## 什么适合交给 LLM

这些部分需要语义判断，适合由 LLM 或 Vision LLM 参与：

- 章节识别和页面角色判断。
- 图表、流程图、公式截图的含义解释。
- 讲义式正文生成。
- 背景直觉、例子、易错点、自测题。
- 语义层面的遗漏修复和质量审阅。

SlideNote 的原则是：本地规则管边界和证据，模型管理解和表达。

## 当前流水线产物

一个完整构建可能包含：

```text
content.json
page_modalities.json
element_ir.json
source_map.json
semantic_layout.json
sections.json
deck_brief.json
deck_understanding.json
page_understanding.json
table_understanding.json
image_importance.json
figure_grounding.json
notes.md
coverage.md
quality_report.json
run_summary.json
notes.assets/
figures/
screenshots/
```

不是每次运行都会生成所有文件。具体取决于 `--preset`、`--use-llm`、`--vision`、`--ocr`、`--review-mode`、`--exam-mode` 和导出选项。

## 稳定认知包

`deck_understanding.json` 聚合 Deck Brief、章节计划、页面角色、核心问题、关键术语、跨页关联、重要表格和高价值图示。它是全局导航入口，不替代底层 `sections.json` / `deck_brief.json` 调试产物。

`page_understanding.json` 聚合每页的 section、role、modality、key points、文本摘要、表格结论、图示解释、semantic groups 和 content guard required items。它适合作为 GUI、Agent backend 和局部 revise 的逐页稳定入口。

`quality_report.json` 继续负责质量审阅：覆盖率、机械逐页复述风险、解释深度、图表整合和题目质量。

## Parser Adapter

解析阶段通过 adapter 返回统一 `Deck` 数据模型。`--parser auto` 默认优先使用内置 PPTX/PPT/PDF 解析；`--parser docling|marker|mineru` 会走外部 CLI adapter，并把外部 JSON / Markdown 输出归一为 `Deck`。核心 build pipeline 只依赖 `Deck`，不直接绑定 Docling、Marker、MinerU 或任何单一解析库。
