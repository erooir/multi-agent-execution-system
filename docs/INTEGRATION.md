# 上下游接入契约 v2

所有服务默认仅监听127.0.0.1，部署到受控内网前应配置身份、成员和网络边界。API前缀/api/v2；认证使用/api/auth/login返回的HttpOnly会话。不能通过前端按钮代替权限验证。机器服务身份须作为独立受限账号配置，不共享管理员凭据。

## 数据
使用data_asset.schema.json，必填project_id/name/modality/content/source_id/provenance。source_version默认1；classification默认local。原始媒体通过/data-assets/upload的multipart file上传，另附project_id/source_id/provenance等元数据。文件路径不是外部可指定的content_ref。缺字段返回422，越权403，已撤回或过期409，过大413。

JSON/CSV使用UTF-8，结构化内容是对象数组或records对象。媒体必须声明真实模态，不把未处理的媒体伪装成文本。文本或结构化输入由程序校验，供应方应给出合法、缺字段、过期、撤回与重复样例。原始材料不自动获得云端处理授权。

## 任务与结果
使用task_request.schema.json。project_id/name/goal/task_type/input_refs/idempotency_key描述业务请求。截止时间以受理时刻起算，费用包括执行与评审。重复幂等键不创建重复任务；异内容409。

POST /tasks返回202和TaskRun对象，以其中id作为task_id。GET /tasks/{id}读取持久状态，GET /tasks/{id}/result读取ResultBundle。GET /tasks/{id}/events?after=N返回可续传事件；/stream支持Last-Event-ID。产物必须经过/artifacts/{id}权限检查，不公开文件存储路径。

成功以schema、来源和自动评审为准。reason_code包括insufficient_data、unsupported_capability、quality_not_met、deadline_exceeded、budget_exhausted、execution_uncertain、data_egress_blocked等。不要把非成功任务转换为成功回调。当前版本未启用外部副作用回调，避免未经协议约定的重复写入。

## 知识服务
本地知识连接器检索已授权、未撤回且未过期的数据。HTTP连接器由管理员配置，主机必须在部署环境HANGZHI_KNOWLEDGE_HOSTS白名单；平台向base_url/search发送POST {query,limit,project_id}。响应为{items:[{id,text,source_id,source_version,provenance}]}，可附带位置字段。响应上限1MB、超时10秒、禁止重定向；不把上游凭据放入模型上下文。

真实内网服务的鉴权、分页、增量、撤回推送与服务等级应由双方提供样例后扩展。当前适配器不能据此宣称已经接通上游。

## 契约发布
docs/contracts中的JSON Schema由 scripts/export_contracts.py 从Pydantic生成。CI检查漂移。这里只包含公开工程契约，不复制私有方案全文。
