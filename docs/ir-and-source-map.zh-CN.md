# Element IR 与 Source Map

SlideNote 的可追溯能力依赖几个稳定结构：

```text
content.json -> element_ir.json -> source_map.json -> notes.md / GUI / coverage
```

它们让正文可以读起来干净，同时保留页面、元素、图片和覆盖率信息。

## content.json

`content.json` 是解析后的基础内容清单，记录每页的标题、文本块、表格、图片、截图路径、OCR 文本和视觉摘要。

它偏向“原始解析结果”，适合调试输入材料是否被正确读取。

常见字段包括：

- 页面编号和页面尺寸。
- 文本块、表格、图片。
- 嵌入图片路径和整页截图路径。
- OCR 识别结果。
- Vision 生成的 `visual_summary`。

## element_ir.json

`element_ir.json` 是统一 Element IR，面向 prompt、coverage、source map、GUI 和后续 Agent 工作流。

每个元素尽量包含：

| 字段 | 说明 |
| --- | --- |
| `element_id` | 稳定元素 ID。 |
| `kind` | text、table、image、figure 等元素类型。 |
| `bbox` | 原始坐标。 |
| `bbox_normalized` | 归一化坐标，方便跨页面尺寸处理。 |
| `role` | 主语义角色。 |
| `roles` | 更细的角色列表。 |
| `confidence` | 当前判断置信度。 |
| `reading_order` | 阅读顺序。 |
| `coverage_state` | coverage 阶段后的 covered / missing / marker-only 等状态。 |
| `evidence` | 用于判断角色、覆盖或来源的证据。 |
| `source_ids` | 指向原始解析对象的来源 ID。 |

当前 `schema_version` 仍保持为 `1`，新增能力通过 `schema_features` 标明，减少过早 schema 版本膨胀。

## 构建时机

IR 会在两个阶段写入：

1. `export_content` 阶段写基础 IR，供后续 prompt、source map 和 coverage 使用。
2. coverage 阶段结束后刷新最终 IR，合入 `covered`、`missing`、`marker-only` 等实际状态。

这样最终 `element_ir.json` 不是只停留在前置状态，而是反映生成后的覆盖结果。

## source_map.json

`source_map.json` 记录笔记块和原始元素之间的映射：

```text
note block -> PPT/PDF page -> text/table/image element id
```

默认 `notes.md` 会用隐藏注释写入类似：

```html
<!-- slidenote-source: p4:s4_t1,s4_t2 -->
```

阅读正文时不会被元素 ID 打断，但 GUI、coverage、导出和局部 revise 仍能找到来源。

## 图片资产

SlideNote 会保留多种图片来源：

```text
notes.assets/
figures/
images/
screenshots/
```

图片处理原则：

- 原始图片尽量保留。
- 疑似 logo、小图标、背景碎片会标记为 decorative / ignored。
- 组合图会尽量裁成一个完整教学单元，而不是把零散小图都插进正文。
- 局部图裁剪和 figure grounding 会尽量让图片靠近相关知识点。

## 后续用途

统一 IR 和 source map 是后续能力的地基：

- GUI 逐页审阅和来源高亮。
- 局部 revise，只重写某一页或某一节。
- Agent backend 根据 artifact registry 调用不同阶段。
- coverage repair 只修漏项，不重写全文。
- review/exam 题目引用原始来源。
