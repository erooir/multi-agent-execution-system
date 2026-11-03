import { useEffect, useRef, useState } from "react";
import { api, post, put, idPath } from "./api";
import { parseObject, splitList, taskTypes } from "./domain";
import type { Item, Mutation } from "./types";
import type { components } from "./generated/api";
import { Field, FormModal } from "./ui";
import ToolParameters from "./ToolParameters";
import WorkflowBuilder from "./WorkflowBuilder";
import { workflowIssues } from "./workflowEditorModel";
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
  offline = false,
  onClose,
  mutate,
}: Common & {
  item?: Item;
  agents: Item[];
  capabilities: Item[];
  offline?: boolean;
}) {
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
    execution_strategy: "fixed",
    tool_refs: [],
    skill_refs: [],
    members: [],
    ...item,
  });
  const [extensions, setExtensions] = useState<Item>({ tools: [], skills: [] });
  const [extensionError, setExtensionError] = useState("");
  const [toolSearch, setToolSearch] = useState("");
  const selectingModelTools =
    form.execution_strategy === "tool_agent" || form.structure === "composite";
  const dualMode = Boolean(
    form.executor_bindings?.local && form.executor_bindings?.cloud,
  );
  const toolsInScope = extensions.tools.filter(
    (tool: Item) =>
      (!item?.role_contract || item.role_contract.grants?.includes(tool.id)) &&
      (selectingModelTools ||
        tool.local_preview ||
        form.tool_refs.some((ref: Item) => ref.tool_id === tool.id)),
  );
  const matchingTools = toolsInScope.filter((t: Item) =>
    [t.id, t.label, t.name]
      .join(" ")
      .toLowerCase()
      .includes(toolSearch.trim().toLowerCase()),
  );
  const selectedNames = new Set(
    extensions.tools
      .filter((t: Item) =>
        form.tool_refs.some(
          (r: Item) => r.tool_id === t.id && r.version === t.version,
        ),
      )
      .map((t: Item) => t.name),
  );
  const missingTools = extensions.skills
    .filter((s: Item) => form.skill_refs.some((r: Item) => r.skill_id === s.id))
    .flatMap((s: Item) =>
      (s.required_tools || []).filter(
        (name: string) => !selectedNames.has(name),
      ),
    );
  useEffect(() => {
    const controller = new AbortController();
    api(`/v2/capabilities?project_id=${encodeURIComponent(projectId)}`, {
      signal: controller.signal,
    })
      .then(setExtensions)
      .catch((error) => {
        if (error.name !== "AbortError") setExtensionError(error.message);
      });
    return () => controller.abort();
  }, [projectId]);
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
        if (missingTools.length)
          throw Error(
            "文档技能包仍需授权工具：" + [...new Set(missingTools)].join("、"),
          );
        if (form.execution_strategy === "tool_agent" && !form.tool_refs.length)
          throw Error("自主工具型智能体至少需要一项版本化工具授权。");
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
              "execution_strategy",
              "tool_refs",
              "skill_refs",
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
        <Field
          label="运行方式"
          hint={
            dualMode
              ? "同一用例资产在本地模式使用固定 role_tools，在云端模式使用领域 Agno 工具循环。"
              : "工具型适用于云端文本分析和资料摘要，自主选择已授权工具。"
          }
        >
          <select
            value={form.execution_strategy}
            onChange={(event) => {
              const strategy = event.target.value;
              const capabilities = form.capabilities.filter((id: string) =>
                ["text_analysis", "source_summary"].includes(id),
              );
              setForm({
                ...form,
                execution_strategy: strategy,
                ...(strategy === "tool_agent"
                  ? {
                      model_policy: "cloud",
                      capabilities: capabilities.length
                        ? capabilities
                        : ["text_analysis"],
                      modalities: ["text", "structured"],
                      tool_ids: capabilities.length
                        ? capabilities
                        : ["text_analysis"],
                      tool_refs: extensions.tools
                        .filter((tool: Item) =>
                          extensions.skills.some((skill: Item) =>
                            skill.required_tools?.includes(tool.name),
                          ),
                        )
                        .map((item: Item) => ({
                          tool_id: item.id,
                          version: item.version,
                        })),
                      skill_refs: extensions.skills.map((item: Item) => ({
                        skill_id: item.id,
                        version: item.version,
                      })),
                    }
                  : { tool_refs: [], skill_refs: [] }),
              });
            }}
          >
            <option value="fixed">固定流程执行</option>
            <option value="tool_agent" disabled={!extensions.tools.length}>
              {dualMode
                ? "双模式用例 · 本地固定工具 / 云端领域 Agent"
                : "自主工具型智能体"}
            </option>
          </select>
        </Field>
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
      <fieldset className="capability-picker">
        <legend>授权固定工具</legend>
        <p className="note">从服务端能力目录选择，任务仅能调用已授权工具。</p>
        <div className="check-grid">
          {capabilities
            .filter((capability) => capability.tool_id)
            .map((capability) => (
              <label className="check" key={capability.id}>
                <input
                  type="checkbox"
                  checked={form.tool_ids.includes(capability.tool_id)}
                  onChange={() => toggle("tool_ids", capability.tool_id)}
                />
                <span>
                  {taskTypes[capability.id] || capability.id}
                  <small className="tool-id">{capability.tool_id}</small>
                </span>
              </label>
            ))}
          {form.tool_ids
            .filter(
              (id: string) =>
                id &&
                !capabilities.some((capability) => capability.tool_id === id),
            )
            .map((id: string) => (
              <label className="check" key={id}>
                <input
                  type="checkbox"
                  checked
                  onChange={() => toggle("tool_ids", id)}
                />
                {id} · 目录中未找到
              </label>
            ))}
        </div>
      </fieldset>
      <Field label="执行指令">
        <textarea
          rows={5}
          maxLength={8000}
          value={form.instructions}
          onChange={(e) => update("instructions", e.target.value)}
          placeholder="说明职责、输出约束与来源要求"
        />
      </Field>
      {extensionError && (
        <p className="warning" role="alert">
          工具目录读取失败：{extensionError}
        </p>
      )}
      {form.execution_strategy === "tool_agent" && offline && (
        <p className="note">
          当前部署关闭云模型。定义可保存，云端工具循环需要联网网关。
        </p>
      )}
      <fieldset className="capability-picker agent-form-tools">
        <legend>授权工具及 MCP</legend>
        <p className="note">
          {selectingModelTools
            ? "仅选中的工具进入模型或复合成员的权限范围。"
            : "固定流程执行智能体可以本地试用已授权工具。实际任务依照发布的执行流程使用工具。"}
          发布时锁定版本。已选 {form.tool_refs.length} / 10 项。
        </p>
        <Field label="筛选可授权工具">
          <input
            type="search"
            value={toolSearch}
            onChange={(e) => setToolSearch(e.target.value)}
            placeholder="搜索工具名称或标识"
          />
        </Field>
        {matchingTools.map((tool: Item) => (
          <label className="check" key={tool.id}>
            <input
              type="checkbox"
              checked={form.tool_refs.some(
                (ref: Item) => ref.tool_id === tool.id,
              )}
              disabled={
                form.tool_refs.length >= 10 &&
                !form.tool_refs.some((r: Item) => r.tool_id === tool.id)
              }
              onChange={(event) =>
                update(
                  "tool_refs",
                  event.target.checked
                    ? [
                        ...form.tool_refs,
                        { tool_id: tool.id, version: tool.version },
                      ]
                    : form.tool_refs.filter(
                        (ref: Item) => ref.tool_id !== tool.id,
                      ),
                )
              }
            />
            <span>
              {tool.label} · v{tool.version}
              <small className="tool-id">{tool.id}</small>
            </span>
          </label>
        ))}
        {!!toolsInScope.length && !matchingTools.length && (
          <p className="muted">没有匹配此关键词的可授权工具。</p>
        )}
      </fieldset>
      {(selectingModelTools || form.skill_refs.length > 0) && (
        <fieldset className="capability-picker">
          <legend>授权文档技能包</legend>
          {extensions.skills.map((skill: Item) => (
            <label className="check" key={skill.id}>
              <input
                type="checkbox"
                checked={form.skill_refs.some(
                  (ref: Item) => ref.skill_id === skill.id,
                )}
                onChange={(event) =>
                  update(
                    "skill_refs",
                    event.target.checked
                      ? [
                          ...form.skill_refs,
                          { skill_id: skill.id, version: skill.version },
                        ]
                      : form.skill_refs.filter(
                          (ref: Item) => ref.skill_id !== skill.id,
                        ),
                  )
                }
              />
              {skill.name} · {skill.version}
              <span className="muted">{skill.description}</span>
            </label>
          ))}
          <p className="note">
            文档技能包所需工具须同时授权；文档技能包只提供说明和参考资料。
          </p>
          {!!missingTools.length && (
            <p className="warning" role="status">
              所选文档技能包仍需工具：{[...new Set(missingTools)].join("、")}
            </p>
          )}
        </fieldset>
      )}
      <h3>默认执行参数</h3>
      <ToolParameters
        value={parameters}
        onChange={setParameters}
        structured={form.capabilities.includes("structured_transform")}
        text={form.capabilities.includes("source_summary")}
      />
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
                  <small>
                    v
                    {form.members.find(
                      (member: Item) => member.agent_id === a.id,
                    )?.version || a.current_version}
                    {form.members.some(
                      (member: Item) =>
                        member.agent_id === a.id &&
                        member.version !== a.current_version,
                    ) && `（目录当前 v${a.current_version}）`}
                  </small>
                </label>
              ))}
          </div>
          {form.members
            .filter(
              (member: Item) =>
                !agents.some(
                  (agent) =>
                    agent.id === member.agent_id &&
                    agent.status === "published",
                ),
            )
            .map((member: Item) => (
              <div className="warning row-between" key={member.agent_id}>
                <span>
                  {agents.find((agent) => agent.id === member.agent_id)?.name ||
                    member.agent_id}{" "}
                  · v{member.version} 当前不可用
                </span>
                <button
                  type="button"
                  onClick={() =>
                    update(
                      "members",
                      form.members.filter(
                        (item: Item) => item.agent_id !== member.agent_id,
                      ),
                    )
                  }
                >
                  移除成员
                </button>
              </div>
            ))}
          <small>
            保留已绑定版本；取消后重新勾选可绑定当前发布版。发布时检查循环、深度与成员权限。
          </small>
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
  initial,
}: Common & {
  assets: Item[];
  workflows: Item[];
  schedule?: boolean;
  afterCreate?: (item: Item) => void;
  initial?: Item;
}) {
  const [form, setForm] = useState<Item>({
    name: "",
    goal: "",
    task_type: "structured_transform",
    input_refs: [],
    workflow_id: "",
    mode: "local",
    service_class: "batch",
    deadline_seconds: 300,
    max_cost_cny: 0,
    max_revisions: 1,
    allow_partial: false,
    use_project_knowledge: false,
    ...initial,
    idempotency_key: crypto.randomUUID(),
  });
  const [catalog, setCatalog] = useState<Item[]>([]);
  const [catalogError, setCatalogError] = useState("");
  const [profiles, setProfiles] = useState<Item[]>([]);
  useEffect(() => {
    if (form.task_type !== "case_analysis") return;
    const c = new AbortController();
    api<Item[]>(
      "/v2/casework/catalog?project_id=" + encodeURIComponent(projectId),
      { signal: c.signal },
    )
      .then(setProfiles)
      .catch((e) => {
        if (e.name !== "AbortError") setCatalogError(e.message);
      });
    return () => c.abort();
  }, [form.task_type, projectId]);
  const [preview, setPreview] = useState<
    components["schemas"]["TaskPreflight"] | null
  >(null);
  const [previewError, setPreviewError] = useState("");
  const [previewBusy, setPreviewBusy] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    api(`/v2/capabilities?project_id=${encodeURIComponent(projectId)}`, {
      signal: controller.signal,
    })
      .then((data) => {
        setCatalog(data.capabilities);
        setCatalogError("");
      })
      .catch((error) => {
        if (error.name !== "AbortError") setCatalogError(error.message);
      });
    return () => controller.abort();
  }, [projectId]);
  const capability = catalog.find((c) => c.id === form.task_type);
  const [quality, setQuality] = useState({
      min_records: 1,
      min_length: 1,
      ...initial?.quality,
      required_fields: (initial?.quality?.required_fields || []).join(", "),
    }),
    [interval, setInterval] = useState(86400);
  const previewInput = useRef("");
  previewInput.current = JSON.stringify({ form, quality });
  const update = (key: string, value: any) =>
    setForm({ ...form, [key]: value });
  useEffect(() => {
    setPreview(null);
    setPreviewError("");
  }, [form, quality]);
  function taskRequest(): components["schemas"]["TaskInput"] {
    return {
      name: form.name,
      goal: form.goal,
      task_type: form.task_type,
      case_id: form.task_type === "case_analysis" ? form.case_id : null,
      case_scope:
        form.task_type === "case_analysis"
          ? form.case_scope || "reference"
          : "reference",
      input_refs: form.input_refs,
      mode: form.mode,
      service_class: form.service_class,
      idempotency_key: form.idempotency_key,
      deadline_seconds: form.deadline_seconds,
      max_cost_cny: form.max_cost_cny,
      max_revisions: form.max_revisions,
      allow_partial: form.allow_partial,
      use_project_knowledge: form.use_project_knowledge,
      project_id: projectId,
      workflow_id: form.workflow_id || null,
      quality: {
        ...quality,
        required_fields: splitList(quality.required_fields),
      },
    };
  }
  return (
    <FormModal
      wide
      title={
        form.task_type === "case_analysis"
          ? schedule
            ? "创建用例计划任务"
            : "提交用例任务"
          : schedule
            ? "创建通用计划任务"
            : "提交通用任务"
      }
      onClose={onClose}
      submit={schedule ? "创建计划" : "提交并自动执行"}
      onSubmit={async () => {
        const task = taskRequest();
        const result = await mutate(
          async () => {
            await post("/v2/tasks/preflight", task);
            return post(
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
            );
          },
          schedule ? "计划任务已创建" : "任务已受理",
        );
        afterCreate?.(result);
      }}
    >
      {catalogError && (
        <p className="warning" role="alert">
          能力目录读取失败：{catalogError}
        </p>
      )}
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
            onChange={(e) => {
              const selected = catalog.find((c) => c.id === e.target.value);
              const mode = selected?.modes.includes(form.mode)
                ? form.mode
                : selected?.modes[0] || "local";
              setForm({
                ...form,
                task_type: e.target.value,
                workflow_id: "",
                mode,
                use_project_knowledge:
                  mode === "cloud" && form.use_project_knowledge,
                max_cost_cny: mode === "cloud" ? form.max_cost_cny || 1 : 0,
              });
            }}
          >
            {Object.entries(taskTypes).map(([id, label]) => (
              <option
                value={id}
                key={id}
                disabled={!catalog.find((c) => c.id === id)?.available}
              >
                {label}
                {catalog.find((c) => c.id === id)?.available
                  ? ""
                  : "（暂未接入）"}
              </option>
            ))}
          </select>
        </Field>
      </div>
      <p className="note">
        {capability?.description || "正在读取服务端能力目录…"}
      </p>
      {form.task_type === "case_analysis" && (
        <Field label="分析用例" hint="使用本项目已发布的用例岗位与流程。">
          <select
            required
            value={form.case_id || ""}
            onChange={(e) =>
              setForm({ ...form, case_id: e.target.value, workflow_id: "" })
            }
          >
            <option value="">选择用例</option>
            {profiles.map((p) => (
              <option key={p.id} value={p.id} disabled={!p.installed}>
                {p.id} · {p.title}
                {!p.installed ? "（未发布到本项目）" : ""}
              </option>
            ))}
          </select>
        </Field>
      )}
      {form.task_type === "case_analysis" && (
        <Field label="验收范围">
          <select
            value={form.case_scope || "reference"}
            onChange={(e) => update("case_scope", e.target.value)}
          >
            <option value="reference">参考用例的指定批次</option>
            <option value="sample">缩小样本验证（不代表完整用例验收）</option>
          </select>
        </Field>
      )}
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
            .filter(
              (a) =>
                a.status === "active" &&
                new Date(a.expires_at).getTime() > Date.now(),
            )
            .map((a) => (
              <label className="check" key={a.id}>
                <input
                  type="checkbox"
                  disabled={
                    !form.input_refs.includes(a.id) &&
                    (!capability?.modalities.includes(a.modality) ||
                      (form.mode === "cloud" && a.classification !== "public"))
                  }
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
                use_project_knowledge:
                  e.target.value === "cloud" && form.use_project_knowledge,
                max_cost_cny: e.target.value === "cloud" ? 1 : 0,
              })
            }
          >
            <option
              value="local"
              disabled={!capability?.modes.includes("local")}
            >
              本地执行 · 不调用云模型
            </option>
            <option
              value="cloud"
              disabled={!capability?.modes.includes("cloud")}
            >
              云端模型 · 计入累计预算
            </option>
            <option
              value="rehearsal"
              disabled={!capability?.modes.includes("rehearsal")}
            >
              配置演练 · 非推理结果
            </option>
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
                  w.status === "published" &&
                  w.task_type === form.task_type &&
                  (form.task_type !== "case_analysis" ||
                    w.id ===
                      profiles.find((p) => p.id === form.case_id)?.workflow_id),
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
          {form.task_type === "case_analysis"
            ? "用例的资料解析、专业分析、独立核查三个节点均调用真实模型并分别计费；仅可外发标记 public 的公开资料，离线部署不可调用。执行、自动评审与修订均经过统一网关并计入历史累计 300 元上限。"
            : "只允许外发已标记公开的数据。执行、自动评审与修订均经过统一网关并计入历史累计 300 元上限。"}
        </p>
      )}
      {form.mode === "cloud" && (
        <label className="check">
          <input
            type="checkbox"
            checked={form.use_project_knowledge}
            onChange={(event) =>
              update("use_project_knowledge", event.target.checked)
            }
          />
          允许工具型智能体 检索本项目公开知识
        </label>
      )}
      {form.mode === "rehearsal" && (
        <p className="warning">
          演练只验证配置和调用路径，产物明确标记为演练，不代表离线模型推理或任务质量。
        </p>
      )}
      <h3>资源与质量约束</h3>
      <Field
        label="任务时效类型"
        hint="交互任务优先；批量任务等待 60 秒后提升顺序。不会中断正在执行的任务。"
      >
        <select
          value={form.service_class}
          onChange={(e) => update("service_class", e.target.value)}
        >
          <option value="batch">批量处理 · 允许排队</option>
          <option value="interactive">交互请求 · 优先调度</option>
        </select>
      </Field>
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
        <Field
          label="最多修订 / 恢复轮次"
          hint="质量修订与可安全恢复的节点错误共用此额度；权限、预算和不确定费用不会自动重试。"
        >
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
      <section className="preflight-summary">
        <button
          type="button"
          disabled={previewBusy}
          onClick={async () => {
            const expectedInput = previewInput.current;
            setPreviewBusy(true);
            setPreviewError("");
            setPreview(null);
            try {
              const result = await post("/v2/tasks/preflight", taskRequest());
              if (previewInput.current === expectedInput) setPreview(result);
            } catch (error) {
              if (previewInput.current === expectedInput)
                setPreviewError((error as Error).message);
            } finally {
              setPreviewBusy(false);
            }
          }}
        >
          {previewBusy ? "正在检查…" : "检查匹配与授权"}
        </button>
        <p className="muted small">
          查看将使用的智能体版本，不创建任务、不调用模型。提交时会再次验证。
        </p>
        {previewError && (
          <p className="warning" role="alert">
            {previewError}
          </p>
        )}
        {preview && (
          <div role="status">
            <strong>预检查通过</strong>
            <div className="tags">
              {preview.selected_agents?.map((agent) => (
                <span key={String(agent.agent_id)}>
                  {String(agent.name)} · v{String(agent.version)}
                </span>
              ))}
            </div>
            {preview.existing_task_id && <p>相同请求已存在，将返回原任务。</p>}
          </div>
        )}
      </section>
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
  const [csvEncoding, setCsvEncoding] = useState("utf-8");
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
          if (/\.csv$/i.test(file.name))
            data.append("csv_encoding", csvEncoding);
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
          hint="文档、表格、图片上限10 MB；音视频上限128 MB。支持 PDF、DOCX、XLSX、GeoJSON 和字幕。"
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
                      : /\.(json|csv|jsonl|geojson|xlsx)$/i.test(selected.name)
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
      {upload && file && /\.csv$/i.test(file.name) && (
        <Field label="CSV文件编码">
          <select
            value={csvEncoding}
            onChange={(e) => setCsvEncoding(e.target.value)}
          >
            <option value="utf-8">UTF-8</option>
            <option value="windows-1252">Windows-1252</option>
          </select>
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
  return (
    <FormModal
      wide
      title={item ? "编辑流程定义" : "创建复用流程"}
      onClose={onClose}
      submit="保存流程"
      onSubmit={() => {
        const issues = workflowIssues(form.nodes, agents, form.task_type);
        if (issues.length) throw new Error(issues.join("；"));
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
      <WorkflowBuilder
        nodes={form.nodes}
        agents={agents}
        taskType={form.task_type}
        onChange={(nodes) => update("nodes", nodes)}
        configTexts={configTexts}
        onConfigChange={(id, value) =>
          setConfigTexts((current) => ({ ...current, [id]: value }))
        }
      />
      <p className="note">
        发布时检查循环依赖与智能体可用性，并冻结各节点的智能体版本。任务自动评审由执行协调器负责。
      </p>
    </FormModal>
  );
}
