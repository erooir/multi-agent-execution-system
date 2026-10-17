import { useEffect, useMemo, useState } from "react";
import {
  ArrowLeft,
  CalendarClock,
  Download,
  ExternalLink,
  Plus,
  RefreshCw,
  Search,
  Square,
} from "lucide-react";
import { api, idPath, post } from "./api";
import { money, reasonNames, taskTypes, terminal, time } from "./domain";
import type { Item, Mutation, Notify } from "./types";
import type { components } from "./generated/api";
import {
  Badge,
  Confirm,
  Empty,
  ErrorLine,
  JsonView,
  Modal,
  Panel,
  SectionTitle,
  Tabs,
} from "./ui";
import { TaskForm } from "./Forms";
import Graph from "./Graph";
import { modeName } from "./domain";
type Props = {
  projectId: string;
  tasks: Item[];
  assets: Item[];
  agents: Item[];
  workflows: Item[];
  schedules: Item[];
  canEdit: boolean;
  mutate: Mutation;
  notify: Notify;
  selectedId: string | null;
  onSelect: (id: string | null) => void;
  onAgent: (id: string) => void;
  onTaskUpdated: (task: Item) => void;
};
export default function TasksPage({
  projectId,
  tasks,
  assets,
  agents,
  workflows,
  schedules,
  canEdit,
  mutate,
  notify,
  selectedId,
  onSelect,
  onAgent,
  onTaskUpdated,
}: Props) {
  const [create, setCreate] = useState<"task" | "schedule" | null>(null),
    [copy, setCopy] = useState<Item | undefined>(),
    [tab, setTab] = useState("tasks"),
    [search, setSearch] = useState(""),
    [status, setStatus] = useState("all");
  const list = tasks.filter(
    (t) =>
      (status === "all" ||
        (status === "active"
          ? !terminal.has(t.status)
          : t.status === status)) &&
      `${t.name || t.request?.name || ""} ${t.id}`
        .toLowerCase()
        .includes(search.toLowerCase()),
  );
  if (selectedId)
    return (
      <TaskDetail
        id={selectedId}
        agents={agents}
        canEdit={canEdit}
        mutate={mutate}
        notify={notify}
        onBack={() => onSelect(null)}
        onAgent={onAgent}
        onTaskUpdated={onTaskUpdated}
        onCopy={(request) => {
          setCopy(request);
          setCreate("task");
          onSelect(null);
        }}
      />
    );
  return (
    <>
      <SectionTitle
        eyebrow="TASK OPERATIONS"
        title="任务运行"
        description="从受理到自动评审、有限修订与交付，所有状态来自持久执行记录。"
        actions={
          <>
            {canEdit && (
              <>
                <button onClick={() => setCreate("schedule")}>
                  <CalendarClock size={18} />
                  创建计划
                </button>
                <button
                  className="primary"
                  onClick={() => {
                    setCopy(undefined);
                    setCreate("task");
                  }}
                >
                  <Plus size={18} />
                  新建任务
                </button>
              </>
            )}
          </>
        }
      />
      <Tabs
        value={tab}
        onChange={setTab}
        items={[
          { id: "tasks", label: `执行任务 · ${tasks.length}` },
          { id: "schedules", label: `持久计划 · ${schedules.length}` },
        ]}
      />
      {tab === "tasks" ? (
        <>
          <div className="toolbar">
            <div className="search">
              <Search size={17} />
              <input
                aria-label="搜索任务"
                placeholder="搜索名称或任务 ID"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
            </div>
            <select
              aria-label="任务状态筛选"
              value={status}
              onChange={(e) => setStatus(e.target.value)}
            >
              <option value="all">全部状态</option>
              <option value="active">进行中</option>
              <option value="succeeded">成功</option>
              <option value="partial">部分交付</option>
              <option value="failed">失败</option>
              <option value="cancelled">已取消</option>
            </select>
          </div>
          <Panel>
            {list.length ? (
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>任务</th>
                      <th>类型 / 模式</th>
                      <th>持久状态</th>
                      <th>创建时间</th>
                      <th>修订</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {list.map((t) => (
                      <tr key={t.id}>
                        <td>
                          <button
                            className="text-button"
                            onClick={() => onSelect(t.id)}
                          >
                            {t.name || t.request?.name || t.id}
                          </button>
                          <small className="mono">{t.id}</small>
                        </td>
                        <td>
                          {taskTypes[t.request?.task_type || t.task_type] ||
                            "—"}
                          <small>{modeName(t.request?.mode || t.mode)}</small>
                        </td>
                        <td>
                          <Badge status={t.status} />
                          {t.reason_code && (
                            <small className="reason">
                              {reasonNames[t.reason_code] || t.reason_code}
                            </small>
                          )}
                        </td>
                        <td>{time(t.created_at)}</td>
                        <td>{t.revision ?? 0}</td>
                        <td>
                          <button
                            className="icon-button"
                            aria-label={`查看任务 ${t.name || t.id}`}
                            onClick={() => onSelect(t.id)}
                          >
                            <ExternalLink size={17} />
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <Empty
                title="暂无符合条件的任务"
                description="选取已登记的数据，设置目标、质量规则和资源上限，提交后自动执行。"
                action={
                  canEdit && (
                    <button onClick={() => setCreate("task")}>创建任务</button>
                  )
                }
              />
            )}
          </Panel>
        </>
      ) : (
        <Panel>
          {schedules.length ? (
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>计划名称</th>
                    <th>间隔 / 时区</th>
                    <th>下次触发</th>
                    <th>状态</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {schedules.map((s) => (
                    <tr key={s.id}>
                      <td>
                        {s.name}
                        <small>
                          {taskTypes[s.task_template?.task_type] || "—"}
                        </small>
                      </td>
                      <td>
                        {s.interval_seconds} 秒<small>{s.timezone}</small>
                      </td>
                      <td>{time(s.next_run_at)}</td>
                      <td>
                        <Badge status={s.status} />
                      </td>
                      <td>
                        {canEdit && (
                          <button
                            onClick={() =>
                              void mutate(
                                () =>
                                  post(
                                    idPath("schedules", s.id) +
                                      (s.status === "disabled"
                                        ? "/enable"
                                        : "/disable"),
                                  ),
                                "计划状态已更新",
                              ).catch(() => {})
                            }
                          >
                            {s.status === "disabled" ? "启用" : "停用"}
                          </button>
                        )}
                        <JsonView value={s} label="计划配置" />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <Empty
              title="暂无持久计划"
              description="为周期性数据整理建立计划。触发、去重与下次运行时间由服务端保存。"
              action={
                canEdit && (
                  <button onClick={() => setCreate("schedule")}>
                    创建计划
                  </button>
                )
              }
            />
          )}
        </Panel>
      )}
      {create && (
        <TaskForm
          projectId={projectId}
          assets={assets}
          workflows={workflows}
          mutate={mutate}
          schedule={create === "schedule"}
          onClose={() => setCreate(null)}
          afterCreate={(item) => {
            if (create === "task") onSelect(item.id);
            else setTab("schedules");
          }}
          initial={create === "task" ? copy : undefined}
        />
      )}
    </>
  );
}
function TaskDetail({
  id,
  agents,
  canEdit,
  mutate,
  notify,
  onBack,
  onAgent,
  onCopy,
  onTaskUpdated,
}: {
  id: string;
  agents: Item[];
  canEdit: boolean;
  mutate: Mutation;
  notify: Notify;
  onBack: () => void;
  onAgent: (id: string) => void;
  onCopy: (request: Item) => void;
  onTaskUpdated: (task: Item) => void;
}) {
  const [task, setTask] = useState<Item | null>(null),
    [error, setError] = useState(""),
    [updated, setUpdated] = useState<string>(""),
    [tab, setTab] = useState("graph"),
    [cancel, setCancel] = useState(false),
    [inspect, setInspect] = useState<Item | null>(null),
    [artifact, setArtifact] = useState<Item | null>(null);
  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    setTask(null);
    setError("");
    async function refresh() {
      try {
        const data = await api(idPath("tasks", id), {
          signal: controller.signal,
        });
        if (active) {
          setTask(data);
          onTaskUpdated(data);
          setError("");
          setUpdated(new Date().toISOString());
        }
      } catch (e) {
        if (active && (e as Error).name !== "AbortError")
          setError((e as Error).message);
      }
    }
    void refresh();
    const timer = window.setInterval(refresh, 4000);
    return () => {
      active = false;
      controller.abort();
      window.clearInterval(timer);
    };
  }, [id, onTaskUpdated]);
  const graphNodes = useMemo(() => {
    if (!task) return [];
    const frozen =
      task.snapshot?.workflow?.nodes ||
      task.snapshot?.nodes ||
      task.snapshot?.workflow_snapshot?.nodes ||
      [];
    return (task.nodes || [])
      .filter((n: Item) => n.attempt === (task.revision || 0) + 1)
      .map((n: Item) => ({
        ...n,
        depends_on:
          n.depends_on ||
          frozen.find((f: Item) => f.id === n.node_id)?.depends_on ||
          [],
      }));
  }, [task]);
  async function showArtifact(item: Item) {
    try {
      setArtifact(await api(idPath("artifacts", item.id)));
    } catch (e) {
      notify((e as Error).message, true);
    }
  }
  if (!task)
    return (
      <>
        <button className="back-button" onClick={onBack}>
          <ArrowLeft size={17} />
          返回任务列表
        </button>
        {error ? (
          <ErrorLine>{error}</ErrorLine>
        ) : (
          <div className="loading">正在读取持久任务记录…</div>
        )}
      </>
    );
  const result = task.result || {};
  return (
    <>
      <button className="back-button" onClick={onBack}>
        <ArrowLeft size={17} />
        返回任务列表
      </button>
      <SectionTitle
        eyebrow="TASK TRACE"
        title={task.name || task.request?.name || "任务详情"}
        description={task.request?.goal}
        actions={
          canEdit && (
            <>
              <button
                onClick={() =>
                  onCopy({
                    ...task.request,
                    name: `${task.name || task.request.name}（重试）`,
                  })
                }
              >
                复制为新任务
              </button>
              {!terminal.has(task.status) && (
                <button
                  className="danger"
                  disabled={task.cancel_requested}
                  onClick={() => setCancel(true)}
                >
                  <Square size={16} />
                  {task.cancel_requested ? "取消请求已登记" : "请求取消"}
                </button>
              )}
            </>
          )
        }
      />
      <div className="task-strip">
        <Badge status={task.status} />
        <span>{modeName(task.request?.mode)}</span>
        <span>
          修订 {task.revision ?? 0} / {task.request?.max_revisions ?? "—"}
        </span>
        <span className="mono">{task.id}</span>
        <small>最近同步 {time(updated)}</small>
      </div>
      {error && (
        <ErrorLine>
          连接异常：{error}。当前显示最后一次同步的持久状态。
        </ErrorLine>
      )}
      {task.request?.mode === "rehearsal" && (
        <p className="warning">
          本任务为配置演练。任何产物均不代表真实模型推理或业务质量。
        </p>
      )}
      {task.reason_code && (
        <p className="warning">
          终止 / 降级原因：{reasonNames[task.reason_code] || task.reason_code}
        </p>
      )}
      {task.cancel_requested && !terminal.has(task.status) && (
        <p className="note">
          取消已请求，等待执行器确认；在途调用尚未被标记为释放。
        </p>
      )}
      <Tabs
        value={tab}
        onChange={setTab}
        items={[
          { id: "graph", label: "执行图与实例" },
          {
            id: "artifacts",
            label: `结果与产物 · ${task.artifacts?.length || 0}`,
          },
          { id: "events", label: `事件轨迹 · ${task.events?.length || 0}` },
          {
            id: "calls",
            label: `工具与技能 · ${task.tool_calls?.length || 0}`,
          },
          { id: "snapshot", label: "输入与冻结快照" },
        ]}
      />
      {tab === "graph" && (
        <>
          <Panel>
            <Graph nodes={graphNodes} agents={agents} onNode={setInspect} />
            <JsonView value={task.nodes} label="全部修订轮次的节点记录" />
          </Panel>
          <Panel title="运行实例">
            <div className="table-scroll">
              {task.instances?.length ? (
                <table>
                  <thead>
                    <tr>
                      <th>智能体 / 版本</th>
                      <th>实例状态</th>
                      <th>隔离上下文</th>
                      <th>实例 ID</th>
                    </tr>
                  </thead>
                  <tbody>
                    {task.instances.map((i: Item) => (
                      <tr key={i.id}>
                        <td>
                          <button
                            className="text-button"
                            onClick={() => onAgent(i.agent_id)}
                          >
                            {agents.find((a) => a.id === i.agent_id)?.name ||
                              i.agent_id}
                          </button>
                          <small>v{i.agent_version}</small>
                        </td>
                        <td>
                          <Badge status={i.status} />
                        </td>
                        <td className="mono small">{i.context_id}</td>
                        <td>
                          <button
                            className="text-button mono small"
                            onClick={() => setInspect(i)}
                          >
                            {i.id}
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                <Empty
                  title="尚无运行实例"
                  description="工作进程实际执行时创建实例，并绑定冻结版本。"
                />
              )}
            </div>
          </Panel>
        </>
      )}
      {tab === "artifacts" && (
        <>
          <Panel title="结果判定">
            {task.result ? (
              <>
                <div className="row-between">
                  <Badge status={result.status || task.status} />
                  <span>
                    {result.reason_code
                      ? reasonNames[result.reason_code] || result.reason_code
                      : "已记录结果包"}
                  </span>
                </div>
                {result.warnings?.map((w: any, i: number) => (
                  <p className="warning" key={i}>
                    {typeof w === "string" ? w : JSON.stringify(w)}
                  </p>
                ))}
                <JsonView
                  value={result.quality_verdict}
                  label="确定性校验与自动评审证据"
                />
                <JsonView value={result.usage} label="实际资源使用" />
                <JsonView value={result.lineage} label="来源血缘" />
                <JsonView value={result} label="完整结果包" />
              </>
            ) : (
              <p className="muted">任务尚未产生结果包。状态由服务端决定。</p>
            )}
          </Panel>
          <Panel title="交付产物">
            {task.artifacts?.length ? (
              task.artifacts.map((a: Item) => (
                <div className="artifact-row" key={a.id}>
                  <div>
                    <strong>{a.name || a.type || a.id}</strong>
                    <small>
                      {a.media_type} · {a.checksum?.slice(0, 16)}
                    </small>
                  </div>
                  <Badge status={a.quality_status || "unknown"} />
                  <button onClick={() => void showArtifact(a)}>查看内容</button>
                  <a
                    className="button"
                    href={`/api/v2/artifacts/${encodeURIComponent(a.id)}/download`}
                    download
                  >
                    <Download size={16} />
                    下载
                  </a>
                </div>
              ))
            ) : (
              <Empty
                title="尚无可交付产物"
                description="只有执行器实际保存的产物才会出现在这里。"
              />
            )}
          </Panel>
        </>
      )}
      {tab === "events" && (
        <Panel title="持久事件时间线">
          {task.events?.length ? (
            <ol className="timeline">
              {task.events.map((event: Item) => (
                <li key={event.id || event.sequence}>
                  <span className="timeline-dot" />
                  <div className="row-between">
                    <strong>{eventLabel(event.type)}</strong>
                    <small>{time(event.time || event.created_at)}</small>
                  </div>
                  <div className="tags">
                    <span>#{event.sequence}</span>
                    {event.node_id && <span>{event.node_id}</span>}
                    {event.trace_id && (
                      <span className="mono">{event.trace_id}</span>
                    )}
                  </div>
                  <JsonView value={event.payload} label="事件详情" />
                </li>
              ))}
            </ol>
          ) : (
            <Empty title="尚无事件" />
          )}
        </Panel>
      )}
      {tab === "calls" && (
        <Panel title="真实工具、MCP 与技能调用">
          <ToolCalls calls={task.tool_calls || []} />
        </Panel>
      )}
      {tab === "snapshot" && (
        <Panel title="请求与执行快照">
          <JsonView value={task.request} label="原始任务请求" />
          <JsonView value={task.snapshot} label="不可变配置快照" />
          <JsonView
            value={{
              lease_owner: task.lease_owner,
              lease_token: task.lease_token,
              lease_until: task.lease_until,
              attempt: task.attempt,
              created_at: task.created_at,
              updated_at: task.updated_at,
            }}
            label="租约与执行元数据"
          />
        </Panel>
      )}
      {cancel && (
        <Confirm
          title="请求取消任务"
          description="系统将登记取消请求，执行器在安全边界停止任务。在途模型调用可能产生费用，只有后端确认后才显示取消终态。"
          onClose={() => setCancel(false)}
          onConfirm={async () => {
            await mutate(
              () => post(idPath("tasks", id) + "/cancel"),
              "取消请求已登记",
            );
            setTask(await api(idPath("tasks", id)));
          }}
        />
      )}
      {inspect && (
        <Modal
          wide
          title={inspect.label || "运行记录"}
          onClose={() => setInspect(null)}
        >
          <div className="modal-body">
            <Badge status={inspect.status || "unknown"} />
            <pre>{JSON.stringify(inspect, null, 2)}</pre>
            <ToolCalls
              calls={(task.tool_calls || []).filter(
                (call: Item) =>
                  call.instance_id === inspect.instance_id ||
                  call.instance_id === inspect.id,
              )}
            />
          </div>
        </Modal>
      )}
      {artifact && (
        <Modal
          wide
          title={artifact.name || artifact.type || "产物内容"}
          onClose={() => setArtifact(null)}
        >
          <div className="modal-body">
            <Badge status={artifact.quality_status || "unknown"} />
            <pre className="prose">
              {typeof artifact.content === "string"
                ? artifact.content
                : JSON.stringify(artifact.content, null, 2)}
            </pre>
            <JsonView value={artifact.source_refs} label="来源引用" />
          </div>
        </Modal>
      )}
    </>
  );
}
export function ToolCalls({
  calls,
}: {
  calls: components["schemas"]["ToolCallRecord"][];
}) {
  const [detail, setDetail] = useState<Item | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState<string | null>(null);
  if (!calls.length)
    return (
      <Empty
        title="暂无工具调用"
        description="工具型 Agent 实际执行后显示记录；固定执行任务可能没有此类调用。"
      />
    );
  return (
    <>
      <div className="table-scroll">
        {error && <ErrorLine>{error}</ErrorLine>}
        <table>
          <thead>
            <tr>
              <th>能力 / 版本</th>
              <th>类型</th>
              <th>状态</th>
              <th>修订 / 耗时</th>
              <th>调用依据</th>
            </tr>
          </thead>
          <tbody>
            {calls.map((call) => (
              <tr key={call.id}>
                <td>
                  {call.capability_id}
                  <small>v{call.version}</small>
                </td>
                <td>
                  {{ tool: "内置工具", mcp: "MCP 服务", skill: "技能读取" }[
                    call.kind as string
                  ] || call.kind}
                </td>
                <td>
                  <Badge status={call.status} />
                  {call.error_code && (
                    <small>
                      {reasonNames[call.error_code] || call.error_code}
                    </small>
                  )}
                </td>
                <td>
                  {call.revision} / {call.duration_ms} ms
                </td>
                <td>
                  <JsonView value={call} label="参数摘要、来源与结果校验" />
                  <button
                    disabled={loading === call.id}
                    onClick={async () => {
                      setLoading(call.id);
                      setError("");
                      try {
                        setDetail(
                          await api(
                            `/v2/tasks/${encodeURIComponent(call.task_id)}/tool-calls/${encodeURIComponent(call.id)}`,
                          ),
                        );
                      } catch (error) {
                        setError((error as Error).message);
                      } finally {
                        setLoading(null);
                      }
                    }}
                  >
                    {loading === call.id ? "正在读取…" : "查看输入与输出"}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {detail && (
        <Modal wide title="工具调用详情" onClose={() => setDetail(null)}>
          <div className="modal-body">
            <JsonView value={detail.call} label="调用元数据" />
            <h3>实际参数</h3>
            <pre>{JSON.stringify(detail.arguments, null, 2)}</pre>
            <h3>实际输出</h3>
            <pre>{JSON.stringify(detail.result, null, 2)}</pre>
          </div>
        </Modal>
      )}
    </>
  );
}
function eventLabel(type: string) {
  return (
    (
      {
        "task.accepted": "任务已受理",
        "task.planning": "开始规划",
        "task.running": "开始执行",
        "task.completed": "任务完成",
        "task.failed": "任务失败",
        "task.cancel_requested": "请求取消",
        "task.cancelled": "任务已取消",
        "node.started": "节点开始",
        "node.completed": "节点完成",
        "review.completed": "自动评审完成",
        "task.revising": "进入有界修订",
        "task.succeeded": "任务成功",
        "task.partial": "部分交付",
      } as Record<string, string>
    )[type] || type
  );
}
