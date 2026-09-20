# 航智界面方向

2026-09-20，用户选定“航智 · 航空情报平台”，采用 D 的深色协作工作台，流程编辑器结合 C 的清晰节点设计。

## 四种参考

| 方向 | 官方参考 | 适用特点 |
| --- | --- | --- |
| A：专业情报工作台 | [Palantir Workshop](https://www.palantir.com/docs/foundry/workshop/getting-started) | 专题任务、对象与证据的分栏组织 |
| B：企业研究控制台 | [IBM Carbon](https://carbondesignsystem.com/components/data-table/style/) | 栅格、表格和操作层级 |
| C：智能体工作室 | [Dify Workflows](https://www.dify.ai/workflows) | 清晰节点与流程编排 |
| D：深色协作工作台（已选） | [Linear 官方设计更新](https://linear.app/now/behind-the-latest-design-refresh) | 中性深灰、低饱和强调色、清晰任务层次 |

`frontend/public/style-options.html` 是使用合成航空情报场景制作的原创布局预览，不是原产品截图，也不表示上述产品对本项目提供支持。浏览器访问 `/style-options.html` 可对照查看。

## 落地约定

- 以炭灰背景、分层表面和克制紫色作为全站基础；状态色同时配合文字与图标，不仅依赖颜色表达。
- 正文、表格、表单与主要按钮使用 15–16 px，辅助文字至少 13 px；标题建立 24–32 px 层次。流程画布缩放时节点文本随画布缩放。
- 流程节点保留明确类型、配置摘要、输入输出连接点以及选中状态。布局和交互沿用真实工作流数据，样式修改不改变执行语义。
- 长篇报告使用浅色纸面和深色正文，保持段落、引用和证据阅读对比度。
- 登录与注册采用普通账号入口，清空预填凭据，不展示演示账号说明或技术实现提示。注册默认研究员权限，不允许自行选择管理员。
- 不引入外部字体或运行时远程视觉资源；参考链接仅供阅读，页面本身不向上述平台发送任务数据。
