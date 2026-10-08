# 2026-10-07 复核修复说明

本文件记录 `SlideNote-main-P0-reviewed-fixed` 相对初始 P0 修改包的第二轮独立复核修复。

## 已修复问题

1. **结构修复防截断**：全文修订必须通过结构契约，正文保留率不得低于 80%，并精确保留原来源标记、图片目标及既有 coverage；修订后重新修复图片链接并执行 figure grounding。
2. **评测路径安全**：`case_id` 使用白名单格式，所有 case 输出/失败目录在删除或写入前验证仍位于预期根目录内。
3. **评测失败关闭**：关键 artifact 缺失、JSON 无效或关键字段缺失时直接判定失败，不再把缺失值当作 0。
4. **报告一致性**：逐页标题列直接依据实际检测结果；local 模式显示“有（未作为硬门槛）”。
5. **结构契约误判**：支持 `Slide 1 - Topic` 等格式；全局结构栏目必须是 H2；“这不是总结”和单独“例子与应用”不再被误判为合格的 summary/topic；成组重复 H3 模板会使机械结构检查失败。
6. **路径兼容性**：`--out`、manifest 和 baseline 可位于仓库外，不再因 `relative_to()` 抛错。
7. **CI 完整性**：CI 安装 `gui` extra，Streamlit 测试不再被跳过。
8. **排版检查证据**：新增 Markdown、DOCX、PDF 三层自动回归测试。
9. **干净 Git diff**：文本统一 LF，新增 `.gitattributes`；PowerShell 脚本声明 CRLF。
10. **空章节误报**：父级标题下直接进入有正文的子标题不再被错误标记为空章节。

## 验证结果

- `python -m pytest -q`：354 passed。
- `python scripts/smoke_first_run.py`：通过。
- 合成样本 baseline/after：1/1 通过，绝对输出路径与 baseline 对比正常。
- 排版检查：当前 markdown-zip 运行 0 error、0 warning；docx/PDF 实际导出因该 manifest 未请求而跳过，但三层检测器均已有自动 fixture 覆盖。

## 仍需真实环境完成

合成 local 样本不能证明 LLM 讲义内容更准确。发布“质量已提升”的对外结论前，仍需使用固定模型和参数，对至少 3–5 份真实 lecture 课件完成修改前/修改后生成、双人独立 rubric、关键事实与来源抽查。该项需要真实课件和 API key，不应以合成测试替代。
