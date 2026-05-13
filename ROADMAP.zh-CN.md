# SlideNote 后续扩展路线图

这个文档记录当前项目里“已经讨论过、但尚未完整实现或仍需增强”的功能方向，方便以后继续开发时快速恢复上下文、排优先级和拆任务。

SlideNote 当前的核心定位仍然是：

> 保真型课程笔记生成器：不是简单总结 PPT/PDF，而是先解析、再生成、再检查覆盖率。

## 当前已经具备的基础

- 支持 `.pptx` / `.pdf` 解析，`.ppt` 可尝试借助 LibreOffice 转 PDF。
- 能生成逐页结构化 `content.json`。
- 能提取标题、文本块、表格、嵌入图片和部分页面截图。
- 能生成 Markdown 笔记 `notes.md`。
- 能保留来源页码和元素 ID。
- 有覆盖率检查 `coverage.json` / `coverage.md`。
- 支持多家文本 LLM：OpenAI、DeepSeek、Qwen、豆包/火山方舟、GLM、Gemini、Claude。
- 支持独立 OCR：百度 OCR、Mathpix、Google Vision OCR。
- 支持独立视觉解析：OpenAI、Qwen-VL、豆包/火山方舟等视觉模型。
- 有 LLM / OCR / Vision 缓存和用量报告，便于未来 GUI 展示成本和缓存命中。
- 有基础运行进度文件 `progress.json` 和运行总览 `run_summary.json`。
- 支持基础 `--speed-mode` 预设、`--concurrency` 并发参数、`--global-cache-dir` 全局缓存目录。
- 支持 `--refresh-pages` 对指定页绕过本地缓存进行局部刷新。
- 支持 `slidenote doctor` 环境检测。
- 支持疑似小图标/装饰图标记，默认从笔记、覆盖率、OCR fallback 和视觉目标中过滤。
- 支持 `source_map.json`，记录笔记块与原始 PPT/PDF 元素之间的来源映射。

## 1. 上下文策略增强

当前主要是按页组织材料，再逐页生成或检查。后续可以支持多种上下文粒度：

- 页级上下文：适合低风险、可追溯生成，也适合精细缓存。
- 小节级上下文：适合默认模式，在速度、质量、缓存粒度之间取得平衡。
- 章节级上下文：适合让模型理解一组连续幻灯片的局部逻辑。
- 全文件上下文：适合整体重排、课程大纲抽取和跨章节关联，但更容易超长或输出截断。
- 自定义上下文：用户手动选择若干页、若干章节或某个知识点范围。

### 粒度取舍

不同上下文粒度会影响质量、速度和缓存。

```text
page mode
```

逐页生成。优点是来源清楚、失败影响小、缓存非常精细；缺点是请求次数多、跨页逻辑弱，容易生成“逐页拼接感”的笔记。

```text
section mode
```

按小节生成。把 5-15 页左右的连续内容合成一个 prompt，让模型写成一个连贯小节。它通常是默认推荐模式，因为请求次数更少、逻辑更顺、又不会像整章那样过长。

```text
chapter mode
```

按章节生成。适合长上下文模型和结构较清晰的课程，但单次输入/输出更长，失败后重跑成本更高，缓存失效范围也更大。

```text
file mode
```

全文件生成。适合做全局大纲、课程总结、知识结构重排，不适合作为默认正文生成模式。

可能参数：

```powershell
--context-mode page
--context-mode section
--context-mode chapter
--context-mode file
--max-pages-per-section 12
--max-input-tokens-per-section 30000
--fallback-to-page-on-overflow
```

### 为什么小节级可能更快

逐页生成时：

```text
257 页 = 最多 257 次 LLM 请求
```

如果按小节生成：

```text
257 页 -> 约 20-30 个小节 = 20-30 次 LLM 请求
```

请求次数减少后，网络等待、模型排队和固定请求开销都会下降。模型也能看到连续几页之间的关系，更容易把内容写成讲义，而不是逐页摘要。

### 对缓存的影响

上下文变大后，缓存粒度也会变粗。

逐页缓存：

- 优点：某一页变了，只失效这一页。
- 缺点：请求多，跨页逻辑弱。

小节缓存：

- 优点：请求少，生成更连贯。
- 缺点：小节内任意一页变化，可能导致整个小节笔记缓存失效。

章节缓存：

- 优点：整体性更强。
- 缺点：缓存失效范围大，重跑成本高。

因此更合理的是分层缓存：

```text
页面解析缓存
  ↓
页面 OCR / Vision 缓存
  ↓
小节笔记缓存
  ↓
章节总结缓存
  ↓
课程总览缓存
```

这样即使小节笔记需要重写，OCR 和视觉结果也不必重新调用。

### 服务端缓存与本地缓存

DeepSeek、Qwen 等平台可能有服务端上下文缓存，但它通常依赖相同 token 前缀，而不是语义相似。因此提升服务端缓存命中的关键是：

- 固定系统提示词。
- 固定任务说明。
- 把变化的页内容放在 prompt 后半部分。
- 尽量保持 prompt 模板稳定。

真正稳定省钱的核心仍然应该是 SlideNote 自己的本地缓存和增量更新。

### 推荐生成管线

未来推荐采用：

```text
逐页解析 content.json
逐页 OCR / Vision
按小节组合上下文
小节级生成正文
章节级生成摘要
课程级生成总览
```

这比直接把整份 PPT 丢给模型稳，也比逐页生成更连贯。

计划增强点：

- 自动识别章节边界。
- 自动识别小节边界。
- 允许用户选择“按页 / 按小节 / 按章节 / 全文件”生成。
- 在 `llm_usage.json` 里记录每次调用使用的上下文范围。
- 对超长 PPT 自动分块，避免上下文过长或输出被截断。
- 对 context overflow 自动 fallback 到更小粒度。
- 在缓存报告里标记缓存粒度：page / section / chapter / file。

## 2. 覆盖率 + 逻辑关联重组

一个重要愿景是：系统不只是顺序复述 PPT，而是能把线性课件打碎，再按“覆盖率”和“逻辑关联”重新拼装。

例如：

- 第 1 页的基础定义。
- 第 20 页的相关公式。
- 第 50 页的综合应用题。

未来希望系统能把它们折叠到同一个知识点小节里，让笔记更接近学习材料，而不是幻灯片复刻。

计划增强点：

- 为每个元素生成知识点标签。
- 建立元素之间的引用关系、先修关系、例题关系。
- 支持“按原 PPT 顺序”和“按知识结构重排”两种笔记模式。
- 重排后仍保留每段对应的原始页码和元素 ID。
- 覆盖率检查需要适配跨页合并，不能只看页内覆盖。

## 3. PPT 章节切分与分批输出

大 PPT 往往并不适合一次性生成一份很长的笔记。后续应支持把一个 PPT/PDF 按章节或小节逻辑切分，再分批生成多份笔记，最后可选合并成完整讲义。

这个能力不是简单“把文件切开”，更重要的是逻辑切分：

```text
Ch07.pdf
  1-12 页：概述
  13-35 页：核心概念
  36-70 页：机制/算法
  71-95 页：例题
  96-120 页：总结
```

输出结构可以是：

```text
outputs/ch07/
  sections/
    01-overview.md
    02-core-concepts.md
    03-mechanism.md
    04-examples.md
  notes.md
  section_index.json
  section_coverage.json
```

产品价值：

- 大 PPT 可以分批生成，减少单次等待焦虑。
- 每份笔记更短，更适合阅读和复习。
- 某一节失败或质量不好时，只需要重跑该节。
- 缓存粒度更合理，小节不变即可复用。
- GUI 可以按章节导航。
- 后续交互式编辑可以锁定到某一小节。
- 持续更新时可以只刷新受影响的小节。

### 自动切分依据

可以结合多种信号：

- 目录页。
- 标题页。
- 页标题变化。
- “Chapter / Section / Part / 小节 / 本章小结”等关键词。
- 字号和版式变化。
- 页码间隔。
- 主题相似度。
- LLM 辅助识别章节边界。

### 手动切分

自动切分不可能总是准确，因此应允许用户手动指定：

```powershell
--section-pages "1-12:概述,13-35:核心概念,36-70:例题"
```

或者在 GUI 中拖动章节边界。

### CLI 设想

独立切分：

```powershell
python -m slidenote split Ch07.pdf --out outputs/ch07
```

构建时切分：

```powershell
python -m slidenote build Ch07.pdf --out outputs/ch07 --split-by section
```

只重跑某一节：

```powershell
python -m slidenote build Ch07.pdf --out outputs/ch07 --section 03
```

### 数据结构

可能新增：

- `section_index.json`：记录每个小节的页码范围、标题、来源文件。
- `section_notes/`：每个小节单独输出。
- `section_coverage.json`：按小节统计覆盖率。
- `section_usage.json`：按小节统计 LLM/OCR/Vision 用量。

### 与上下文策略的关系

PPT 章节切分是 `--context-mode section` 的基础能力。推荐流程：

```text
先切分章节/小节
  ↓
逐页解析、OCR、Vision
  ↓
按小节生成笔记
  ↓
合并成完整 notes.md
```

这样既保留页级证据，又能获得小节级连贯表达。

## 4. 教材 / 参考书作为知识库

后续可以支持用户上传教材、参考书、讲义或课程大纲，为笔记生成提供外部知识支撑。

可能路线：

- RAG：把教材切块、向量化、检索相关片段，再喂给笔记生成模型。
- 关键词索引：对章节标题、术语、公式名、定义做轻量检索。
- 摘要索引：先把教材章节压缩成结构化摘要，再按需注入。
- 混合检索：向量检索 + 关键词检索 + 章节目录定位。

需要注意：

- 教材大多可能是扫描件，需要高质量 OCR。
- 教材内容可能很长，必须做缓存。
- 教材引用要和 PPT 来源区分开。
- 模型补充教材内容时，应标记为“参考书补充”或类似来源。

计划输出：

- `knowledge_index.json`
- `retrieval_usage.json`
- 笔记里的“参考书补充说明”
- 可追溯到教材页码或章节的引用

## 5. 教材缓存与成本控制

教材和扫描 PDF 很耗 token，也很耗 OCR 成本。未来需要专门缓存制度。

计划增强点：

- OCR 结果缓存：按页面图片 hash 缓存。
- 教材切块缓存：按文件 hash、页码、chunk hash 缓存。
- 向量索引缓存：同一本教材不重复 embedding。
- 检索结果缓存：同一页 PPT 的相同查询可复用。
- GUI 展示缓存命中率、节省调用次数、估算节省成本。

缓存信息应尽量结构化，便于 GUI 直接读取，而不是只写在日志里。

## 6. GUI 可视化界面

当前项目是 CLI。未来如果面向没有计算机基础的用户，GUI 会非常重要。

核心目标：

- 降低 API key 配置门槛。
- 让用户看到 PPT 原页、结构化解析结果、AI 笔记和覆盖状态。
- 让用户可以选择需要 OCR / 视觉解析的页面。
- 让缓存、费用、进度更透明。

可能界面：

- 左侧：原始 PPT/PDF 页面预览。
- 中间：页面元素清单和覆盖状态。
- 右侧：生成后的笔记。
- 顶部：模型选择、OCR 开关、视觉解析开关、成本模式。
- 设置页：配置 OpenAI、DeepSeek、Qwen、豆包、百度 OCR 等 API key。

需要考虑：

- 开箱即用和自带 key 的商业模式。
- 用户自填 key 的高级模式。
- 对需要控制台创建 endpoint 的服务商提供清晰引导。
- Windows 下自动检测 LibreOffice / PowerPoint / PATH。

## 7. 交互式编辑与对话式微调

这是一个非常适合 GUI 阶段的产品方向。与其让用户每次重新生成整份笔记，不如让用户先获得一版可编辑 Markdown/讲义，再通过对话框提出局部修改意见，让模型基于当前笔记和原始来源做小范围微调。

目标形态：

- 中间是可编辑的 Markdown / 富文本笔记。
- 左侧可以查看原 PPT/PDF 页面或结构化元素。
- 右侧或底部是对话框。
- 用户选中一段文字后，可以让 AI 修改这段。
- 用户也可以对整章提出要求，例如“这一节太啰嗦，压缩一点”“这里补一个例子”“把第 23 页的图解释加进来”。

### 产品价值

- 用户不会被迫接受一次性生成结果。
- 大模型从“生成器”变成“可协作的编辑助手”。
- 比重新全量生成更省 token、更快、更可控。
- 用户可以保留自己的修改痕迹。
- 很适合个人笔记接入、来源显示策略、LaTeX/Word 导出。

### 关键设计

#### 局部编辑优先

不要默认让模型重写整份 `notes.md`。更合理的是：

```text
选中段落 / 当前小节 / 当前章节 -> 模型局部改写 -> 生成 diff -> 用户确认
```

这样可以避免：

- 整份笔记风格突然变化。
- 已经改好的部分被覆盖。
- token 消耗过高。
- 来源映射丢失。

#### 用户编辑优先级

用户手动改过的内容应该被视为高优先级内容。

需要记录：

- 哪些段落是用户编辑过的。
- 哪些段落是 AI 生成的。
- 哪些段落被 AI 后续修改过。
- 用户是否锁定某段，禁止后续自动覆盖。

可能字段：

```json
{
  "block_id": "note_sec_12_p3",
  "edited_by_user": true,
  "locked": false,
  "source_refs": ["chapter02.pptx:p12:s12_t2"]
}
```

#### 来源映射不能丢

即使用户选择清爽阅读模式，不显示来源，系统底层也应继续维护 `source_map.json`。

对话微调时，模型输入应该包含：

- 被选中的笔记段落。
- 该段落对应的 PPT/PDF 元素。
- 相关图片 OCR / 视觉摘要。
- 用户的新要求。
- 当前来源显示策略。

这样可以避免模型凭空改写。

#### Diff / 审阅机制

模型修改后不应直接覆盖，最好先展示：

- 原文。
- 修改后版本。
- 差异高亮。
- 接受 / 拒绝 / 再改。

CLI 里可以先输出 patch 或生成 `revision.md`；GUI 里再做可视化 diff。

#### 对话上下文

对话不应简单把整份笔记塞给模型。更合理的是：

- 当前选区。
- 当前章节。
- 相关 source refs。
- 用户最近几轮修改要求。
- 必要的全局风格设置。

这能降低 token，也能减少模型误改无关内容。

### 可能命令设计

CLI 阶段可以先做离线修订：

```powershell
python -m slidenote revise outputs/lecture `
  --section note_sec_12 `
  --instruction "这一节写得更适合考试复习，保留来源"
```

或者：

```powershell
python -m slidenote revise outputs/lecture `
  --range "page:20-25" `
  --instruction "压缩冗余内容，并补充图示解释"
```

GUI 阶段则做成：

```text
选中文字 -> 输入修改意见 -> AI 生成修改建议 -> 用户确认
```

### 需要的文件

可能新增：

- `note_blocks.json`：把 Markdown 拆成可定位的段落/小节。
- `source_map.json`：笔记段落到原始元素的映射。
- `revision_history.json`：记录每次用户/AI 修改。
- `edit_sessions.json`：记录对话式编辑会话。
- `locked_blocks.json`：记录用户锁定的段落。

### 风险

- 如果没有段落级 ID，后续很难稳定局部修改。
- 如果模型直接改 Markdown，可能破坏图片路径、来源标注或表格格式。
- 如果用户多次手动编辑，自动重生成可能覆盖劳动成果。
- 如果不做版本历史，用户会害怕让 AI 修改。

### 建议优先级

这个方向很适合放在 GUI 原型之后，但底层数据结构可以提前准备。尤其是：

- 段落级 note block ID。
- source map。
- revision history。
- 用户编辑保护。

这些能力也会服务 LaTeX 导出、持续更新和个人笔记融合。

## 8. 运行进度与状态报告

长课件、图像驱动型课件、多模型调用都会让运行时间变长。如果 CLI 在运行时没有反馈，用户很容易不知道程序是卡住了、还在调用 API，还是已经处理到后半段。因此进度系统应作为近期基础能力建设。

当前基础版已经实现：CLI 阶段进度、`progress.json` 和 `run_summary.json`。后续还需要继续增强 ETA、失败恢复、GUI 任务状态和更细粒度的阶段统计。

### CLI 实时进度

运行时应显示当前阶段和处理数量，例如：

```text
[1/6] Parsing input file...
[2/6] Rendering page screenshots... 257 pages
[3/6] OCR... 8/32 targets, cache hits 3, API calls 5
[4/6] Vision analysis... 12/80 targets, cache hits 4, API calls 8
[5/6] LLM note generation... page 43/257, cache hits 18, API calls 25
[6/6] Coverage check...
```

需要展示：

- 当前阶段：Parsing / Rendering / OCR / Vision / LLM / Coverage / Export。
- 当前进度：第几页 / 总页数，或第几个 target / 总 target。
- 缓存命中数。
- 实际 API 调用数。
- 已耗时。
- 简单 ETA。
- 当前正在处理的文件、页码或图片。

### 结构化进度文件

为了未来 GUI，不应只把进度打印到终端。运行时应同步写入：

```text
progress.json
```

示例结构：

```json
{
  "status": "running",
  "stage": "vision",
  "stage_index": 4,
  "stage_total": 6,
  "current": 12,
  "total": 80,
  "message": "Analyzing slide 12 screenshot",
  "cache_hits": 3,
  "api_calls": 9,
  "started_at": "...",
  "updated_at": "...",
  "elapsed_seconds": 512,
  "estimated_remaining_seconds": 840
}
```

价值：

- CLI 可以显示实时状态。
- GUI 可以直接读它做进度条。
- 异常退出后能知道停在哪一步。
- 后续可以支持继续运行、增量更新和任务恢复。

### 最终运行报告

除了已有的 `llm_usage.json`、`vision_usage.json`、`ocr_usage.json` 和 `coverage.json`，还可以增加：

```text
run_summary.json
```

用于汇总：

- 总页数。
- 总图片数。
- OCR 处理页数 / target 数。
- Vision 处理页数 / target 数。
- LLM 处理页数。
- 缓存命中次数。
- 实际 API 调用次数。
- 总耗时。
- token 用量。
- 哪一步最慢。
- 哪些页失败或跳过。

### CLI 参数设想

默认显示简洁进度：

```powershell
python -m slidenote build lecture.pdf --out outputs/lecture
```

可选静默模式：

```powershell
--quiet
```

可选显式指定进度文件：

```powershell
--progress-json outputs/lecture/progress.json
```

这个能力不只是体验优化，也会成为后续 GUI、课程工作区、多 PPT 整合和持续更新的基础设施。

## 9. 加速与成本调度

长课件、扫描 PDF、图片驱动型 PPT 会让运行时间和 token 成本快速上升。后续需要把 SlideNote 从“顺序执行所有步骤”升级为“可调度的处理管线”，让用户可以在速度、成本和质量之间做选择。

当前基础版已经实现：`--speed-mode`、`--concurrency`、`--global-cache-dir` 和 `--refresh-pages`。它们提供了第一层成本控制；后续仍需要加入 provider 限速、失败重试、真正的局部小节重跑和更智能的任务调度。

### 当前耗时来源

大文件运行慢通常来自：

- 页面渲染：PDF/PPT 转整页截图。
- 图片抽取：PDF 内部可能有大量碎图、logo、小图标。
- OCR 调用：扫描页或图片文字需要外部 OCR。
- Vision 调用：视觉模型逐页理解截图或图片。
- LLM 改写：逐页生成笔记时请求次数多。

其中最慢、最贵的通常是 Vision 和 LLM。

### 用户可选运行模式

可以设计几种预设模式：

```text
fast
```

快速预览模式。只做本地解析和规则草稿，或只做文本 LLM，不做视觉/OCR。

```text
balanced
```

默认推荐模式。自动选择少量高价值页面做视觉解析，LLM 正常生成。

```text
quality
```

高质量模式。更多视觉解析、更高图片分辨率、更完整的 OCR 和 LLM 改写。

```text
debug
```

调试模式。保留更多中间文件和诊断信息，但不追求速度。

可能参数：

```powershell
--speed-mode fast
--speed-mode balanced
--speed-mode quality
```

### 分阶段运行

不应该强迫用户一次性把所有能力拉满。更合理的流程是：

```text
1. 本地解析 + 页面截图
2. 本地规则草稿
3. 少量视觉解析
4. LLM 改写
5. 对遗漏/低质量页面局部 refresh
```

用户可以先低成本跑一版，再决定是否增强某些页面。

### 视觉解析加速

视觉是图多课件的主要瓶颈之一。

可优化方向：

- 限制视觉目标数，例如 `--vision-max-targets 20/30/50`。
- 优先解析整页截图，而不是所有嵌入碎图。
- 跳过小 logo、小图标、装饰性图片。
- 先用低分辨率/低 detail 粗解析。
- 对用户选中的页再高质量 refresh。
- 根据页面信息密度选择视觉目标。
- 对目录页、过渡页、纯标题页降权。
- 对流程图、公式图、代码截图、表格截图提权。

### LLM 改写加速

逐页 LLM 调用在大课件上会比较慢。

可优化方向：

- 缓存命中时跳过调用。
- 支持小节级 / 章节级生成，减少请求次数。
- 支持只生成指定页码范围。
- 对文本很少或无有效内容的页面跳过改写。
- 对连续简单页合并调用。
- 支持并发 API 调用，但需要限速和失败重试。
- 对超长小节自动拆分，避免输出截断。
- 对小节级生成结果建立缓存，减少重复运行成本。

### OCR 加速

OCR 不应默认扫所有页。

可优化方向：

- 只 OCR 抽取文字很少的页面。
- 只 OCR 用户选择的页。
- 只 OCR 大图或疑似文字截图。
- 对页面截图 hash 缓存 OCR 结果。
- 公式类材料才优先走 Mathpix。
- 普通中文材料优先走性价比更高的中文 OCR。

### 缓存与复用

当前已有 LLM / OCR / Vision 缓存，但后续可以增强为跨输出目录、跨课程复用的全局缓存。

计划增强：

- 支持全局缓存目录。
- 缓存 key 包含文件 hash、页 hash、模型、prompt、参数。
- 换输出目录不丢缓存。
- 多 PPT 课程中相同页面或相同图片不重复调用。
- `run_summary.json` 中显示缓存节省的调用次数和估算成本。

### 并发与限速

未来可以支持并发处理，但必须谨慎。

适合并发的任务：

- 多页 OCR。
- 多页 Vision。
- 多页 LLM 改写。
- 图片尺寸分析。

需要限制：

- provider 的 QPS / RPM / TPM。
- 失败重试次数。
- 并发任务数。
- 网络错误和超时。
- API 余额不足时的中断恢复。

可能参数：

```powershell
--concurrency 4
--max-retries 3
--rate-limit auto
```

### 增量更新

长期来看，最快的运行方式不是“更快地全量重跑”，而是“不重跑没变的东西”。

需要：

- 文件 hash。
- 页级 hash。
- 图片 hash。
- prompt version。
- 模型参数记录。
- 变更页识别。
- stale cache 标记。

这会和课程工作区、多 PPT 整合、持续输入功能共用同一套基础。

### 进度反馈

加速也需要和进度系统配合。用户应该能看到：

- 当前阶段预计剩余时间。
- 哪些页命中缓存。
- 哪些页正在调用 API。
- 哪些页被跳过，原因是什么。
- 当前 token 用量和调用次数。

## 10. 图片取舍与视觉策略

PPT 很多知识在图片里，但全量读图成本高。后续需要更细的策略。

当前基础版已经实现：对很小、极窄或疑似装饰性的图片资源打 `ignored` 标记，并默认从 notes、coverage、OCR fallback 和独立 vision target 中跳过。后续仍需要更智能地区分装饰图、logo、真实公式图、流程图和截图。

已有方向：

- `--vision auto` 优先选高价值页面截图。
- `--vision all` 全量解析，质量高但成本高。
- 视觉结果缓存，避免重复花费。

未来增强：

- 先用本地规则判断图片价值：面积、位置、是否含文字、是否像图表。
- 对疑似装饰图、logo、小图标降权。
- 对流程图、公式截图、代码截图、表格截图提权。
- 支持用户在 GUI 勾选“解析这几页”。
- 支持先低清解析，必要时对单页高精度 refresh。

## 11. 学科策略 / Domain Profiles

不同学科的 PPT 形态差异很大，不能假设同一套默认模式能在所有学科上都达到高质量。

典型差异：

```text
数学：公式、定义、定理、证明、推导、例题
医学：解剖图、病理图、影像图、诊断流程、分类标准
计算机：代码、协议流程、系统架构、命令、算法步骤
文科：长文本、理论框架、案例、术语辨析
商科：图表、模型、数据、案例分析
```

因此，SlideNote 后续应支持“通用框架 + 学科 profile”，而不是一个 prompt 打天下。

### 建议 profile

```powershell
--domain auto
--domain general
--domain math
--domain medicine
--domain cs
--domain humanities
--domain business
```

### 数学模式

重点：

- 公式 OCR 优先级高。
- Mathpix 权重更高。
- 保留定义、定理、引理、证明和推导步骤。
- 不要随意压缩证明过程。
- 公式编号、符号解释和例题对应关系很重要。
- LaTeX 导出优先级更高。

输出结构可以偏向：

```text
定义 -> 定理/性质 -> 推导/证明 -> 例题 -> 易错点
```

### 医学模式

重点：

- 图片和视觉理解优先级高。
- 解剖图、病理图、影像图、流程图要尽量保留并解释。
- 图注、图像来源页码和视觉摘要非常重要。
- 专业名词中英对照有价值。
- 表格、诊断标准、分类系统要结构化。
- 需要谨慎免责声明，避免把学习笔记误用为医疗建议。

输出结构可以偏向：

```text
概念 -> 图像说明 -> 临床/课程意义 -> 分类/诊断标准 -> 记忆要点
```

### 计算机模式

重点：

- 代码截图 OCR。
- 协议流程图、系统架构图、调用链要解释清楚。
- API、参数、命令、伪代码和复杂度需要保留。
- 实验步骤和错误排查信息可能很重要。

输出结构可以偏向：

```text
概念 -> 机制/流程 -> 代码/伪代码 -> 例子 -> 常见问题
```

### 文科 / 商科模式

重点：

- 概念辨析。
- 理论框架。
- 案例归纳。
- 图表和模型解释。
- 时间线、人物、流派、比较表。

输出结构可以偏向：

```text
核心观点 -> 理论框架 -> 案例 -> 对比 -> 复习问题
```

### 自动识别

`--domain auto` 可以先用轻量规则：

- 公式符号、LaTeX 符号、数学关键词多：倾向 `math`。
- 图片面积占比高、医学关键词多：倾向 `medicine` 或 visual-heavy。
- 代码关键词、命令、括号/缩进代码多：倾向 `cs`。
- 长文本和理论关键词多：倾向 `humanities`。
- 图表、商业模型、财务术语多：倾向 `business`。

自动识别结果应允许用户手动覆盖。

### profile 影响范围

Domain Profile 应影响：

- OCR provider 推荐。
- Vision target 选择。
- 图片保留策略。
- Prompt 模板。
- 笔记章节结构。
- 覆盖率检查规则。
- LaTeX / Word / HTML 导出模板。
- 复习资料生成方式。

### 配置文件设想

后续可以把 profile 做成可扩展配置：

```text
domain_profiles/
  general.yaml
  math.yaml
  medicine.yaml
  cs.yaml
```

每个 profile 可配置：

- 默认 OCR provider。
- 默认 vision 参数。
- prompt 片段。
- 输出结构。
- 覆盖率规则。
- 推荐导出模板。

关键原则：

- 学科策略不应破坏底层可追溯性。
- 用户可以选择简单通用模式，也可以启用学科增强。
- 高质量不是靠一个万能 prompt，而是靠领域策略。

## 12. OCR 能力增强

当前已支持 OCR API，但后续仍可以细化。

计划增强点：

- 更好的 OCR provider 抽象，方便接入腾讯云、阿里云、火山 OCR、PaddleOCR、本地 OCR。
- 针对公式类材料优先使用 Mathpix。
- 针对中文扫描教材优先使用高质量中文 OCR。
- 对 OCR 结果做版面重建：段落、标题、表格、公式分区。
- OCR 置信度低时，在笔记和报告里标注风险。

## 13. LaTeX / PDF 高质量排版

Markdown 后续可以进一步导出成 LaTeX，再编译成 PDF。

设计方向：

- 默认提供一套课程笔记 LaTeX 模板。
- 支持用户上传自己的 `.tex` 模板。
- 支持章节标题、页码引用、图片、表格、公式、代码块。
- Markdown 到 LaTeX 尽量使用机械转换。
- AI 只参与排版语义优化，不应直接自由生成整份 LaTeX 源码。

可能输出：

- `notes.md`
- `notes.tex`
- `notes.pdf`

待解决问题：

- 图片路径和尺寸控制。
- 中文 LaTeX 模板。
- 表格过宽处理。
- 公式 OCR 结果如何转 LaTeX。
- 用户模板变量规范。

## 14. 模板系统

未来可以支持用户上传个人模板。

模板类型：

- Markdown 模板。
- LaTeX 模板。
- Word 模板。
- HTML 模板。

模板变量示例：

- 课程标题。
- 章节标题。
- 知识点层级。
- 原 PPT 页码。
- 图片路径。
- 覆盖率报告。
- AI 补充说明。

原则：

- 默认模板要足够好看、稳定、中文友好。
- 用户模板不应破坏内容覆盖率。
- 模板只控制呈现，不应该改变知识内容本身。

## 15. 多 Agent 工作流

后续可以考虑多 Agent，但不应该一开始就复杂化。SlideNote 更适合采用：

> 黑板式共享状态 + 任务队列 + 结构化审阅回路。

不建议让多个 Agent 主要靠自由聊天协作。自由聊天看起来智能，但工程上很难缓存、恢复、校验和追溯，容易丢失来源证据。

### 黑板式共享状态

多个 Agent 不直接互相聊天，而是读写同一个结构化工作区：

```text
Blackboard/
  content.json
  source_map.json
  note_blocks.json
  ocr_results.json
  vision_results.json
  coverage.json
  progress.json
  run_summary.json
  revision_history.json
```

每个 Agent 把自己的结果写到黑板上，其他 Agent 读取这些结构化结果继续工作。

示例：

```text
Parser Agent -> 写 content.json
OCR Agent -> 写 page_ocr_text / image.ocr_text
Vision Agent -> 写 page_visual_summary / image.visual_summary
Note Writer Agent -> 读 content + OCR + Vision，写 note_blocks
Coverage Agent -> 读 note_blocks + source_map，写 coverage
Refiner Agent -> 根据 coverage 创建局部修订建议
Export Agent -> 导出 Markdown / LaTeX / Word / PDF
```

黑板式适合 SlideNote 的原因：

- 可追溯：每一步结果都有结构化文件。
- 可缓存：Vision/OCR/LLM 结果可以复用。
- 可恢复：运行中断后可以从已有状态继续。
- 可调试：能定位是哪一步写坏了。
- 可视化：GUI 可以直接读取每个阶段的状态。
- 可校验：Coverage Agent 可以检查具体元素是否被覆盖。

### 任务队列

黑板负责保存状态，任务队列负责任务调度。

可能结构：

```json
[
  {
    "task_id": "vision_s12",
    "agent": "vision",
    "input": "screenshots/slide12.png",
    "status": "pending"
  },
  {
    "task_id": "note_section_03",
    "agent": "note_writer",
    "input_pages": [12, 13, 14],
    "status": "pending"
  }
]
```

任务队列用于：

- 并发处理。
- 限速。
- 失败重试。
- 暂停恢复。
- 增量更新。
- GUI 进度显示。

### 结构化审阅回路

有些 Agent 之间需要反馈，但不应该是开放式闲聊，而应该变成结构化任务。

例如：

```text
Coverage Agent 发现第 13 页图片没有解释
  ↓
创建 revision task
  ↓
Refiner Agent 只修改相关 note block
  ↓
Coverage Agent 重新检查
```

这种回路比“Agent 互相聊天”更稳定，也更容易记录版本历史。

### 可能角色

- Parser Agent：检查结构化解析是否完整。
- OCR Agent：读取扫描页、公式图、图片中文字。
- Vision Agent：专门理解图、表、流程图和截图。
- Section Planner Agent：识别章节/小节边界。
- Note Writer Agent：把内容改写成课程笔记。
- Coverage Checker Agent：检查遗漏元素。
- Refiner Agent：统一语言风格、章节结构和排版。
- Export Agent：处理 Markdown、LaTeX、Word、PDF 导出。
- Coordinator：只负责任务分配、依赖检查、并发控制、失败重试，不直接写正文。

### 适合多 Agent 的场景

- 长 PPT。
- 图像驱动型课程。
- 多 PPT / 课程工作区。
- 需要教材辅助的复杂课程。
- 需要交互式编辑和局部修订。
- 需要生成多种产物：完整笔记、精简版、题库、Anki。

### 注意

- 多 Agent 会显著增加 token 成本。
- 必须有缓存、任务边界和中间 JSON。
- 不应让多个 Agent 各自自由发挥，否则可追溯性会变差。
- Agent 输出必须落到结构化文件，而不是只停留在对话历史里。
- 用户手动编辑过的内容应有更高优先级，避免被 Agent 自动覆盖。

## 16. 学习资料衍生输出

在完整笔记之外，可以生成更多学习产物：

- 精简版笔记。
- 考前速览。
- 知识点大纲。
- 易错点列表。
- 选择题。
- 简答题。
- Anki 卡片。
- 术语表。
- 公式表。
- 代码示例整理。

这些应基于 `content.json` 和最终笔记生成，而不是重新读取原始 PPT 后自由总结。

## 17. 覆盖率系统增强

当前覆盖率主要检查元素 ID 是否出现在笔记里。后续可以更智能：

- 检查元素是否只是出现 ID，还是内容真的被解释。
- 检查 OCR / 视觉摘要是否被纳入正文。
- 检查图片是否被保留。
- 检查跨页合并时元素是否有去向。
- 给每个元素标记状态：已覆盖、合并覆盖、仅引用、可能遗漏、低置信度。
- GUI 中直接高亮遗漏元素。

## 18. 学生个人笔记接入

这是一个很有价值的扩展方向。很多学生不是完全没有笔记，而是笔记零散、字迹/排版不好看、缺少结构，或者只记录了老师口头强调的部分。让学生上传个人笔记，可以把 SlideNote 从“只整理课件”扩展成“整合个人学习材料”。

可能输入：

- 手写笔记扫描图。
- iPad / 平板导出的 PDF。
- Markdown / TXT。
- Word 文档。
- 课堂随手截图。
- 语音转写后的课堂记录。

产品价值：

- 个人笔记里常有 PPT 没有的信息，例如老师口头强调、考试提示、个人理解。
- 可以让生成结果更贴近学生自己的学习方式。
- 可以把“老师课件”和“学生笔记”互相补全。
- 适合生成个性化复习清单和易错点。

设计原则：

- 个人笔记不能直接覆盖 PPT 来源，应作为独立来源保留。
- 笔记中要区分“PPT 来源”“个人笔记来源”“AI 补充说明”。
- 如果个人笔记和 PPT 冲突，应标记冲突，而不是静默合并。
- 手写笔记需要 OCR，且要记录置信度。

建议实现路径：

1. 先支持文本型个人笔记：`.md`、`.txt`、可选中文字的 `.pdf`。
2. 再支持图片/扫描笔记 OCR。
3. 最后支持把个人笔记内容和 PPT 元素建立关联。

可能输出：

- `personal_notes.json`
- `personal_note_coverage.json`
- 笔记中的“个人笔记补充”
- PPT 与个人笔记的对应关系

## 19. 来源显示与内容融合策略

当系统同时使用 PPT、个人笔记、教材和 AI 补充时，不同用户对“来源显示”和“内容融合”的偏好会明显不同。

当前基础版已经实现 `source_map.json`，记录笔记块、元素 ID 和来源页之间的映射。后续要继续支持严格/简洁/隐藏来源显示，以及 GUI 点击笔记跳回原页。

一些用户希望严格区分：

- 哪些内容来自 PPT。
- 哪些内容来自自己的个人笔记。
- 哪些内容来自教材或参考书。
- 哪些内容是 AI 补充说明。

另一些用户则希望读到一份顺滑、完整的讲义，不希望正文里反复出现来源标签。还有一些用户希望 PPT 内容和个人笔记融合在一起，但个人笔记又能相对独立地保留。

因此，这不应该被写死成一种输出格式，而应该设计成两个可配置维度。

### 内容融合程度

```text
separated
```

按来源分开。适合想保留个人笔记独立性、做材料审查或课程项目展示的用户。

示例结构：

```markdown
## 传输层概述

### PPT 内容整理
...

### 个人笔记补充
...

### AI 补充说明
...
```

```text
blended
```

同一知识点小节内融合 PPT、个人笔记和 AI 补充，但仍保留来源标注。适合作为默认模式。

```text
integrated
```

完全融合成一篇可读讲义，不按来源分块。适合沉浸阅读和考前快速复习。

### 来源显示程度

```text
strict
```

详细显示来源，例如：

```text
【来源：chapter02.pptx 第 12 页，文本块 s12_t2；个人笔记 note_3；AI 补充】
```

适合保真、审查、学术和怕 AI 编造的场景。

```text
compact
```

正文只显示简短来源，例如：

```text
【对应：PPT 第 12 页；个人笔记第 3 条】
```

适合作为默认显示策略。

```text
hidden
```

正文不显示来源，但仍在结构化 metadata 中保留映射关系。适合只想读顺滑讲义的用户。

### 推荐组合

| 用户偏好 | 内容融合程度 | 来源显示程度 |
| --- | --- | --- |
| 想严格区分来源 | `separated` | `strict` |
| 想读得顺，但仍能追溯 | `blended` | `compact` |
| 只想要完整讲义 | `integrated` | `hidden` |
| 想保留个人笔记独立性 | `separated` | `compact` |
| 做课程项目展示 | `blended` | `strict` |

### 设计原则

- 即使正文隐藏来源，底层也必须保留来源映射。
- 来源显示是呈现层选项，不应影响覆盖率检查。
- AI 补充内容可以融合进正文，但必须能在 metadata 中追溯出来。
- 用户个人笔记可以被融合，但原始个人笔记也应单独保存。
- 后续 LaTeX / Word / HTML 模板也应支持这些显示策略。

可能配置：

```powershell
--fusion-mode blended
--source-display compact
```

可能输出：

- `source_map.json`
- `source_display.json`
- `note_metadata.json`

## 20. 多 PPT / 课程级整合

这个方向非常现实。很多老师不会把课程内容放在一个大 PPT 里，而是按章节、周次、主题分成多个文件。SlideNote 后续应该支持一次输入多个 PPT/PDF，并把它们整合成一个课程级学习材料。

目标形态：

```powershell
python -m slidenote build-course `
  --input "week01.pptx" "week02.pdf" "chapter03.pptx" `
  --out outputs/course
```

或者：

```powershell
python -m slidenote build-course materials/ --out outputs/course
```

需要新增的概念：

- Course：一门课程或一个资料集合。
- Source：单个 PPT/PDF/笔记文件。
- Chapter：从文件名、标题页或用户配置中识别出来的章节。
- Global Element ID：跨文件唯一的元素 ID。

产品价值：

- 自动把分散章节合并成完整课程讲义。
- 支持跨章节引用和知识点重组。
- 能发现重复定义、重复图示、前后术语不一致。
- 更适合期末复习，而不是只处理一次课。

需要注意：

- 多文件顺序需要可控，不能完全依赖文件名猜测。
- 不同 PPT 的页码要保留来源文件名，例如 `chapter02.pptx 第 13 页`。
- 覆盖率检查要从“单文件覆盖率”升级为“课程级覆盖率”。
- 缓存 key 应包含源文件 hash，避免某个文件更新后全课程重跑。

可能输出：

- `course.json`
- `sources.json`
- `course_notes.md`
- `course_coverage.json`
- `chapters/`

## 21. 持续输入与增量更新

这是多 PPT 整合之后很自然的下一步。真实课程材料经常不是一次性发完，而是老师每周、每章、甚至课前课后陆续发布。SlideNote 可以从“一次性生成器”升级为“持续更新的课程工作区”。

目标形态：

```powershell
python -m slidenote init-course outputs/my-course
python -m slidenote add-source outputs/my-course week01.pptx
python -m slidenote add-source outputs/my-course week02.pdf
python -m slidenote update-course outputs/my-course
```

核心能力：

- 新增文件时，只解析新文件。
- 文件内容变化时，只刷新变化的页或章节。
- 已有 OCR / Vision / LLM 缓存尽量复用。
- 课程级笔记可以增量更新，而不是每次全量重写。
- 记录每次更新的变更摘要。

产品价值：

- 更贴近真实学期节奏。
- 用户可以持续把新课件加入同一门课程。
- 期末时已经自然积累出完整课程材料。
- GUI 里可以显示“本周新增了哪些内容”“哪些笔记需要刷新”。

难点：

- 如何稳定识别同一个文件的新旧版本。
- 如何处理用户手动改过的笔记，避免增量更新覆盖人工修改。
- 如何在课程级笔记里插入新章节而不破坏旧结构。
- 如何让跨章节知识关联随新增内容逐步完善。

需要的数据结构：

- 文件 hash。
- 页级 hash。
- 章节索引。
- 更新日志。
- 人工编辑保护标记。
- 每次生成使用的模型、prompt 和缓存 key。

可能输出：

- `course_state.json`
- `update_log.json`
- `changed_pages.json`
- `stale_notes.json`

这个方向非常适合未来 GUI，因为用户可以像维护一个课程项目一样维护自己的学习材料。

## 22. 输入格式扩展

未来可以支持更多输入：

- `.docx` 讲义。
- 图片文件夹。
- 扫描教材 PDF。
- 网页课程资料。
- Markdown / HTML 资料。
- 视频课件截图或字幕。

优先级建议：

1. 扫描 PDF 教材。
2. `.docx` 讲义。
3. 图片文件夹。
4. 视频字幕和截图。

## 23. 本机依赖自动检测

未来 CLI / GUI 都应能自动检测环境：

- Python 版本。
- LibreOffice 是否安装。
- `soffice` 是否在 PATH。
- Windows 是否可用 PowerPoint COM。
- 是否安装 `pywin32`。
- API key 是否配置。
- OCR provider 是否可用。
- 视觉 provider 是否可用。

可以新增命令：

```powershell
python -m slidenote doctor
```

输出本机配置诊断报告，告诉用户缺什么、怎么补。

## 24. 更友好的 API Key 配置

CLI 对新手不够友好，尤其是多个服务商都要 key。

未来方向：

- `.env` 文件支持。
- GUI 设置页保存本地 key。
- 一键测试 key 是否有效。
- 对火山方舟 endpoint、Qwen DashScope、百度 OCR 等给出图形化引导。
- 区分“个人自备 key”和“软件内置托管 key”的产品模式。

安全原则：

- 默认不要把 key 写进输出目录。
- 不要把 key 写进 usage JSON。
- 日志里不要打印完整 key。

## 25. Rust 或其他语言重写的可能性

当前全 Python 是合理的，因为项目重点在文档解析、API 调用、数据管线和快速迭代。

未来只有在这些场景出现时，才考虑 Rust：

- 大量文件批处理性能瓶颈明显。
- GUI 需要更稳定的桌面端打包。
- 本地 OCR / 本地图像预处理很重。
- 需要做长期运行的任务队列或服务端。

更现实的路线：

- 保持 Python 核心。
- 性能热点再局部 Rust 化。
- GUI 可以考虑 Tauri + Python 后端，或 Electron / Web 前端 + Python 服务。

## 建议优先级

### P0：近期最值得做

- `slidenote doctor` 环境检测。基础版已实现，后续补更详细的修复建议和 GUI 诊断。
- 运行进度显示增强：基础版 CLI 实时进度、`progress.json`、`run_summary.json` 已有；后续补 ETA、失败恢复和更细状态。
- 加速与成本调度增强：基础版 `--speed-mode`、`--concurrency`、`--global-cache-dir`、`--refresh-pages` 已有；后续补限速、重试、真正只重跑/只输出指定小节。
- 课程工作区基础模型：Course / Source / Chapter。
- 多 PPT / 多 PDF 课程级整合。
- 更好的 GUI 前置准备：结构化 usage / cache / coverage 输出保持稳定。
- 小节级上下文默认模式：`--context-mode section`、分层缓存、overflow fallback。
- PPT 章节切分与分批输出：`split`、`--split-by section`、`section_index.json`。
- 来源显示与内容融合策略：基础 `source_map.json` 已实现；后续补 `fusion_mode` + `source_display`。
- 更智能的视觉目标选择。基础装饰图过滤已实现；后续补内容图分类和用户选择。
- LaTeX 默认模板设计。

### P1：中期能力

- 教材 RAG 知识库。
- 学科策略 / Domain Profiles：`--domain auto/general/math/medicine/cs`。
- 交互式编辑与对话式微调：局部修订、diff 审阅、用户编辑保护。
- 学生个人笔记接入，先支持文本型笔记和可选中文字的 PDF。
- 持续输入与增量更新，先支持新增文件后只处理新 source。
- GUI 原型。
- 按知识结构重排笔记。
- 覆盖率状态细分。
- 用户可选页级视觉 refresh。

### P2：长期增强

- 多 Agent 工作流。
- Anki / 题库 / 复习资料生成。
- 用户上传 LaTeX / Word 模板。
- 本地 OCR 或更多 OCR API。
- Rust 局部重写或桌面端工程化。

## 关键原则

- AI 不是第一步，解析和校验才是第一步。
- 图像不是装饰，很多课程的灵魂在图里。
- 成本必须可见，缓存必须可复用。
- 可追溯性不能牺牲，即使笔记按知识结构重排。
- 不要只做“总结器”，要做“可检查、可追溯、可复习”的学习材料生成系统。
