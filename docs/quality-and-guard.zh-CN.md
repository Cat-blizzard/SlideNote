# Coverage、Content Guard 与学习质量

SlideNote 的质量控制分两层：

1. 硬检查：关键元素有没有进入笔记，来源是否可追溯。
2. 软检查：笔记是否像讲义，是否有解释深度、图表整合、例子、自测和易错点。

Coverage 不应该决定正文结构。它更适合作为最后的质检器，发现遗漏后只做局部修补，避免把正文改回逐页清单。

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

Content Guard 负责先找出“必须解释”的学习内容，再把它们交给写作和修复阶段。

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

## Quality Report

`quality_report.json` 是学习质量报告。第一版主要使用本地启发式指标，避免额外增加 LLM 成本。

重点指标包括：

| 字段 | 含义 |
| --- | --- |
| `coherence_score` | 章节是否连贯。 |
| `explanation_depth_score` | 是否解释“是什么、为什么、怎么运作”。 |
| `example_score` | 是否包含例子、类比或直观说明。 |
| `figure_integration_score` | 图表是否融入正文，而不是只附在页尾。 |
| `mechanical_page_listing_score` | 是否像“第 1 页讲 A，第 2 页讲 B”的机械复述。 |
| `self_check_coverage_score` | 是否包含自测题。 |
| `misconception_coverage_score` | 是否覆盖易错点或常见误解。 |
| `question_quality_score` | 复习/考试题是否有明确答案、来源和解析。 |

未来可以增加轻量 LLM 审阅 pass，但不应该让同一个写作模型无约束地自己审自己。

## 教师讲义模式

`--preset lecture` / `--note-profile lecture-notes` 的目标是“教学重构”，不是简单总结。

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

Review / Exam 模式把最终 `notes.md` 延伸成复习材料：

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

- 让复习从“看一份笔记”变成“做题、批改、复盘、定位知识漏洞”的闭环。
- 题目要有来源页和解析，不只是随机问答。
- 涉及图表的题目应尽量把相关图文就地放在题目附近。
- 错题复盘 prompt 应帮助学生追问：到底漏掉了哪个知识点。

## 推荐顺序

高质量路线建议：

```text
parse content
  -> deck/page understanding
  -> section lecture writing
  -> teaching enrichment
  -> content guard repair
  -> coverage check
  -> quality report
  -> review/exam pack
```

核心思想是：Write 负责把内容讲清楚，Guard 负责不漏和不乱编。
