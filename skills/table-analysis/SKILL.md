---
name: table-analysis
description: 对项目内 JSON、CSV 记录进行筛选、排序、去重和分组统计，提供可追溯计算依据。
license: MIT
allowed-tools: [list_sources, read_source, table_transform, table_aggregate]
metadata:
  version: "1.0.2"
---

# 表格分析

适用于数量、金额、分类汇总、排名和重复记录分析。先确认资料字段及任务口径。

- 筛选、排序、去重使用真实 MCP table_transform；统计使用 table_aggregate，不用语言模型心算替代。
- table_aggregate 在完整授权输入上统计，table_transform 可能只返回预览，必须注意 total_rows 和 truncated。
- 数值字段不完整或类型不正确时报告问题，不能把无效值当成0。
- 最终 JSON 返回 text、citations、missing_information；计算引用使用 kind=derived_result、tool_call_id、result_checksum、source_id。
- 最终结果注明筛选口径、分组字段和统计值。可调用多次工具验证不同口径。

详细边界见 references/tables.md。技能不提供执行脚本或扩大授权的能力。
