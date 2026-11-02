# 参考批次登记与验收

用例任务支持样本演练和指定参考批次两种范围。样本演练验证算法在所给输入上的表现。指定参考批次还要核验全部原件、来源版本及批次规定的数量或期间。资料登记、管理员登记参考批次、任务通过独立审核是三个分别留痕的步骤。

## 登记随附公开材料

在部署主机使用有权读取原件、写入参考清单的操作系统账号，从项目根目录执行以下命令。默认清单为忽略目录内的 `.local/casework/reference-batches.json`。如需指定其他受控私有目录，给服务进程设置 `HANGZHI_REFERENCE_BATCHES_FILE`，并将登记命令的 `--output` 指向同一文件。部署者应将清单及同目录的写权限限于负责登记的管理员，平台服务仅需读取。

```powershell
python -m scripts.register_reference_batches --materials "用例/公开材料" --scope public --version supplied-20261102-v1
```

登记工具对供方随附清单中的文件名、长度和 SHA-256 逐一复核。当前 11 份原件覆盖 B01、B05、B06、T01、T05、G03、G04、G05、G06、G07。G03 同时登记 1979 至 2023 年的逐年期间。G07 从原 CSV 单独统计应解析行数、指定日期窗内行数和日期集合。B06 需要两份指定顺序的版本原件及一份仅含前后原件角色的绑定资料。

## 登记自定义项目批次

其他用例可以按项目登记，由项目管理员在有权写清单的本机账号下执行，也可交部署者代为登记。先将经过项目确认的来源文件放入受控目录，独立核对甲方交付的版本、摘要和所需的数量或期间，再编写 UTF-8 JSON 说明。文件上传接口没有登记参考批次的权限。登记命令也拒绝平台上传目录、符号链接、目录联接和越界路径。

下面以 B02 的一份 CSV 为例。须把两个占位符替换为该原件的同一个 64 位小写 SHA-256，将行数改成批次实际要求。其余 29 种用例使用相同结构。

```json
{
  "schema_version": 1,
  "case_id": "B02",
  "sources": [
    {
      "path": "inputs/observations.csv",
      "sha256": "<verified-64-character-sha256>",
      "source_version": "delivery-rev-3"
    }
  ],
  "conditions": [
    {
      "kind": "record_count",
      "sha256": "<verified-64-character-sha256>",
      "count": 12
    }
  ]
}
```

```powershell
python -m scripts.register_reference_batches --manifest "E:/project-intake/batch.json" --scope project --project-id "<平台项目ID>" --version intake-3
```

管理员需要为每项来源提供说明文件所在目录下的相对路径、原件 SHA-256 和非空来源版本。命令重新读取每份文件计算 SHA-256，只将登记元数据写入清单。审核时，上传的文件字节和登记的 `source_version` 都必须匹配。登记的 1 至 50 个来源必须全部冻结在同一任务中。资料顺序无关，重复、缺件或额外来源均会产生缺口。B06 的显式前后绑定按前述规则处理。若整项业务确需其他参考资料，应先把这些原件加入受控批次再提交任务。

**B06：前后版本各一份，再附术语表或对照表。** 原批次只有两份原件时，不能把临时上传的术语表直接作为第三份来源。项目管理员应使用新的批次版本登记全部原件：恰好一个 `before` 和一个 `after`，每份辅助资料使用 `source`（默认角色），分别核验字节摘要与来源版本。下例展示三份原件，需把三个 SHA 占位符替换为各自真实的小写 64 位十六进制摘要：

```json
{
  "schema_version": 1,
  "case_id": "B06",
  "supplement": "before_after_binding",
  "sources": [
    {"path": "inputs/earlier.pdf", "sha256": "<earlier-sha256>", "source_version": "earlier-2", "role": "before"},
    {"path": "inputs/later.pdf", "sha256": "<later-sha256>", "source_version": "later-2", "role": "after"},
    {"path": "inputs/glossary.csv", "sha256": "<glossary-sha256>", "source_version": "glossary-1", "role": "source"}
  ]
}
```

将说明文件作为 `--manifest`，指定 `--scope project --project-id <项目ID> --version <新批次版本>`；若切换该项目已有的 B06 条目，另加 `--replace`。登记成功后，把三份来源以**登记时的 source_version** 上传到同一项目。再单独创建一份 structured / application/json 的版本绑定资产，其内容只能是以下两行，替换占位符为项目内两份**原件的平台资产 ID**：

```json
[
  {"kind": "document_binding", "target_source_id": "<earlier-asset-id>", "side": "before"},
  {"kind": "document_binding", "target_source_id": "<later-asset-id>", "side": "after"}
]
```

提交时将 **全部三份登记资产和一份绑定资产** 放入 `input_refs`；绑定资产不登记为原件，也不包含术语行或分析答案。术语表同样是必需来源，但不参加版本绑定。绑定到术语表、交换前后 ID、缺件、变更版本、增加未登记的第四份原件或多一行绑定，都不能通过完整来源核验。单任务最多 50 个输入；使用 B06 绑定时可登记的原件上限为 49 份，给绑定资产保留一个名额。来源核验通过不代表条款或译注已通过独立审核。

可选的 `conditions` 支持以下三种检查。独立审核还会运行各用例自身规定的产品、证据和范围核验。

| 条件 | 必填字段 | 核验内容 |
| --- | --- | --- |
| `record_count` | `sha256`、`count` | 指定原件经解析得到的记录数 |
| `product_count` | `product`、`count` | 指定报告产品的项数 |
| `product_values` | `product`、`field`、`values` | 指定产品行中该字段的完整值集合，例如逐年年份或日期 |

## 范围、版本与历史任务

- 同一项目同一用例的专属条目优先于共享 `public` 条目。公开范围只适用于可供所有项目复用的批次元数据。登记不会改变资料分类、读写权限或外发授权，也不会触发联网。
- 同一范围、用例、版本不能换内容。启用新版本时使用新的 `--version` 并显式传 `--replace`。旧清单保存在相邻的 `history/`。重复执行同一命令幂等。清单及其历史应纳入私有运行备份。
- 平台在受理时把当前适用条目、项目、条目摘要和校验摘要冻结到任务快照。参考批次切换后，历史任务复核和自我改进回放沿用各自冻结的条目。校验摘要用于识别误改，登记权限来自部署目录的文件权限。
- 未登记、清单损坏、版本不符、缺件、产品缺期或不完整会进入预检或报告缺口，并得出部分完成或失败。缺少可信参考快照的旧任务不用于自动晋升。新的合格批次可通过新任务受理重新验收。
