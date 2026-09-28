# Coverage、Content Guard 与学习质量

SlideNote 的质量诊断分两层：

1. 结构检查：关键元素是否在可见正文中出现，来源标记能否关联到原始元素。
2. 启发式检查：笔记的章节、解释、图表引用、例子、自测和易错点是否具备讲义特征。

Coverage 在 Write 之后运行，适合作为漏项提示和人工复核入口。覆盖率高只能说明结构上的来源关联较完整，不能证明讲解正确或没有编造事实。针对缺失项进行局部修补是后续目标；当前自动补漏仍需校验候选稿，不能把它视为独立的逐项语义验证。

## Coverage

Coverage 负责回答：

- 关键文本、表格、图片是否被笔记覆盖。
- 内容是否只藏在 source marker 里，而没有进入可见正文。
- 哪些页或元素需要人工复查。

常见产物：

```text
coverage.json
coverage.md
```

`coverage.md` 面向人读，适合快速看哪些页有风险。`coverage.json` 面向 GUI、测试和后续自动修复。

## Content Guard

Content Guard 在 Understand 阶段先找出“必须解释”的学习内容，再把它们交给写作和修复阶段；Guard 阶段会把最终覆盖状态写回 `content_guard.json`。未启用时不会生成该文件。

它会优先关注：

- 定义、公式、条件、结论。
- 表格中的关键对比和结论。
- OCR 识别出的重要文本。
- 视觉摘要中的核心信息。
- 非装饰图片和需要解释的图表。

典型产物：

```text
content_guard.json
```

启用 LLM 时，Content Guard 可以结合模型判断页面角色和元素学习价值；未启用 LLM 时，也会保留本地启发式检查。

自动补漏会先检查候选修订，再决定是否替换原稿。候选必须至少补上一项缺失的必讲内容，同时保留原有元素的溯源与正文覆盖和已有 Markdown 图片链接。候选正文字符数不得低于原稿的 80%（不计来源注释、图片链接、标题和空白等），该下限用于发现明显缩短，并不代表保留了相同比例的原文。空结果、覆盖退化、明显缩短、模型明确报告截断或修补调用失败时，均保留修补前的原稿；缓存命中的修补结果也执行这些检查。

`content_guard.json` 的 `repairs` 会记录 `accepted`、`rejection_reasons` 和候选稿检查结果。`resolved_items` / `unresolved_items` 始终对应最终采用的正文；被拒绝的候选稿不会计为修补成功。运行摘要会提示 `content_guard_repair_rejected`。这些保护用于防止修补退化，不代替语义准确性审查，也不提供历史版本管理。

## Quality Report

`quality_report.json` 是笔记质量诊断报告，当前主要使用本地启发式指标，避免额外增加 LLM 成本。分数来自段落长度、标题、关键词、图片引用和来源标记等信号，应结合课件与笔记人工判断。

重点指标包括：

| 字段 | 含义 |
| --- | --- |
| `coherence_score` | 段落和标题结构的启发式分数，不验证逻辑连贯性。 |
| `explanation_depth_score` | 段落长度及“为什么”“如何”等词的启发式分数。 |
| `example_score` | 例子、类比等词的出现情况。 |
| `figure_integration_score` | 图片引用与原图数量等结构信号。 |
| `mechanical_page_listing_score` | 机械逐页复述的文本模式信号。 |
| `self_test_score` | 自测相关词的出现情况。 |
| `pitfall_score` | 易错点、误解等词的出现情况。 |
| `hallucination_risk` | 由来源标记密度和覆盖缺失推断的复核优先级；不做事实核查。 |
| `question_quality_score` | 当前 `build` 没有接入独立学习包，值为 `null`；`study-pack` 的题目质量另行计算。 |

未来可以增加轻量 LLM 审阅 pass，但不应该让同一个写作模型无约束地自己审自己。

## 教师讲义模式

`lecture` preset 的目标是“教学重构”，不是简单总结。

高质量讲义应该尽量包含：

- 本节要解决的核心问题。
- 背景与直觉。
- 概念的详细解释。
- 图表、公式、流程图的作用。
- 关键术语解释。
- 例子或类比。
- 易错点 / 常见误解。
- 本节小结。
- 自测问题。

补充内容可以是通用背景、直观解释和例子，但不能新增课件没有依据的具体数字、实验结果、结论或作者观点。

## Review / Exam 学习包

`slidenote study-pack` 是构建后的独立命令，读取已有的 `notes.md` 和 `content.json`，生成复习材料：

```text
review.md
exam.md
exam.json
exam.html
section_study_pack.json
exam_review_pack.json
final_exam.md
final_exam.answers.md
wrong_answer_review_prompt.md
```

设计目标：

- 逐步让复习从“看一份笔记”延伸到做题、批改和复盘；目前错题复盘以提示词文件为主，尚无持久化答题历史。
- 题目要有来源页和解析，不只是随机问答。
- 涉及图表的题目应尽量把相关图文就地放在题目附近。
- 错题复盘 prompt 应帮助学生追问：到底漏掉了哪个知识点。

## 推荐顺序

高质量路线建议：

```text
parse content
  -> deck/page understanding 与必讲项识别（Understand）
  -> section lecture writing
  -> teaching enrichment
  -> 候选补漏与校验（Write，启用时）
  -> coverage check、最终 element IR 与 source map（Guard）
  -> quality report（Guard）
  -> review/exam pack（单独运行 study-pack）
```

核心思想是：Write 负责生成可读正文，Guard 负责提示漏项和结构风险；准确性仍要对照课件复核。
