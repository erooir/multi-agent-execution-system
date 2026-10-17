import { useState } from "react";
import { Plus, Trash2 } from "lucide-react";
import { api, post, put, idPath } from "./api";
import { parseObject, splitList, taskTypes } from "./domain";
import type { Item, Mutation } from "./types";
import { Field, FormModal } from "./ui";
const modalities: Record<string, string> = {
  text: "文本",
  structured: "结构化数据",
  image: "图像",
  audio: "音频",
  video: "视频",
};
type Common = { projectId: string; onClose: () => void; mutate: Mutation };
export function AgentForm({
  projectId,
  item,
  agents,
  capabilities,
  onClose,
  mutate,
}: Common & { item?: Item; agents: Item[]; capabilities: Item[] }) {
  const [form, setForm] = useState<Item>({
    name: "",
    description: "",
    purpose: "general",
    structure: "atomic",
    capabilities: ["structured_transform"],
    modalities: ["structured"],
    tool_ids: capabilities
      .filter((c) => c.id === "structured_transform" && c.tool_id)
      .map((c) => c.tool_id),
    instructions: "",
    model_policy: "local",
    members: [],
    ...item,
  });
  const update = (key: string, value: any) =>
    setForm({ ...form, [key]: value });
  const [parameters, setParameters] = useState(
    JSON.stringify(item?.parameters || {}, null, 2),
  );
  const toggle = (key: string, value: string) =>
    update(
      key,
      form[key].includes(value)
        ? form[key].filter((x: string) => x !== value)
        : [...form[key], value],
    );
  return (
    <FormModal
      wide
      title={item ? "编辑智能体定义" : "创建智能体"}
      onClose={onClose}
      submit="保存定义"
      onSubmit={() => {
        const body = {
          ...Object.fromEntries(
            [
              "name",
              "description",
              "purpose",
              "structure",
              "capabilities",
              "modalities",
              "instructions",
              "model_policy",
            ].map((k) => [k, form[k]]),
          ),
          tool_ids: form.tool_ids.filter(Boolean),
          members: form.structure === "composite" ? form.members : [],
          parameters: parseObject(parameters, "工具参数"),
        };
        return mutate(
          () =>
            item
              ? put(idPath("agents", item.id), {
                  ...body,
                  project_id: projectId,
                })
              : post("/v2/agents", { ...body, project_id: projectId }),
          "智能体定义已保存",
        );
      }}
    >
      <div className="form-grid">
        <Field label="名称">
          <input
            required
            maxLength={100}
            value={form.name}
            onChange={(e) => update("name", e.target.value)}
          />
        </Field>
        <Field label="执行策略">
          <select
            value={form.model_policy}
            onChange={(e) => update("model_policy", e.target.value)}
          >
            <option value="local">本地确定性工具</option>
            <option value="cloud">统一网关模型</option>
          </select>
        </Field>
        <Field label="用途分类">
          <select
            value={form.purpose}
            onChange={(e) => update("purpose", e.target.value)}
          >
            <option value="general">通用智能体</option>
            <option value="specialized">任务专用智能体</option>
          </select>
        </Field>
        <Field label="结构分类">
          <select
            value={form.structure}
            onChange={(e) => {
              update("structure", e.target.value);
            }}
          >
            <option value="atomic">元智能体</option>
            <option value="composite">复合智能体</option>
          </select>
        </Field>
      </div>
      <Field label="职责说明">
        <textarea
          value={form.description}
          maxLength={2000}
          onChange={(e) => update("description", e.target.value)}
        />
      </Field>
      <div className="field">
        <span>任务能力</span>
        <div className="check-grid">
          {Object.entries(taskTypes).map(([id, label]) => (
            <label className="check" key={id}>
              <input
                type="checkbox"
                checked={form.capabilities.includes(id)}
                onChange={() => toggle("capabilities", id)}
              />
              {label}
            </label>
          ))}
        </div>
        <small>声明能力不代表底层模型已验证；运行时仍检查能力目录。</small>
      </div>
      <div className="field">
        <span>输入模态</span>
        <div className="check-grid">
          {Object.entries(modalities).map(([id, label]) => (
            <label className="check" key={id}>
              <input
                type="checkbox"
                checked={form.modalities.includes(id)}
                onChange={() => toggle("modalities", id)}
              />
              {label}
            </label>
          ))}
        </div>
      </div>
      <Field label="允许工具 ID" hint="工具由服务端白名单验证，以逗号分隔。">
        <input
          value={form.tool_ids.join(", ")}
          onChange={(e) =>
            update(
              "tool_ids",
              e.target.value.split(",").map((x) => x.trim()),
            )
          }
          placeholder={capabilities
            .map((c) => c.tool_id)
            .filter(Boolean)
            .join(", ")}
        />
      </Field>
      <Field label="执行指令">
        <textarea
          rows={5}
          maxLength={8000}
          value={form.instructions}
          onChange={(e) => update("instructions", e.target.value)}
          placeholder="说明职责、输出约束与来源要求"
        />
      </Field>
      <Field
        label="工具参数 JSON"
        hint="允许 operation、select_fields、filter、sort_by、descending、deduplicate、max_chars；参数由服务端白名单验证。"
      >
        <textarea
          className="mono"
          rows={4}
          value={parameters}
          onChange={(e) => setParameters(e.target.value)}
        />
      </Field>
      {form.structure === "composite" && (
        <div className="field">
          <span>固定成员版本</span>
          <div className="selection-list">
            {agents
              .filter((a) => a.id !== item?.id && a.status === "published")
              .map((a) => (
                <label className="check" key={a.id}>
                  <input
                    type="checkbox"
                    checked={form.members.some(
                      (m: Item) => m.agent_id === a.id,
                    )}
                    onChange={() =>
                      update(
                        "members",
                        form.members.some((m: Item) => m.agent_id === a.id)
                          ? form.members.filter(
                              (m: Item) => m.agent_id !== a.id,
                            )
                          : [
                              ...form.members,
                              { agent_id: a.id, version: a.current_version },
                            ],
                      )
                    }
                  />
                  {a.name}
                  <small>v{a.current_version}</small>
                </label>
              ))}
          </div>
          <small>发布时检查循环、深度与每个成员的有效权限。</small>
        </div>
      )}
      <p className="note">
        保存修改只更新定义。发布生成新的不可变版本，已经运行的任务继续使用原有快照。
      </p>
    </FormModal>
  );
}
export function TaskForm({
  projectId,
  assets,
  workflows,
  onClose,
  mutate,
  schedule = false,
  afterCreate,
}: Common & {
  assets: Item[];
  workflows: Item[];
  schedule?: boolean;
  afterCreate?: (item: Item) => void;
}) {
  const [form, setForm] = useState<Item>({
    name: "",
    goal: "",
    task_type: "structured_transform",
    input_refs: [],
    workflow_id: "",
    mode: "local",
    idempotency_key: crypto.randomUUID(),
    deadline_seconds: 300,
    max_cost_cny: 0,
    max_revisions: 1,
    allow_partial: false,
  });
  const [quality, setQuality] = useState({
      min_records: 1,
      min_length: 1,
      required_fields: "",
    }),
    [interval, setInterval] = useState(86400);
  const update = (key: string, value: any) =>
    setForm({ ...form, [key]: value });
  return (
    <FormModal
      wide
      title={schedule ? "创建持久计划任务" : "提交新任务"}
      onClose={onClose}
      submit={schedule ? "创建计划" : "提交并自动执行"}
      onSubmit={async () => {
        const task = {
          ...form,
          project_id: projectId,
          workflow_id: form.workflow_id || null,
          quality: {
            ...quality,
            required_fields: splitList(quality.required_fields),
          },
        };
        const result = await mutate(
          () =>
            post(
              schedule ? "/v2/schedules" : "/v2/tasks",
              schedule
                ? {
                    project_id: projectId,
                    name: form.name,
                    interval_seconds: interval,
                    timezone: "Asia/Shanghai",
                    task_template: task,
                  }
                : task,
            ),
          schedule ? "计划任务已创建" : "任务已受理",
        );
        afterCreate?.(result);
      }}
    >
      <div className="form-grid">
        <Field label="任务名称">
          <input
            required
            maxLength={100}
            value={form.name}
            onChange={(e) => update("name", e.target.value)}
          />
        </Field>
        <Field label="任务类型">
          <select
            value={form.task_type}
            onChange={(e) =>
              setForm({ ...form, task_type: e.target.value, workflow_id: "" })
            }
          >
            {Object.entries(taskTypes).map(([id, label]) => (
              <option value={id} key={id}>
                {label}
              </option>
            ))}
          </select>
        </Field>
      </div>
      <Field label="任务目标">
        <textarea
          required
          rows={3}
          maxLength={8000}
          value={form.goal}
          onChange={(e) => update("goal", e.target.value)}
          placeholder="具体描述期望产物和处理范围"
        />
      </Field>
      <div className="field">
        <span>输入数据 · 选择已登记的有效资产</span>
        <div className="selection-list">
          {assets
            .filter((a) => a.status === "active")
            .map((a) => (
              <label className="check" key={a.id}>
                <input
                  type="checkbox"
                  checked={form.input_refs.includes(a.id)}
                  onChange={() =>
                    update(
                      "input_refs",
                      form.input_refs.includes(a.id)
                        ? form.input_refs.filter((id: string) => id !== a.id)
                        : [...form.input_refs, a.id],
                    )
                  }
                />
                <span>{a.name}</span>
                <small>
                  {modalities[a.modality]} ·{" "}
                  {a.classification === "public" ? "公开" : "仅本地"}
                </small>
              </label>
            ))}
          {!assets.some((a) => a.status === "active") && (
            <p className="muted">
              当前项目尚无有效数据。请先在「数据资产」中登记或上传。
            </p>
          )}
        </div>
      </div>
      <div className="form-grid">
        <Field label="执行模式">
          <select
            value={form.mode}
            onChange={(e) =>
              setForm({
                ...form,
                mode: e.target.value,
                max_cost_cny: e.target.value === "cloud" ? 1 : 0,
              })
            }
          >
            <option value="local">本地工具 · 无模型推理</option>
            <option value="cloud">云端模型 · 计入累计预算</option>
            <option value="rehearsal">配置演练 · 非推理结果</option>
          </select>
        </Field>
        <Field label="复用流程">
          <select
            value={form.workflow_id}
            onChange={(e) => update("workflow_id", e.target.value)}
          >
            <option value="">自动匹配能力与流程</option>
            {workflows
              .filter(
                (w) =>
                  w.status === "published" && w.task_type === form.task_type,
              )
              .map((w) => (
                <option key={w.id} value={w.id}>
                  {w.name} · v{w.current_version}
                </option>
              ))}
          </select>
        </Field>
      </div>
      {form.mode === "cloud" && (
        <p className="warning">
          只允许外发已标记公开的数据。执行、自动评审与修订均经过统一网关并计入历史累计
          300 元上限。
        </p>
      )}
      {form.mode === "rehearsal" && (
        <p className="warning">
          演练只验证配置和调用路径，产物明确标记为演练，不代表离线模型推理或任务质量。
        </p>
      )}
      <h3>资源与质量约束</h3>
      <div className="form-grid three">
        <Field label="端到端时限（秒）">
          <input
            type="number"
            min={1}
            max={86400}
            value={form.deadline_seconds}
            onChange={(e) => update("deadline_seconds", Number(e.target.value))}
          />
        </Field>
        <Field label="任务预算（人民币）">
          <input
            type="number"
            min={0}
            max={10}
            step="0.01"
            disabled={form.mode !== "cloud"}
            value={form.max_cost_cny}
            onChange={(e) => update("max_cost_cny", Number(e.target.value))}
          />
        </Field>
        <Field label="最多质量修订轮次">
          <input
            type="number"
            min={0}
            max={2}
            value={form.max_revisions}
            onChange={(e) => update("max_revisions", Number(e.target.value))}
          />
        </Field>
        <Field label="最少记录数">
          <input
            type="number"
            min={0}
            value={quality.min_records}
            onChange={(e) =>
              setQuality({ ...quality, min_records: Number(e.target.value) })
            }
          />
        </Field>
        <Field label="最少文本长度">
          <input
            type="number"
            min={0}
            value={quality.min_length}
            onChange={(e) =>
              setQuality({ ...quality, min_length: Number(e.target.value) })
            }
          />
        </Field>
        <Field label="必需字段（逗号分隔）">
          <input
            value={quality.required_fields}
            onChange={(e) =>
              setQuality({ ...quality, required_fields: e.target.value })
            }
          />
        </Field>
      </div>
      <label className="check">
        <input
          type="checkbox"
          checked={form.allow_partial}
          onChange={(e) => update("allow_partial", e.target.checked)}
        />
        允许部分交付，明确列出缺失范围
      </label>
      {schedule && (
        <Field
          label="执行间隔（秒）"
          hint="由服务端持久调度，时区 Asia/Shanghai；无需保持页面打开。"
        >
          <input
            type="number"
            min={60}
            max={31536000}
            value={interval}
            onChange={(e) => setInterval(Number(e.target.value))}
          />
        </Field>
      )}
      <details>
        <summary>请求标识</summary>
        <Field label="幂等键" hint="相同项目内同键同内容只创建一个任务。">
          <input
            required
            value={form.idempotency_key}
            onChange={(e) => update("idempotency_key", e.target.value)}
          />
        </Field>
      </details>
    </FormModal>
  );
}
export function AssetForm({ projectId, onClose, mutate }: Common) {
  const [upload, setUpload] = useState(false),
    [file, setFile] = useState<File | null>(null);
  const [form, setForm] = useState<Item>({
    name: "",
    modality: "structured",
    media_type: "application/json",
    content: "",
    source_id: "",
    source_version: "1",
    provenance: "",
    classification: "local",
    retention_days: 30,
  });
  const update = (key: string, value: any) =>
    setForm({ ...form, [key]: value });
  return (
    <FormModal
      wide
      title="登记数据资产"
      onClose={onClose}
      submit="验证并登记"
      onSubmit={async () => {
        if (upload) {
          if (!file) throw new Error("请选择要上传的文件");
          const data = new FormData();
          Object.entries({ ...form, project_id: projectId })
            .filter(([k]) => !["content", "media_type"].includes(k))
            .forEach(([k, v]) => data.append(k, String(v)));
          data.append("file", file);
          return mutate(
            () => api("/v2/data-assets/upload", { method: "POST", body: data }),
            "文件已登记",
          );
        }
        const content =
          form.modality === "structured"
            ? JSON.parse(form.content)
            : form.content;
        return mutate(
          () =>
            post("/v2/data-assets", {
              ...form,
              project_id: projectId,
              content,
            }),
          "数据资产已登记",
        );
      }}
    >
      <div className="segmented">
        <button
          type="button"
          className={!upload ? "selected" : ""}
          onClick={() => {
            setUpload(false);
            if (!["text", "structured"].includes(form.modality))
              setForm({
                ...form,
                modality: "structured",
                media_type: "application/json",
              });
          }}
        >
          文本 / JSON
        </button>
        <button
          type="button"
          className={upload ? "selected" : ""}
          onClick={() => setUpload(true)}
        >
          上传文件
        </button>
      </div>
      <div className="form-grid">
        <Field label="资产名称">
          <input
            required
            value={form.name}
            maxLength={150}
            onChange={(e) => update("name", e.target.value)}
          />
        </Field>
        <Field label="模态">
          <select
            value={form.modality}
            onChange={(e) =>
              setForm({
                ...form,
                modality: e.target.value,
                media_type:
                  e.target.value === "structured"
                    ? "application/json"
                    : "text/plain",
              })
            }
          >
            {Object.entries(modalities)
              .filter(([id]) => upload || ["text", "structured"].includes(id))
              .map(([id, label]) => (
                <option key={id} value={id}>
                  {label}
                </option>
              ))}
          </select>
        </Field>
      </div>
      {upload ? (
        <Field
          label="选择文件"
          hint="上限 10 MB；文本、JSON、CSV 使用 UTF-8 编码。"
        >
          <input
            type="file"
            required
            onChange={(e) => {
              const selected = e.target.files?.[0] || null;
              setFile(selected);
              if (selected) {
                const kind = selected.type.startsWith("image/")
                  ? "image"
                  : selected.type.startsWith("audio/")
                    ? "audio"
                    : selected.type.startsWith("video/")
                      ? "video"
                      : /\.(json|csv|jsonl)$/i.test(selected.name)
                        ? "structured"
                        : "text";
                setForm({
                  ...form,
                  name: form.name || selected.name,
                  modality: kind,
                });
              }
            }}
          />
        </Field>
      ) : (
        <Field
          label={form.modality === "structured" ? "JSON 内容" : "文本内容"}
        >
          <textarea
            required
            rows={8}
            className="mono"
            value={form.content}
            onChange={(e) => update("content", e.target.value)}
            placeholder={
              form.modality === "structured"
                ? '[{"name":"公开样例","value":1}]'
                : "输入有权处理的原始文本"
            }
          />
        </Field>
      )}
      <div className="form-grid">
        <Field label="来源标识">
          <input
            required
            value={form.source_id}
            onChange={(e) => update("source_id", e.target.value)}
            placeholder="上游系统 / 数据集标识"
          />
        </Field>
        <Field label="来源版本">
          <input
            required
            value={form.source_version}
            onChange={(e) => update("source_version", e.target.value)}
          />
        </Field>
        <Field label="数据范围">
          <select
            value={form.classification}
            onChange={(e) => update("classification", e.target.value)}
          >
            <option value="local">仅本地 · 禁止云端外发</option>
            <option value="public">公开 · 可用于网关模型</option>
          </select>
        </Field>
        <Field label="保留期限（天）">
          <input
            type="number"
            min={1}
            max={3650}
            value={form.retention_days}
            onChange={(e) => update("retention_days", Number(e.target.value))}
          />
        </Field>
      </div>
      <Field label="来源及授权说明">
        <textarea
          required
          maxLength={1000}
          value={form.provenance}
          onChange={(e) => update("provenance", e.target.value)}
          placeholder="记录数据来自何处、处理依据及定位信息"
        />
      </Field>
    </FormModal>
  );
}
export function WorkflowForm({
  projectId,
  item,
  agents,
  onClose,
  mutate,
}: Common & { item?: Item; agents: Item[] }) {
  const [form, setForm] = useState<Item>({
    name: "",
    description: "",
    task_type: "structured_transform",
    nodes: [],
    ...item,
  });
  const [configTexts, setConfigTexts] = useState<Record<string, string>>(
    Object.fromEntries(
      (item?.nodes || []).map((n: Item) => [
        n.id,
        JSON.stringify(n.config || {}, null, 2),
      ]),
    ),
  );
  const update = (key: string, value: any) =>
    setForm({ ...form, [key]: value });
  const nodeUpdate = (i: number, key: string, value: any) =>
    update(
      "nodes",
      form.nodes.map((n: Item, index: number) =>
        index === i ? { ...n, [key]: value } : n,
      ),
    );
  return (
    <FormModal
      wide
      title={item ? "编辑流程定义" : "创建复用流程"}
      onClose={onClose}
      submit="保存流程"
      onSubmit={() => {
        if (!form.nodes.length) throw new Error("请至少添加一个节点");
        const body = {
          project_id: projectId,
          name: form.name,
          description: form.description,
          task_type: form.task_type,
          nodes: form.nodes.map((n: Item) => ({
            id: n.id,
            label: n.label,
            agent_id: n.agent_id,
            depends_on: n.depends_on || [],
            config: parseObject(
              configTexts[n.id] || JSON.stringify(n.config || {}),
              "节点配置",
            ),
          })),
        };
        return mutate(
          () =>
            item
              ? put(idPath("workflows", item.id), body)
              : post("/v2/workflows", body),
          "流程定义已保存",
        );
      }}
    >
      <div className="form-grid">
        <Field label="流程名称">
          <input
            required
            maxLength={100}
            value={form.name}
            onChange={(e) => update("name", e.target.value)}
          />
        </Field>
        <Field label="任务类型">
          <select
            value={form.task_type}
            onChange={(e) => update("task_type", e.target.value)}
          >
            {Object.entries(taskTypes).map(([id, label]) => (
              <option key={id} value={id}>
                {label}
              </option>
            ))}
          </select>
        </Field>
      </div>
      <Field label="流程说明">
        <textarea
          value={form.description}
          onChange={(e) => update("description", e.target.value)}
        />
      </Field>
      <div className="panel-title">
        <h3>协作节点</h3>
        <button
          type="button"
          onClick={() =>
            update("nodes", [
              ...form.nodes,
              {
                id: `node-${crypto.randomUUID().slice(0, 8)}`,
                label: "处理节点",
                agent_id:
                  agents.find((a) => a.status === "published")?.id || "",
                depends_on: [],
                config: {},
              },
            ])
          }
        >
          <Plus size={16} />
          添加节点
        </button>
      </div>
      {form.nodes.map((node: Item, i: number) => (
        <div className="node-edit" key={node.id}>
          <div className="row-between">
            <code>{node.id}</code>
            <button
              type="button"
              className="icon-button"
              aria-label={`删除节点 ${node.label}`}
              onClick={() =>
                update(
                  "nodes",
                  form.nodes
                    .filter((_: Item, n: number) => n !== i)
                    .map((n: Item) => ({
                      ...n,
                      depends_on: n.depends_on.filter(
                        (id: string) => id !== node.id,
                      ),
                    })),
                )
              }
            >
              <Trash2 size={16} />
            </button>
          </div>
          <div className="form-grid">
            <Field label="节点名称">
              <input
                required
                value={node.label}
                onChange={(e) => nodeUpdate(i, "label", e.target.value)}
              />
            </Field>
            <Field label="绑定智能体">
              <select
                required
                value={node.agent_id}
                onChange={(e) => nodeUpdate(i, "agent_id", e.target.value)}
              >
                <option value="">选择已发布智能体</option>
                {agents
                  .filter((a) => a.status === "published")
                  .map((a) => (
                    <option key={a.id} value={a.id}>
                      {a.name} · v{a.current_version}
                    </option>
                  ))}
              </select>
            </Field>
          </div>
          <div className="field">
            <span>前置依赖</span>
            <div className="check-grid">
              {form.nodes
                .filter((n: Item) => n.id !== node.id)
                .map((n: Item) => (
                  <label className="check" key={n.id}>
                    <input
                      type="checkbox"
                      checked={node.depends_on.includes(n.id)}
                      onChange={() =>
                        nodeUpdate(
                          i,
                          "depends_on",
                          node.depends_on.includes(n.id)
                            ? node.depends_on.filter(
                                (id: string) => id !== n.id,
                              )
                            : [...node.depends_on, n.id],
                        )
                      }
                    />
                    {n.label}
                  </label>
                ))}
            </div>
          </div>
          <Field
            label="节点参数 JSON"
            hint="节点参数覆盖智能体默认参数，由后端白名单校验。"
          >
            <textarea
              className="mono"
              value={
                configTexts[node.id] ??
                JSON.stringify(node.config || {}, null, 2)
              }
              onChange={(e) =>
                setConfigTexts({ ...configTexts, [node.id]: e.target.value })
              }
            />
          </Field>
        </div>
      ))}
      <p className="note">
        发布时检查循环依赖与智能体可用性，并冻结各节点的智能体版本。任务自动评审由执行协调器负责。
      </p>
    </FormModal>
  );
}
