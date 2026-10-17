import { useEffect, useState } from "react";
import {
  Database,
  FlaskConical,
  Plus,
  Search,
  Server,
  ShieldCheck,
} from "lucide-react";
import { api, idPath, post, put } from "./api";
import { money, parseObject, splitList, taskTypes, time } from "./domain";
import type { Bootstrap, Item, Mutation, Notify, User } from "./types";
import {
  Badge,
  Confirm,
  Empty,
  Field,
  FormModal,
  JsonView,
  Modal,
  Panel,
  SectionTitle,
  Tabs,
} from "./ui";
type Common = {
  projectId: string;
  canEdit: boolean;
  mutate: Mutation;
  notify: Notify;
};
export function KnowledgePage({
  projectId,
  connectors,
  memories,
  assets,
  canEdit,
  mutate,
  notify,
}: Common & { connectors: Item[]; memories: Item[]; assets: Item[] }) {
  const [tab, setTab] = useState("search"),
    [query, setQuery] = useState(""),
    [results, setResults] = useState<any>(null),
    [busy, setBusy] = useState(false),
    [create, setCreate] = useState<"connector" | "memory" | null>(null);
  const [connector, setConnector] = useState({
      name: "",
      kind: "local_assets",
      base_url: "",
      description: "",
    }),
    [memory, setMemory] = useState({
      content: "",
      source_refs: [] as string[],
      retention_days: 7,
    });
  async function search() {
    if (!query.trim()) return;
    setBusy(true);
    try {
      setResults(
        await post("/v2/knowledge/search", {
          project_id: projectId,
          query,
          limit: 20,
        }),
      );
    } catch (e) {
      notify((e as Error).message, true);
    } finally {
      setBusy(false);
    }
  }
  const hits = Array.isArray(results)
    ? results
    : results?.results || results?.items || [];
  return (
    <>
      <SectionTitle
        eyebrow="KNOWLEDGE & MEMORY"
        title="知识与记忆"
        description="通过授权连接器检索来源，保存有依据且有保留期限的核心信息。"
        actions={
          canEdit && (
            <>
              <button onClick={() => setCreate("memory")}>保存核心记忆</button>
              <button
                className="primary"
                onClick={() => setCreate("connector")}
              >
                <Plus size={18} />
                登记连接器
              </button>
            </>
          )
        }
      />
      <Tabs
        value={tab}
        onChange={setTab}
        items={[
          { id: "search", label: "授权检索" },
          { id: "connectors", label: `连接器 · ${connectors.length}` },
          { id: "memories", label: `核心记忆 · ${memories.length}` },
        ]}
      />
      {tab === "search" && (
        <>
          <form
            className="search-bar"
            onSubmit={(e) => {
              e.preventDefault();
              void search();
            }}
          >
            <Search size={20} />
            <input
              required
              aria-label="知识检索内容"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="在当前项目的授权数据与记忆中检索"
            />
            <button className="primary" disabled={busy}>
              {busy ? "正在检索…" : "检索来源"}
            </button>
          </form>
          <Panel title="检索结果">
            {results === null ? (
              <Empty
                title="从有依据的来源开始"
                description="检索先应用项目权限、有效期与撤回策略。连接器未实际对接时会明确报告。"
              />
            ) : hits.length ? (
              hits.map((hit: Item, i: number) => (
                <article className="record" key={hit.id || i}>
                  <div className="row-between">
                    <h3>
                      {hit.name || hit.source_id || hit.type || "来源片段"}
                    </h3>
                    <span className="mono small">{hit.id}</span>
                  </div>
                  <p className="prose">
                    {typeof (hit.snippet || hit.content || hit.text) ===
                    "string"
                      ? hit.snippet || hit.content || hit.text
                      : JSON.stringify(hit.snippet || hit.content || hit.text)}
                  </p>
                  <JsonView value={hit} label="来源、版本与权限元数据" />
                </article>
              ))
            ) : (
              <Empty
                title="没有匹配的有效来源"
                description="请检查查询词、数据范围与来源有效性。"
              />
            )}
            {results && !Array.isArray(results) && (
              <JsonView value={results} label="完整检索响应" />
            )}
          </Panel>
        </>
      )}
      {tab === "connectors" && (
        <div className="asset-grid">
          {connectors.length ? (
            connectors.map((c) => (
              <Panel key={c.id}>
                <div className="row-between">
                  <span className="asset-icon">
                    <Database size={24} />
                  </span>
                  <Badge status={c.status} />
                </div>
                <h3>{c.name}</h3>
                <p>{c.description}</p>
                <p className="muted">
                  {c.kind === "local_assets"
                    ? "本地授权数据目录"
                    : "上游知识接口"}
                </p>
                {c.base_url && <code className="wrap-text">{c.base_url}</code>}
                <JsonView value={c} />
              </Panel>
            ))
          ) : (
            <Empty
              title="尚未登记连接器"
              description="可登记本地数据检索连接器或待对接的上游知识接口。"
            />
          )}
        </div>
      )}
      {tab === "memories" && (
        <Panel>
          {memories.length ? (
            memories.map((m) => (
              <article className="record" key={m.id}>
                <div className="row-between">
                  <Badge status={m.status} />
                  <small>有效期至 {time(m.expires_at)}</small>
                </div>
                <p className="prose">{m.content}</p>
                <JsonView value={m} label="来源引用与保留策略" />
              </article>
            ))
          ) : (
            <Empty
              title="尚无核心记忆"
              description="只保存获准保留、能够回溯到有效来源的信息。"
            />
          )}
        </Panel>
      )}
      {create === "connector" && (
        <FormModal
          title="登记知识连接器"
          onClose={() => setCreate(null)}
          onSubmit={() =>
            mutate(
              () =>
                post("/v2/connectors", { ...connector, project_id: projectId }),
              "连接器已登记",
            )
          }
        >
          <Field label="名称">
            <input
              required
              value={connector.name}
              onChange={(e) =>
                setConnector({ ...connector, name: e.target.value })
              }
            />
          </Field>
          <Field label="连接器类型">
            <select
              value={connector.kind}
              onChange={(e) =>
                setConnector({ ...connector, kind: e.target.value })
              }
            >
              <option value="local_assets">本地授权数据</option>
              <option value="http_knowledge">上游 HTTP 知识服务</option>
            </select>
          </Field>
          {connector.kind === "http_knowledge" && (
            <>
              <Field label="内网服务地址">
                <input
                  type="url"
                  value={connector.base_url}
                  onChange={(e) =>
                    setConnector({ ...connector, base_url: e.target.value })
                  }
                  placeholder="http://internal-service/knowledge"
                />
              </Field>
              <p className="note">
                登记接口不代表完成对接；实际状态由服务端适配器验证。凭据只在服务端配置。
              </p>
            </>
          )}
          <Field label="说明">
            <textarea
              value={connector.description}
              onChange={(e) =>
                setConnector({ ...connector, description: e.target.value })
              }
            />
          </Field>
        </FormModal>
      )}
      {create === "memory" && (
        <FormModal
          title="保存有来源的核心记忆"
          onClose={() => setCreate(null)}
          onSubmit={() =>
            mutate(
              () => post("/v2/memories", { ...memory, project_id: projectId }),
              "核心记忆已保存",
            )
          }
        >
          <Field label="事实 / 经验内容">
            <textarea
              required
              rows={5}
              value={memory.content}
              onChange={(e) =>
                setMemory({ ...memory, content: e.target.value })
              }
            />
          </Field>
          <div className="field">
            <span>来源引用</span>
            <div className="selection-list">
              {assets
                .filter((a) => a.status === "active")
                .map((a) => (
                  <label className="check" key={a.id}>
                    <input
                      type="checkbox"
                      checked={memory.source_refs.includes(a.id)}
                      onChange={() =>
                        setMemory({
                          ...memory,
                          source_refs: memory.source_refs.includes(a.id)
                            ? memory.source_refs.filter((id) => id !== a.id)
                            : [...memory.source_refs, a.id],
                        })
                      }
                    />
                    {a.name}
                  </label>
                ))}
            </div>
          </div>
          <Field label="保留天数">
            <input
              type="number"
              min={1}
              max={365}
              value={memory.retention_days}
              onChange={(e) =>
                setMemory({ ...memory, retention_days: Number(e.target.value) })
              }
            />
          </Field>
          <p className="note">
            撤回来源会使派生记忆失效；模型猜测不能作为权威事实保存。
          </p>
        </FormModal>
      )}
    </>
  );
}
export function ImprovementsPage({
  projectId,
  improvements,
  evaluations,
  agents,
  tasks,
  canEdit,
  isAdmin,
  mutate,
  notify,
}: Common & {
  improvements: Item[];
  evaluations: Item[];
  agents: Item[];
  tasks: Item[];
  isAdmin: boolean;
}) {
  const [create, setCreate] = useState(false),
    [form, setForm] = useState({
      name: "",
      agent_id: "",
      instructions: "",
      tool_ids: "",
      sample_task_ids: [] as string[],
    }),
    [selected, setSelected] = useState<Item | null>(null),
    [action, setAction] = useState<{ item: Item; type: string } | null>(null);
  const [parameters, setParameters] = useState("{}");
  const fresh = improvements.find((i) => i.id === selected?.id) || selected;
  const labels: Record<string, string> = {
    evaluate: "运行冻结评测",
    promote: "晋升候选",
    rollback: "回滚发布",
  };
  return (
    <>
      <SectionTitle
        eyebrow="CONTINUOUS IMPROVEMENT"
        title="评测与改进"
        description="把跨任务改进与任务内修订分开。用冻结样本、基线对比和回归门槛管理版本。"
        actions={
          canEdit && (
            <button className="primary" onClick={() => setCreate(true)}>
              <Plus size={18} />
              创建改进候选
            </button>
          )
        }
      />
      <div className="notice">
        <FlaskConical size={20} />
        <div>
          <strong>每一次晋升都需要可追溯的评测证据</strong>
          <p>
            候选只能调整授权配置。评测不足、硬性失败或退化都会阻止晋升；运行任务保持原快照。
          </p>
        </div>
      </div>
      <Panel title="改进候选">
        {improvements.length ? (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>候选</th>
                  <th>智能体 / 基线</th>
                  <th>状态</th>
                  <th>评测证据</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {improvements.map((i) => (
                  <tr key={i.id}>
                    <td>
                      <button
                        className="text-button"
                        onClick={() => setSelected(i)}
                      >
                        {i.name}
                      </button>
                      <small>{time(i.created_at)}</small>
                    </td>
                    <td>
                      {agents.find((a) => a.id === i.agent_id)?.name ||
                        i.agent_id}
                      <small>基线 v{i.baseline_version || "—"}</small>
                    </td>
                    <td>
                      <Badge status={i.status} />
                    </td>
                    <td>
                      {i.evaluation_id ? (
                        <button
                          className="text-button"
                          onClick={() => setSelected(i)}
                        >
                          查看评测记录
                        </button>
                      ) : (
                        <span className="muted">尚未评测</span>
                      )}
                    </td>
                    <td>
                      {canEdit && (
                        <div className="actions wrap">
                          {[
                            "candidate",
                            "draft",
                            "rejected",
                            "evaluated",
                          ].includes(i.status) && (
                            <button
                              onClick={() =>
                                setAction({ item: i, type: "evaluate" })
                              }
                            >
                              评测
                            </button>
                          )}
                          {isAdmin && i.status === "evaluated" && (
                            <button
                              onClick={() =>
                                setAction({ item: i, type: "promote" })
                              }
                            >
                              晋升
                            </button>
                          )}
                          {isAdmin && i.status === "promoted" && (
                            <button
                              onClick={() =>
                                setAction({ item: i, type: "rollback" })
                              }
                            >
                              回滚
                            </button>
                          )}
                        </div>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty
            title="还没有改进候选"
            description="先积累真实任务与质量结果，再针对明确问题提出配置候选。"
            action={
              canEdit && (
                <button onClick={() => setCreate(true)}>创建候选</button>
              )
            }
          />
        )}
      </Panel>
      <Panel title={`评测记录 · ${evaluations.length}`}>
        {evaluations.length ? (
          evaluations.map((e) => (
            <JsonView
              key={e.id}
              value={e}
              label={`${e.name || e.id} · ${time(e.created_at)}`}
            />
          ))
        ) : (
          <p className="muted">尚无已执行的评测，不展示预置分数或提升率。</p>
        )}
      </Panel>
      {create && (
        <FormModal
          wide
          title="创建改进候选"
          submit="保存候选"
          onClose={() => setCreate(false)}
          onSubmit={() =>
            mutate(
              () =>
                post("/v2/improvements", {
                  ...form,
                  project_id: projectId,
                  tool_ids: splitList(form.tool_ids),
                  parameters: parseObject(parameters, "候选工具参数"),
                }),
              "改进候选已保存",
            )
          }
        >
          <div className="form-grid">
            <Field label="候选名称">
              <input
                required
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
              />
            </Field>
            <Field label="基线智能体">
              <select
                required
                value={form.agent_id}
                onChange={(e) => {
                  const agent = agents.find((a) => a.id === e.target.value);
                  setParameters(
                    JSON.stringify(agent?.parameters || {}, null, 2),
                  );
                  setForm({
                    ...form,
                    agent_id: e.target.value,
                    instructions: agent?.instructions || "",
                    tool_ids: (agent?.tool_ids || []).join(", "),
                  });
                }}
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
          <Field label="候选执行指令">
            <textarea
              rows={6}
              value={form.instructions}
              maxLength={8000}
              onChange={(e) =>
                setForm({ ...form, instructions: e.target.value })
              }
            />
          </Field>
          <Field label="候选工具 ID（逗号分隔）">
            <input
              value={form.tool_ids}
              onChange={(e) => setForm({ ...form, tool_ids: e.target.value })}
            />
          </Field>
          <Field
            label="候选工具参数 JSON"
            hint="在允许范围内设置 max_chars、sort_by 等参数；用于与固定基线真实对比。"
          >
            <textarea
              className="mono"
              rows={4}
              value={parameters}
              onChange={(e) => setParameters(e.target.value)}
            />
          </Field>
          <div className="field">
            <span>冻结评测样本 · 至少两个真实任务</span>
            <div className="selection-list">
              {tasks
                .filter(
                  (t) =>
                    ["succeeded", "partial", "failed"].includes(t.status) &&
                    t.request?.mode !== "rehearsal",
                )
                .map((t) => (
                  <label className="check" key={t.id}>
                    <input
                      type="checkbox"
                      checked={form.sample_task_ids.includes(t.id)}
                      onChange={() =>
                        setForm({
                          ...form,
                          sample_task_ids: form.sample_task_ids.includes(t.id)
                            ? form.sample_task_ids.filter((id) => id !== t.id)
                            : [...form.sample_task_ids, t.id],
                        })
                      }
                    />
                    <span>{t.name || t.request?.name || t.id}</span>
                    <Badge status={t.status} />
                  </label>
                ))}
            </div>
          </div>
          <p className="note">
            服务端冻结样本与版本，并在相同约束下执行基线和候选。评测使用真实输出，不以候选自评分证明改进。
          </p>
        </FormModal>
      )}
      {fresh && (
        <Modal wide title={fresh.name} onClose={() => setSelected(null)}>
          <div className="modal-body">
            <Badge status={fresh.status} />
            <JsonView value={fresh} label="候选配置与版本引用" />
            <h3>关联评测证据</h3>
            {evaluations
              .filter(
                (e) =>
                  e.id === fresh.evaluation_id || e.improvement_id === fresh.id,
              )
              .map((e) => (
                <pre key={e.id}>{JSON.stringify(e, null, 2)}</pre>
              ))}
          </div>
        </Modal>
      )}
      {action && (
        <Confirm
          title={`${labels[action.type]}「${action.item.name}」`}
          description={
            action.type === "evaluate"
              ? "执行冻结样本的基线和候选对比，保留逐样本结果、耗时和资源证据。涉及模型时由统一网关计费。"
              : action.type === "promote"
                ? "服务端检查评测门槛与回归结果，通过后发布新版本供后续任务使用。"
                : "将后续任务的发布指向回滚到已记录的稳定版本；已经运行的任务保持快照。"
          }
          onClose={() => setAction(null)}
          onConfirm={() =>
            mutate(
              () =>
                post(
                  idPath("improvements", action.item.id) + "/" + action.type,
                ),
              `${labels[action.type]}已执行`,
            )
          }
        />
      )}
    </>
  );
}
export function OperationsPage({
  data,
  user,
  projectId,
  mutate,
  notify,
  onProject,
}: {
  data: Bootstrap;
  user: User;
  projectId: string;
  mutate: Mutation;
  notify: Notify;
  onProject: (id: string) => void;
}) {
  const [tab, setTab] = useState("services"),
    [createProject, setCreateProject] = useState(false),
    [projectForm, setProjectForm] = useState({ name: "", description: "" }),
    [members, setMembers] = useState<Item | null>(null),
    [memberText, setMemberText] = useState(""),
    [audits, setAudits] = useState<Item[]>([]);
  useEffect(() => {
    if (tab !== "audit") return;
    let active = true;
    api("/v2/audits")
      .then((x) => active && setAudits(x))
      .catch((e) => notify(e.message, true));
    return () => {
      active = false;
    };
  }, [tab]);
  const budget = data.budget || {},
    system = data.system || {},
    capabilities = Array.isArray(data.capabilities) ? data.capabilities : [];
  return (
    <>
      <SectionTitle
        eyebrow="PLATFORM OPERATIONS"
        title="平台运维"
        description="核对实际预算、工作进程心跳、能力目录、项目权限和操作审计。"
      />
      <Tabs
        value={tab}
        onChange={setTab}
        items={[
          { id: "services", label: "服务与能力" },
          { id: "budget", label: "累计预算" },
          { id: "projects", label: "项目与成员" },
          ...(user.role === "admin"
            ? [{ id: "audit", label: "操作审计" }]
            : []),
        ]}
      />
      {tab === "services" && (
        <>
          <div className="metric-grid">
            <div className="metric">
              <Server size={20} />
              <span>执行运行时</span>
              <strong>{system.runtime || "—"}</strong>
              <small>
                {system.agno_version
                  ? `Agno ${system.agno_version}`
                  : "版本未返回"}
              </small>
            </div>
            <div className="metric">
              <Database size={20} />
              <span>业务数据库</span>
              <strong>{system.database || "—"}</strong>
              <small>持久任务、版本与事件</small>
            </div>
            <div className="metric">
              <ShieldCheck size={20} />
              <span>独立 Worker</span>
              <strong>
                {system.worker_online === true
                  ? "有在线进程"
                  : system.worker_online === false
                    ? "无在线进程"
                    : "状态未知"}
              </strong>
              <small>以服务端心跳判断</small>
            </div>
          </div>
          <Panel title="工作进程">
            {data.workers?.length ? (
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>进程标识</th>
                      <th>状态</th>
                      <th>最近心跳</th>
                      <th>详情</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.workers.map((w) => (
                      <tr key={w.id}>
                        <td className="mono">{w.id}</td>
                        <td>
                          <Badge status={w.health || "unknown"} />
                        </td>
                        <td>
                          {time(
                            w.heartbeat_at || w.last_heartbeat || w.updated_at,
                          )}
                        </td>
                        <td>
                          <JsonView value={w} />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <Empty
                title="尚无工作进程记录"
                description="任务可以持久受理，但需要独立 Worker 执行。请按部署手册启动 Worker 并检查心跳。"
              />
            )}
          </Panel>
          <Panel title="实际能力目录">
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>能力</th>
                    <th>验证状态</th>
                    <th>支持模式</th>
                    <th>工具 / 说明</th>
                  </tr>
                </thead>
                <tbody>
                  {capabilities.map((c) => (
                    <tr key={c.id}>
                      <td>
                        {c.name || taskTypes[c.id] || c.id}
                        <small>{c.modalities?.join(" / ")}</small>
                      </td>
                      <td>
                        <Badge status={c.available ? "ready" : "unsupported"}>
                          {c.available ? "已提供" : "尚未支持"}
                        </Badge>
                      </td>
                      <td>{c.modes?.join(" / ") || "—"}</td>
                      <td>
                        <code>{c.tool_id}</code>
                        <small>{c.description}</small>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Panel>
          <Panel title="部署配置">
            <div className="detail-grid">
              <span>平台版本</span>
              <strong>{system.version || "—"}</strong>
              <span>离线模式</span>
              <strong>
                {system.offline === true
                  ? "已启用"
                  : system.offline === false
                    ? "未启用"
                    : "未知"}
              </strong>
              <span>模型标识</span>
              <strong>{system.model || "未配置"}</strong>
              <span>网关密钥</span>
              <strong>
                {system.key_configured ? "服务端已配置" : "未配置"}
              </strong>
            </div>
            <JsonView value={system} />
          </Panel>
        </>
      )}
      {tab === "budget" && (
        <>
          {budget.available === false && (
            <p className="error-line" role="alert">
              历史预算账本不可用，付费调用已关闭。请恢复原有账本；不能通过新建或重置账本绕过累计上限。
            </p>
          )}
          <div className="notice">
            <ShieldCheck size={22} />
            <div>
              <strong>
                整个项目历史累计上限：人民币{" "}
                {typeof budget.limit_cny === "number" ? budget.limit_cny : "—"}{" "}
                元
              </strong>
              <p>
                业务存储重建不会重置预算。所有付费调用在统一网关预留最坏费用，未确认调用继续保留预留。
              </p>
            </div>
          </div>
          <div className="metric-grid four">
            <div className="metric">
              <span>历史已计费用</span>
              <strong>¥ {money(budget.spent_cny)}</strong>
            </div>
            <div className="metric">
              <span>未结算预留</span>
              <strong>¥ {money(budget.reserved_cny)}</strong>
            </div>
            <div className="metric">
              <span>可用预算</span>
              <strong>¥ {money(budget.remaining_cny)}</strong>
            </div>
            <div className="metric">
              <span>请求 / 不确定调用</span>
              <strong>
                {budget.request_count ?? "—"} / {budget.uncertain_count ?? "—"}
              </strong>
            </div>
          </div>
          <Panel title="账本与价格依据">
            <p>{budget.pricing_note || "服务端未返回价格说明"}</p>
            <JsonView value={budget} label="原始账本汇总" />
          </Panel>
        </>
      )}
      {tab === "projects" && (
        <Panel
          title="可访问项目"
          actions={
            user.role !== "reviewer" && (
              <button onClick={() => setCreateProject(true)}>
                <Plus size={16} />
                创建项目
              </button>
            )
          }
        >
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>项目</th>
                  <th>描述</th>
                  <th>成员</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {data.projects.map((p) => (
                  <tr key={p.id}>
                    <td>
                      {p.name}
                      <small className="mono">{p.id}</small>
                    </td>
                    <td>{p.description || "—"}</td>
                    <td>
                      {Array.isArray(p.members) ? p.members.join("、") : "—"}
                    </td>
                    <td>
                      <div className="actions">
                        <button
                          disabled={p.id === projectId}
                          onClick={() => onProject(p.id)}
                        >
                          {p.id === projectId ? "当前项目" : "切换"}
                        </button>
                        {user.role === "admin" && (
                          <button
                            onClick={() => {
                              setMembers(p);
                              setMemberText((p.members || []).join(", "));
                            }}
                          >
                            管理成员
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
      )}
      {tab === "audit" && (
        <Panel title="服务端操作审计">
          {audits.length ? (
            audits.map((a) => (
              <article className="record" key={a.id}>
                <div className="row-between">
                  <strong>{a.action || a.type}</strong>
                  <small>{time(a.created_at || a.time)}</small>
                </div>
                <p>
                  {a.user || a.actor || a.actor_id} · {a.entity || a.entity_id}
                </p>
                <JsonView value={a} />
              </article>
            ))
          ) : (
            <Empty title="暂无可见审计记录" />
          )}
        </Panel>
      )}
      {createProject && (
        <FormModal
          title="创建项目"
          onClose={() => setCreateProject(false)}
          onSubmit={async () => {
            const created = await mutate(
              () => post("/v2/projects", projectForm),
              "项目已创建",
            );
            onProject(created.id);
          }}
        >
          <Field label="项目名称">
            <input
              required
              maxLength={100}
              value={projectForm.name}
              onChange={(e) =>
                setProjectForm({ ...projectForm, name: e.target.value })
              }
            />
          </Field>
          <Field label="说明">
            <textarea
              value={projectForm.description}
              onChange={(e) =>
                setProjectForm({ ...projectForm, description: e.target.value })
              }
            />
          </Field>
        </FormModal>
      )}
      {members && (
        <FormModal
          title={`管理「${members.name}」成员`}
          onClose={() => setMembers(null)}
          onSubmit={() =>
            mutate(
              () =>
                put(idPath("projects", members.id) + "/members", {
                  members: splitList(memberText),
                }),
              "项目成员已更新",
            )
          }
        >
          <Field
            label="成员用户 ID"
            hint="逗号或换行分隔。只有已登记用户可以加入项目。"
          >
            <textarea
              value={memberText}
              onChange={(e) => setMemberText(e.target.value)}
            />
          </Field>
          {data.users && <JsonView value={data.users} label="已登记用户目录" />}
        </FormModal>
      )}
    </>
  );
}
