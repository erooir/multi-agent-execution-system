# 表格口径

支持对象数组、{"records":[...]}、UTF-8 CSV/JSONL；每次最多10000行且输入不超过2MB。
筛选 where 按字段精确匹配，CSV 的字段值通常是字符串；统计支持 JSON 数值及有限的十进制数值字符串，空值、布尔值和非数值均会明确报错。
计算证据来自真实 MCP 响应，不编造 quote。引用完整拷贝工具返回的 derived_evidence。
table_transform 的 operation 仅为 filter、sort、deduplicate。where 总是先筛选，指定 sort_by 后会按 descending 排序；例如筛选A组并降序，传 operation=filter、where={"team":"A"}、sort_by=amount、descending=true。
每次调用独立读取 source_id，后一次调用不自动继承前一次的筛选条件，必须重传 where。
sort_type=auto 自动识别有限数值及数字字符串；numeric 强制数值排序，非法值明确失败；text 按文本排序。空值始终在最后。row_numbers 是从1开始的原始记录序号，CSV不计表头。
空分组平均值、最小值和最大值为 null。记录缺失、类型错误和超限必须如实说明。
