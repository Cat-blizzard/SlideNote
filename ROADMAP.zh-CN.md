# SlideNote 后续扩展路线图

这个文档只保留方向、优先级和关键决策。较长的模块设计说明已经迁移到 [docs/roadmap/extension-notes.zh-CN.md](docs/roadmap/extension-notes.zh-CN.md)。

SlideNote 当前的核心定位是：

> 保真型课程笔记生成器：不是简单总结 PPT/PDF，而是先解析、再理解、再写作、再检查覆盖率。

长期愿景是：

> 从课件转换器，成长为课程学习操作系统。

## 目录

- [五阶段产品流水线](#五阶段产品流水线)
- [当前基础](#当前基础)
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
| **Ingest** | 解析 PPT/PDF，生成 `content.json`、`element_ir.json`、`source_map.json`、截图和图片资产。 | 已引入 parser adapter 架构；内置解析器和 Docling / Marker / MinerU 外部 CLI adapter 通过统一 `Deck` 契约接入。 |
| **Understand** | 生成章节、页面类型、语义版面、图表理解、Deck Brief。 | 已收束为 `deck_understanding.json` 和 `page_understanding.json`，作为 GUI、Agent 和局部 revise 的稳定认知入口。 |
| **Write** | 生成 `notes.md`，支持 Lecture-Weave、section context、lecture-notes profile、teaching enrichment。 | 继续把默认高质量路线从“总结”推向“教学重构”。 |
| **Guard** | coverage、content guard、quality report、review/exam 题目质量检查。 | 加强来源校验、幻觉风险检测和错题复盘闭环。 |
| **Export** | Markdown、Word、PDF、LaTeX、review/exam pack、GUI 下载。 | 模板系统、课程级导出、多 PPT 整合。 |

更多细节见 [Pipeline 文档](docs/pipeline.zh-CN.md)。

## 当前基础

已经具备的产品基础：

- 支持 `.pptx` / `.pdf` 解析，`.ppt` 可尝试借助 LibreOffice 转 PDF；解析入口已抽象为 parser adapter，外部 Docling / Marker / MinerU 可选接入。
- 生成 `content.json`、`element_ir.json`、`source_map.json`、页面截图和图片资产。
- 支持 OCR、Vision、语义版面增强、表格理解、图片重要性排序、组合图识别、局部图裁剪、图文锚定。
- 生成 `deck_understanding.json` 和 `page_understanding.json`，统一承载 Deck Brief、章节、页面角色、图表理解和图片排序。
- 支持 `--preset lecture|local`，把底层参数收束成默认高质量讲义和无 API 本地预览两条用户侧工作流。
- 支持 Lecture-Weave、Deck Brief、Content Guard、teaching enrichment 和质量报告。
- 支持 coverage 报告、review/exam 学习包、题目质量指标和错题复盘 prompt。
- 支持 LLM/OCR/Vision 缓存、并发、用量报告、`progress.json`、`run_summary.json` 和 GUI。
- 支持 Markdown、带目录 Markdown、Word、PDF、LaTeX 导出。

详细配置见 [CONFIG.zh-CN.md](CONFIG.zh-CN.md)，详细机制见 [docs/index.zh-CN.md](docs/index.zh-CN.md)。

## 建议优先级

### P0：近期最值得做

1. **错题复盘闭环继续产品化**
   - 让 `exam.html` 的答题结果更自然地进入 `wrong_answer_review_prompt.md`。
   - 把错题关联到 section、source page、concept 和 review pack。
   - 在 GUI 中展示“错在哪里、该回看哪里”。

2. **题目质量约束继续增强**
   - 扩展 `quality_report.json` 的题目质量指标。
   - 检查答案唯一性、干扰项质量、解析充分性和来源覆盖。
   - 图表题保持图文就地嵌入。

3. **统一理解产物（已落地，2026-06-03）**
   - 已生成 `deck_understanding.json`：聚合 Deck Brief、章节、页面角色、关键概念、跨页关联、重要图表。
   - 已生成 `page_understanding.json`：聚合每页 section、role、modality、key points、表格、图示、语义组和 required items。
   - Deck Brief、section detection、page role、figure/table understanding、image ranking 已收束为稳定认知包，底层调试产物仍保留。

4. **Parser Adapter 架构（已落地，2026-06-03）**
   - 已定义内置解析器 adapter 接口，默认 `auto` 仍优先走内置 PPT/PDF 解析。
   - 已注册 Docling / Marker / MinerU 外部 CLI adapter，可通过 `--parser docling|marker|mineru` 或命令模板环境变量接入。
   - 核心 pipeline 只依赖统一 `Deck` 数据模型，不直接绑定某个外部解析库。

### P1：中期能力

1. **课程级学习包**
   - 支持多 PPT / 多讲次整合。
   - 生成课程级概念图、术语表、章节导航和复习计划。

2. **GUI 局部编辑与 revise**
   - 在 GUI 中按页、按 section 或按 source element 发起局部重写。
   - 依赖 `element_ir.json`、`source_map.json` 和 artifact registry。

3. **质量审阅升级**
   - 引入可选 LLM 质量评审。
   - 检查机械逐页复述、解释深度、例子密度、图表整合和幻觉风险。
   - 审阅模型和写作模型尽量分离。

4. **教材 / 个人笔记接入**
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
- **Preset 是用户入口。** 普通用户应优先看到 `lecture|local`；底层质量、缓存、并发、OCR/Vision 细节尽量留在内部默认里。
- **保留项目气质。** 起源、愿景、致谢和对未来 AI 红利的期待属于 README，不是可以随手迁走的噪音。
