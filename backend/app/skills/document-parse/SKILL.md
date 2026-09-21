# 文档解析

## 任务方法

解析调用方显式指定的资料（PDF、DOCX、TXT、Markdown、CSV、JSON）：

1. 校验每个 document_id 存在且属于当前项目（若提供项目范围）。
2. 调用 `local.document.read_chunks` 读取入库分块并组装为 Evidence。
3. 返回 Evidence 列表与数量；需要模型摘要时由调用方另行发起，
   本技能只返回解析内容，不触发任何付费调用。

## 边界

- 只处理显式给出的 document_ids，绝不自动挑选项目文档。
- 只读本地存储与上传文件，不发起网络请求。
