# SlideNote Claude Backend 实验路线图

这个文档记录 `experiment/claude-backend` 分支的开发方向。它只保留方向、优先级和关键决策；较长的模块设计说明已经迁移到 [docs/roadmap/extension-notes.zh-CN.md](docs/roadmap/extension-notes.zh-CN.md)，Claude 后端细节见 [docs/claude-backend.zh-CN.md](docs/claude-backend.zh-CN.md)。

SlideNote 当前的核心定位是：

> 保真型课程笔记生成器：不是简单总结 PPT/PDF，而是先解析、再理解、再写作、再检查覆盖率。

长期愿景是：

> 从课件转换器，成长为课程学习操作系统。

## 目录

- [五阶段产品流水线](#五阶段产品流水线)
- [当前基础](#当前基础)
- [Claude Backend 实验重点](#claude-backend-实验重点)
- [建议优先级](#建议优先级)
- [长期愿景](#长期愿景吃到未来-ai-红利)
- [关键原则](#关键原则)

## 五阶段产品流水线

SlideNote 的底层能力很多，但用户侧和 LLM 工作流应该按五个阶段收束：

```text
Ingest -> Understand -> Write -> Guard -> Export
```

| 阶段 | 当前角色 | 下一步方向 |
| --- | --- | --- |
| **Ingest** | 解析 PPT/PDF，生成 `content.json`、`element_ir.json`、`source_map.json`、截图和图片资产。 | MarkItDown 式 parser adapter，允许 Docling、Marker、MinerU 等外部解析器作为可选 adapter。 |
| **Understand** | 生成章节、页面类型、语义版面、图表理解、Deck Brief。 | 收束为 `deck_understanding.json` 和 `page_understanding.json`。 |
| **Write** | 生成 `notes.md`，支持 Lecture-Weave、section context、lecture-notes profile、teaching enrichment。 | 继续把默认高质量路线从“总结”推向“教学重构”。 |
| **Guard** | coverage、content guard、quality report、review/exam 题目质量检查。 | 加强来源校验、幻觉风险检测和错题复盘闭环。 |
| **Export** | Markdown、Word、PDF、LaTeX、review/exam pack、GUI 下载。 | 模板系统、课程级导出、多 PPT 整合。 |

更多细节见 [Pipeline 文档](docs/pipeline.zh-CN.md)。

## 当前基础

已经具备的产品基础：

- 支持 `.pptx` / `.pdf` 解析，`.ppt` 可尝试借助 LibreOffice 转 PDF。
- 生成 `content.json`、`element_ir.json`、`source_map.json`、页面截图和图片资产。
- 支持 OCR、Vision、语义版面增强、表格理解、图片重要性排序、组合图识别、局部图裁剪、图文锚定。
- 支持 `--preset fast|faithful|lecture`，把底层参数收束成用户侧工作流。
- 支持 Lecture-Weave、Deck Brief、Content Guard、teaching enrichment 和质量报告。
- 支持 coverage 报告、review/exam 学习包、题目质量指标和错题复盘 prompt。
- 支持 LLM/OCR/Vision 缓存、并发、用量报告、`progress.json`、`run_summary.json` 和 GUI。
- 支持 Markdown、带目录 Markdown、Word、PDF、LaTeX 导出。

详细配置见 [CONFIG.zh-CN.md](CONFIG.zh-CN.md)，详细机制见 [docs/index.zh-CN.md](docs/index.zh-CN.md)。

## Claude Backend 实验重点

当前分支验证一条旁路：

> SlideNote 做可检查的课件理解与质量控制；Claude Code 做讲义生成和 repair；最终用 coverage 与 agent-eval 判断哪些旧能力可以被替换。

已经具备的实验入口：

- `agent-pack`：把解析、章节、资产、来源和写作约束打包成 Claude Code 可读的 agent pack。
- `agent-run`：调用官方 Claude Code CLI，以 stdout-only JSON 方式生成 section notes。
- `agent-build`：组合 pack + run，形成端到端 Claude backend 实验闭环。
- `agent-eval`：同时跑稳定 build 和 Claude backend，生成 `eval_report.json` / `eval_report.md`。
- 默认 coverage repair：trace missing、required visible missing 会回传给 Claude 重写整节。

分工边界：

- SlideNote 保持文件写入权，Claude 不直接写文件、不改仓库。
- SlideNote 保持 source map、coverage、图片路径校验和 artifact registry。
- Claude 优先接管高层 section 写作、图文组织和自然语言 repair。
- 稳定 `slidenote build` 暂不改成 Claude-first，先用实验入口比较质量。

## 建议优先级

### P0：近期最值得做

1. **Claude backend 质量闭环**
   - 用真实小课件跑 `agent-eval`，建立 baseline vs Claude backend 样例。
   - 根据样例改进 `eval_report.md`，让人工能快速看到 Claude 版是否真的更好。
   - 扩展 mock 测试：非法图片路径、repair 后仍缺图、Claude 返回 markdown 为空、coverage 无法映射 section。
   - 记录 Claude 调用次数、repair 触发原因和失败诊断，方便估算成本和稳定性。

2. **错题复盘闭环继续产品化**
   - 让 `exam.html` 的答题结果更自然地进入 `wrong_answer_review_prompt.md`。
   - 把错题关联到 section、source page、concept 和 review pack。
   - 在 GUI 中展示“错在哪里、该回看哪里”。

3. **题目质量约束继续增强**
   - 扩展 `quality_report.json` 的题目质量指标。
   - 检查答案唯一性、干扰项质量、解析充分性和来源覆盖。
   - 图表题保持图文就地嵌入。

4. **统一理解产物**
   - 设计并落地 `deck_understanding.json`。
   - 设计并落地 `page_understanding.json`。
   - 让 Deck Brief、section detection、page role、figure/table understanding、image ranking 收束到更稳定的认知包。

5. **Parser Adapter 架构**
   - 先定义内置解析器 adapter 接口。
   - 再接 Docling / Marker / MinerU 等外部解析器。
   - 核心 pipeline 不直接绑定某个解析库。

### P1：中期能力

1. **课程级学习包**
   - 支持多 PPT / 多讲次整合。
   - 生成课程级概念图、术语表、章节导航和复习计划。

2. **逐步把 Claude 做得更好的能力挪过去**
   - 图文讲义生成：优先让 Claude 决定图片插入位置和解释文字，SlideNote 做审计和路径校验。
   - Section 写作：让 Claude 输出更像讲义的章节，而不是逐页摘要。
   - 如果 `agent-eval` 证明 Claude backend 明显更好，再新增可选 note strategy 或实验入口别名。

3. **GUI 局部编辑与 revise**
   - 在 GUI 中按页、按 section 或按 source element 发起局部重写。
   - 依赖 `element_ir.json`、`source_map.json` 和 artifact registry。

4. **质量审阅升级**
   - 引入可选 LLM 质量评审。
   - 检查机械逐页复述、解释深度、例子密度、图表整合和幻觉风险。
   - 审阅模型和写作模型尽量分离。

5. **教材 / 个人笔记接入**
   - 教材作为可追溯背景知识，不替代课件来源。
   - 个人笔记作为补充上下文，明确区分来源。

### P2：长期增强

1. **开放式 Agent 工作流**
   - 用 artifact registry、IR 和 source map 作为共享状态。
   - 让 agent 调用明确阶段，而不是把整个 pipeline 交给自由聊天。

2. **模板与发布系统**
   - 支持课程封面、页眉页脚、引用样式、主题和导出模板。
   - 支持更完整的 PDF / Word / LaTeX 发布路线。

3. **更多输入格式**
   - 在 parser adapter 基础上支持 Word、HTML、图片集合、教材 PDF 和更多文档格式。

4. **局部性能重写**
   - 如果确定性模块成为瓶颈，再考虑 Rust 或其它语言重写局部组件。
   - 当前阶段优先产品结构和质量闭环，不急于语言迁移。

## 长期愿景：吃到未来 AI 红利

SlideNote 带着一个乐观前提在建设：未来 AI 会更强、更快、更便宜，也会更容易通过成熟的开源智能体框架来组织复杂工作流。

以 DeepSeek 这类强调性价比、可获得性和开放生态的模型 / 服务为例，当高质量 API 的价格、速度和可用性继续改善，多 pass 的高质量流程就会更适合普通课程材料。SlideNote 可以把更深的课件理解、逐页视觉推理、教师讲义式写作、teaching enrichment、coverage repair、考试题生成、错题复盘和来源校验变成更自然的默认能力。

项目真正要抓住的不是某一个模型，而是一个可迁移的工程底座：`element_ir.json`、`source_map.json`、coverage、artifact registry、preset、cache key、review/exam 学习包和 GUI 审阅工作台。这些结构让未来模型能力提升时，SlideNote 的上限也能一起升高。

愿景是让课件、教材、个人笔记、图表、公式、测验、错题和局部修订都处在同一条可检查、可追溯、可复习的学习工作流里。

## 关键原则

- **不要让 README 变成参数手册。** 首页负责让人快速理解项目，细节放到 `docs/`。
- **不要把确定性工程交给 LLM。** 元素 ID、source map、缓存、导出、成本统计应保持稳定。
- **让 LLM 做它擅长的事。** 章节理解、图表解释、讲义写作、易错点、自测题和语义修复适合模型参与。
- **Coverage 是质检器，不是写作模板。** 正文应该像讲义，coverage 负责最后查漏和局部修补。
- **Claude 不是第一步，解析和校验才是第一步。** 不要因为 Claude 输出好看就放弃 coverage；好看的漏内容仍然是失败。
- **Preset 是用户入口。** 底层参数保留给高级用户，普通用户应优先看到 `fast|faithful|lecture`。
- **保留项目气质。** 起源、愿景、致谢和对未来 AI 红利的期待属于 README，不是可以随手迁走的噪音。
