# 证据格式

原文引用示例：{"source_id":"实际资产ID","source_version":"1","start":0,"end":4,"quote":"原文片段"}。
start/end 是该工具返回的 source_text 字符位置，左闭右开，不是字节位置。
引用必须从 read_source 或 search_sources 返回的真实文本中选取，并通过 locate_evidence 核实。
多个来源有分歧时分别标明。缺少时间、金额或责任人时列入 missing_information，不自行补全。
