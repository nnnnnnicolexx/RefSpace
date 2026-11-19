---
name: refspace-usage
description: 引知 RefSpace 多格式资料空间查询与表格精确统计操作指南。当需要跨本机文件夹、文档（MD/DOCX/PDF）与表格（Excel/CSV）执行定位、去重汇总或登记新资料空间时读取。
---

# 引知 RefSpace 使用指南

## 1. 五个核心 MCP 原语

| 工具名 | 用途 |
| --- | --- |
| `refspace_scope_status` | 查看当前窗口会话绑定的资料空间，以及本机已登记的全部资料空间与可读状态 |
| `refspace_bind_scope` | 绑定/替换（`bind`/`replace`）、追加（`add`）、移除（`remove`）、单次临时使用（`once`）、清空（`clear`），或首次登记新空间（`register_space`） |
| `refspace_locate` | 在当前会话范围内扫描和搜索相关文件、工作表字段摘要或文档大纲 |
| `refspace_read_doc` | 按章节或关键词提取 `.md`、`.txt`、`.docx`、`.pdf`、`.json` 的段落内容与溯源 |
| `refspace_query_table` | 对 `.xlsx`、`.xlsm`、`.csv` 执行结构探测（`inspect`）、筛选取行（`rows`）或分组聚合计算（`aggregate`） |

## 2. 复杂 Excel 统计口径规范

1. 先调用 `refspace_query_table(file=..., mode="inspect")` 确认工作表、独立数据区域（`region_index`，避免把同页旁置汇总与明细混算）、有效字段及公式缺值警告（`warnings`）。
2. 遇到拆箱或多批次明细表时，若单据级金额/应发数量在多行重复出现，使用 `dedup_by`（如 `["ERP单号", "行号", "SKU"]`）去重后再执行 `sum`，或直接使用批次发货数字段求和。
3. 若返回 `warnings` 提示公式单元格缺少缓存计算值，必须在回复中明确告知用户该影响，不得自行猜算缺失结果。
