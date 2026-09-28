# Element IR 与 Source Map

SlideNote 用以下产物保留课件内容与笔记的结构化关联：

```text
Ingest: Deck / 截图
    -> Understand: content.json
    -> Write: notes.md（含来源注释）
    -> Guard: coverage.json + 最终 element_ir.json + source_map.json
```

`source_map.json` 需要读取已生成的 `notes.md`，因此在 Guard 阶段建立，而不是笔记的输入。结构化来源有助于定位和复核，不证明解释在语义上完全准确。

## content.json

`content.json` 是 Understand 阶段写出的页面内容清单，记录每页的标题、文本块、表格、图片、截图路径，以及实际启用的 OCR 和视觉理解结果。

它保留解析结果及后续理解步骤写回的字段，适合检查输入材料是否被正确读取和补充。

常见字段包括：

- 页面编号和页面尺寸。
- 文本块、表格、图片。
- 嵌入图片路径和整页截图路径。
- OCR 识别结果。
- Vision 生成的 `visual_summary`。

## element_ir.json

`element_ir.json` 是 Guard 阶段写出的最终 Element IR，供元素检查和后续工具使用。构建中的 prompt 与 coverage 也会从当前 `Deck` 构造需要的元素视图。

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

构建过程中，prompt、coverage 和 source map 会直接从当前 `Deck` 构造需要的元素视图。coverage 阶段结束后写入一次最终 `element_ir.json`，合入 `covered`、`missing`、`marker-only` 等实际状态，避免重复生成中间文件。

## source_map.json

`source_map.json` 记录笔记块和原始元素之间的映射：

```text
note block -> PPT/PDF page -> text/table/image element id
```

默认 `notes.md` 会用隐藏注释写入类似：

```html
<!-- slidenote-source: p4:s4_t1,s4_t2 -->
```

阅读正文时不会被元素 ID 打断；构建中的覆盖检查会使用这些标记，`source_map.json` 可供后续来源高亮和局部 revise 使用。目前 GUI 尚未直接读取该文件。

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
