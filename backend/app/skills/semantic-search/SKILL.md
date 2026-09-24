# 语义检索

## 任务方法

本地中文 BGE 向量检索：

1. `prepare_only` 为真时只调用 `local.embedding.prepare` 准备本地模型并返回
   embedding 状态，不做检索。
2. 否则调用 `local.knowledge.semantic_search`：对候选分块计算向量（按内容哈希
   缓存）、与查询向量做余弦相似度排序，返回 Evidence。

## 边界

- 模型下载可联网，文档嵌入始终在本地运行；模型不可用时如实报错，不静默
  降级为关键词检索。
- 只读本地存储。
