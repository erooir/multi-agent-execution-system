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
  Trash2,
} from "lucide-react";
import { api, ApiError, idPath, post, remove } from "./api";
import { money, reasonNames, taskTypes, terminal, time } from "./domain";
import type { Item, Mutation, Notify } from "./types";
import { NodeExecutionFacts, ToolCallDialog, ToolCalls } from "./TaskToolTrace";
import { roundNodes } from "./taskTraceModel";
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
import { ArtifactContent, QualityView } from "./ResultViews";
import { durationLabel, elapsedSeconds } from "./resultModel";
import TaskSummary from "./TaskSummary";
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
    [status, setStatus] = useState("all"),
    [deleting, setDeleting] = useState<Item | null>(null);
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
        key={selectedId}
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
        title="任务中心"
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
                          {canEdit && terminal.has(t.status) && (
                            <button
                              className="icon-button danger"
                              aria-label={`删除任务 ${t.name || t.request?.name || t.id}`}
                              onClick={() => setDeleting(t)}
                            >
                              <Trash2 size={17} />
                            </button>
                          )}
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
      {deleting && (
        <DeleteTaskConfirm
          task={deleting}
          mutate={mutate}
          onClose={() => setDeleting(null)}
          onDeleted={() => setDeleting(null)}
        />
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
    [tab, setTab] = useState("details"),
    [round, setRound] = useState<number | null>(null),
    [callId, setCallId] = useState<string | null>(null),
    [cancel, setCancel] = useState(false),
    [deleting, setDeleting] = useState(false),
    [inspect, setInspect] = useState<Item | null>(null),
    [artifact, setArtifact] = useState<Item | null>(null);
  useEffect(() => {
    let active = true;
    let refreshing = false;
    const controller = new AbortController();
    setTask(null);
    setError("");
    async function refresh() {
      if (refreshing) return;
      refreshing = true;
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
        if (active && (e as Error).name !== "AbortError") {
          setError((e as Error).message);
          if (e instanceof ApiError && [401, 403, 404].includes(e.status)) {
            setTask(null);
            setArtifact(null);
            setInspect(null);
            setCallId(null);
          }
        }
      } finally {
        refreshing = false;
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
  useEffect(() => {
    if (
      task?.content_withheld ||
      task?.result?.content_withheld ||
      task?.artifacts?.some((item: Item) => item.content_withheld)
    ) {
      setArtifact(null);
      setInspect(null);
      setCallId(null);
    }
  }, [task]);
  const graphNodes = useMemo(() => {
    if (!task) return [];
    const frozen =
      task.snapshot?.workflow?.nodes ||
      task.snapshot?.nodes ||
      task.snapshot?.workflow_snapshot?.nodes ||
      [];
    return roundNodes(task, round ?? task.revision ?? 0).map((n: Item) => ({
      ...n,
      depends_on:
        n.depends_on ||
        frozen.find((f: Item) => f.id === n.node_id)?.depends_on ||
        [],
    }));
  }, [task, round]);
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
  const quality = result.quality_verdict || task.quality_verdict;
  const activeRound = round ?? task.revision ?? 0;
  const selectedCall = (task.tool_calls || []).find(
    (c: Item) => c.id === callId,
  );
  const reviewEvents = (task.events || []).filter(
    (e: Item) => e.type === "quality.checked",
  );
  function showRound(revision: number) {
    setRound(revision);
    setTab("graph");
  }
  function showNode(record: Item) {
    const node = task?.nodes?.find(
      (item: Item) => item.instance_id === record.id,
    );
    setInspect(node ? { ...record, ...node } : record);
  }
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
        descriptionVisible
        actions={
          canEdit && (
            <>
              {(task.request?.task_type || task.task_type) ===
              "autonomous_analysis" ? (
                <a
                  className="button"
                  href={`/?project=${encodeURIComponent(task.project_id)}`}
                >
                  前往任务工作台新建
                </a>
              ) : (
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
              )}
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
              {terminal.has(task.status) && (
                <button className="danger" onClick={() => setDeleting(true)}>
                  <Trash2 size={16} />
                  删除任务
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
          {task.request?.service_class === "interactive"
            ? "交互任务"
            : "批量任务"}
        </span>
        <span>
          修订 / 恢复 {task.revision ?? 0} /{" "}
          {task.request?.max_revisions ?? "—"}
        </span>
        <span className="mono">{task.id}</span>
        <small>最近同步 {time(updated)}</small>
      </div>
      <div className="task-outcome-grid">
        <div>
          <small>执行进度</small>
          <strong>
            {graphNodes.filter((n: Item) => n.status === "succeeded").length} /{" "}
            {graphNodes.length} 节点
          </strong>
          <span>第 {activeRound + 1} 轮 · 含实际复用节点</span>
        </div>
        <div>
          <small>端到端耗时</small>
          <strong>
            {task.finished_at
              ? durationLabel(elapsedSeconds(task.created_at, task.finished_at))
              : "尚未结束"}
          </strong>
          <span>包含排队 · 时限 {task.request?.deadline_seconds} 秒</span>
        </div>
        <div>
          <small>可交付产物</small>
          <strong>{task.artifacts?.length || 0} 份</strong>
          <button className="text-button" onClick={() => setTab("artifacts")}>
            查看结果与质量依据
          </button>
        </div>
        <div>
          <small>本任务保守计费</small>
          <strong>
            {result.usage?.total_cny == null
              ? "待结算"
              : `¥ ${money(result.usage?.total_cny)}`}
          </strong>
          <span>
            {result.usage?.accounting_complete === false
              ? "含未确认费用，预留继续保留"
              : "以网关记录为准"}
          </span>
        </div>
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
          { id: "details", label: "任务详情" },
          { id: "graph", label: "执行图与实例" },
          { id: "review", label: "审核与迭代" },
          {
            id: "artifacts",
            label: `结果与产物 · ${task.artifacts?.length || 0}`,
          },
          { id: "events", label: `事件轨迹 · ${task.events?.length || 0}` },
          {
            id: "calls",
            label: `工具与技能（Skill） · ${task.tool_calls?.length || 0}`,
          },
          { id: "snapshot", label: "输入与冻结快照" },
        ]}
      />
      {tab === "details" && (
        <TaskSummary
          task={task}
          onNode={showNode}
          onArtifact={(item) => void showArtifact(item)}
        />
      )}
      {tab === "graph" && (
        <>
          <Panel>
            <div className="trace-round-picker">
              <div>
                <strong>节点执行记录</strong>
                <p>按轮次查看执行与复用的步骤。</p>
              </div>
              <label className="field">
                <span>执行轮次</span>
                <select
                  value={round === null ? "latest" : round}
                  onChange={(e) =>
                    setRound(
                      e.target.value === "latest"
                        ? null
                        : Number(e.target.value),
                    )
                  }
                >
                  <option value="latest">
                    跟随最新轮次（第 {(task.revision || 0) + 1} 轮）
                  </option>
                  {Array.from(
                    { length: (task.revision || 0) + 1 },
                    (_, rev) => (
                      <option key={rev} value={rev}>
                        第 {rev + 1} 轮
                      </option>
                    ),
                  )}
                </select>
              </label>
            </div>
            <Graph nodes={graphNodes} agents={agents} onNode={showNode} />
            <JsonView
              value={task.nodes}
              label="全部修订 / 恢复轮次的节点记录"
            />
          </Panel>
          {!!task.repair_history?.length && !task.content_withheld && (
            <RepairHistory history={task.repair_history} />
          )}
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
                            onClick={() => showNode(i)}
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
      {tab === "review" && (
        <>
          <Panel title="最近一次自动审核">
            {!task.content_withheld && Number.isInteger(quality?.revision) && (
              <p className="muted">
                本次判定来自第 {quality.revision + 1} 轮。
                {quality.revision < task.revision &&
                  "当前轮次尚未产生新的审核判定。"}
              </p>
            )}
            <QualityView quality={quality} withheld={task.content_withheld} />
          </Panel>
          {!task.content_withheld && (
            <>
              <Panel title="审核轨迹">
                {reviewEvents.length ? (
                  reviewEvents.map((event: Item) => (
                    <div className="trace-review-row" key={event.id}>
                      <strong>
                        {event.payload?.revision != null
                          ? "第 " + (event.payload.revision + 1) + " 轮"
                          : "自动审核"}
                      </strong>
                      <Badge
                        status={
                          event.payload?.passed === true
                            ? "passed"
                            : event.payload?.passed === false
                              ? "rejected"
                              : "unknown"
                        }
                      />
                      <small>{time(event.created_at || event.time)}</small>
                      {event.payload?.revision != null && (
                        <button
                          onClick={() => showRound(event.payload.revision)}
                        >
                          查看本轮执行图
                        </button>
                      )}
                    </div>
                  ))
                ) : (
                  <Empty title="暂无已记录的审核事件" />
                )}
              </Panel>
              {task.repair_history?.length ? (
                <RepairHistory
                  history={task.repair_history}
                  onRound={showRound}
                />
              ) : (
                <p className="muted">当前没有已记录的定向流程调整。</p>
              )}
            </>
          )}
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
                <QualityView
                  quality={result.quality_verdict}
                  withheld={result.content_withheld}
                />
                {task.scientific_review_note?.issues?.length > 0 && (
                  <div className="warning" role="note">
                    <strong>确定性复核补记 · 术语复核部分通过</strong>
                    {task.scientific_review_note.issues.map(
                      (
                        issue: {
                          message: string;
                          source_id: string;
                          start: number;
                        },
                        i: number,
                      ) => (
                        <p key={i}>
                          {issue.message} 来源 {issue.source_id}，字符位置{" "}
                          {issue.start}。
                        </p>
                      ),
                    )}
                  </div>
                )}
                {task.semantic_evidence &&
                  task.status === "succeeded" &&
                  !result.content_withheld && (
                    <a
                      className="button"
                      download
                      href={`/api/v2/tasks/${encodeURIComponent(task.id)}/semantic-evidence/report`}
                    >
                      下载中文取证报告
                    </a>
                  )}
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
                  <button
                    disabled={a.content_withheld || task.content_withheld}
                    onClick={() => void showArtifact(a)}
                  >
                    查看内容
                  </button>
                  {a.content_withheld || task.content_withheld ? (
                    <small>来源不可用，产物已屏蔽</small>
                  ) : (
                    <a
                      className="button"
                      href={`/api/v2/artifacts/${encodeURIComponent(a.id)}/download`}
                      download
                    >
                      <Download size={16} />
                      下载
                    </a>
                  )}
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
        <Panel title="真实工具、MCP 与技能（Skill）调用">
          <ToolCalls
            task={task}
            calls={task.tool_calls || []}
            onSelect={setCallId}
          />
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
      {deleting && (
        <DeleteTaskConfirm
          task={task}
          mutate={mutate}
          onClose={() => setDeleting(false)}
          onDeleted={onBack}
        />
      )}
      {inspect && !task.content_withheld && (
        <Modal
          wide
          title={inspect.label || "运行记录"}
          onClose={() => setInspect(null)}
        >
          <div className="modal-body">
            <Badge status={inspect.status || "unknown"} />
            <dl className="trace-facts">
              <dt>智能体</dt>
              <dd>
                {agents.find((a) => a.id === inspect.agent_id)?.name ||
                  inspect.agent_id ||
                  "未记录"}{" "}
                · v{inspect.agent_version ?? "—"}
              </dd>
              <dt>步骤 / 实例</dt>
              <dd>
                {inspect.node_id || "—"} ·{" "}
                <span className="mono">
                  {inspect.instance_id || inspect.id}
                </span>
              </dd>
              <dt>执行轮次</dt>
              <dd>第 {inspect.attempt ?? "未记录"} 轮</dd>
              <dt>开始时间</dt>
              <dd>{time(inspect.started_at)}</dd>
              <dt>结束时间</dt>
              <dd>{time(inspect.finished_at)}</dd>
              <dt>执行耗时</dt>
              <dd>
                {durationLabel(
                  elapsedSeconds(inspect.started_at, inspect.finished_at),
                )}
              </dd>
              <dt>任务输入资料</dt>
              <dd>
                {task.snapshot?.inputs
                  ?.map((a: Item) => a.name || a.id)
                  .join("、") || "未记录"}
              </dd>
            </dl>
            {inspect.error && (
              <p className="warning">
                {typeof inspect.error === "string"
                  ? reasonNames[inspect.error] || inspect.error
                  : inspect.error.message || JSON.stringify(inspect.error)}
              </p>
            )}
            <NodeExecutionFacts node={inspect} />
            {inspect.output && (
              <>
                <h3>步骤输出</h3>
                <ArtifactContent
                  artifact={{
                    type: inspect.output.type,
                    content: inspect.output.content ?? inspect.output,
                  }}
                  calls={task.tool_calls || []}
                  onCall={setCallId}
                />
              </>
            )}
            <JsonView value={inspect} label="完整运行记录 JSON" />
            <h3>本节点实际工具记录</h3>
            <ToolCalls
              task={task}
              onSelect={setCallId}
              calls={(task.tool_calls || []).filter(
                (call: Item) =>
                  call.instance_id === inspect.instance_id ||
                  call.instance_id === inspect.id,
              )}
            />
          </div>
        </Modal>
      )}
      {artifact && !task.content_withheld && (
        <Modal
          wide
          title={artifact.name || artifact.type || "产物内容"}
          onClose={() => setArtifact(null)}
        >
          <div className="modal-body">
            <Badge status={artifact.quality_status || "unknown"} />
            <ArtifactContent
              artifact={artifact}
              calls={task.tool_calls || []}
              onCall={setCallId}
            />
            <JsonView value={artifact.source_refs} label="来源引用" />
          </div>
        </Modal>
      )}
      {selectedCall && !task.content_withheld && (
        <ToolCallDialog
          key={selectedCall.id}
          task={task}
          call={selectedCall}
          onClose={() => setCallId(null)}
        />
      )}
    </>
  );
}
function DeleteTaskConfirm({
  task,
  mutate,
  onClose,
  onDeleted,
}: {
  task: Item;
  mutate: Mutation;
  onClose: () => void;
  onDeleted: () => void;
}) {
  return (
    <Confirm
      title="删除已结束任务"
      danger
      confirm="确认删除"
      description={`将“${task.name || task.request?.name || task.id}”从任务列表移除。历史运行记录、评测引用和累计费用仍保留，删除不会退还或重置预算。`}
      onClose={onClose}
      onConfirm={async () => {
        await mutate(() => remove(idPath("tasks", task.id)), "任务已删除");
        onDeleted();
      }}
    />
  );
}
const stageLabels: Record<string, string> = {
  prepare: "资料解析",
  analyze: "专业分析",
  review: "独立核查",
};

function RepairHistory({
  history,
  onRound,
}: {
  history: Item[];
  onRound?: (revision: number) => void;
}) {
  const stages = (values: string[] = []) =>
    values.map((key) => stageLabels[key] || key).join("、");
  return (
    <Panel title="技术恢复与业务修复记录">
      {history.map((plan, i) => (
        <div className="case-repair" key={i}>
          <h4>
            {plan.decision === "revise"
              ? `第 ${plan.revision + 1} 轮 · ${plan.entry_kind === "technical_recovery" ? "技术错误恢复" : plan.label}`
              : "停止自动修订"}
          </h4>
          <p>{plan.reason}</p>
          <dl>
            <dt>
              {plan.entry_kind === "technical_recovery"
                ? "技术错误"
                : "上轮未通过"}
            </dt>
            <dd>
              {plan.entry_kind === "technical_recovery"
                ? plan.reason_code || plan.error_category || "—"
                : plan.failed_checks?.join("、") || "—"}
            </dd>
            {!!plan.targets?.length && (
              <>
                <dt>
                  {plan.entry_kind === "technical_recovery"
                    ? "关联业务对象"
                    : "修复对象"}
                </dt>
                <dd>{plan.targets.join("、")}</dd>
              </>
            )}
            {plan.decision === "revise" && (
              <>
                <dt>执行步骤</dt>
                <dd>{stages(plan.rerun_stages)}</dd>
                <dt>复用步骤</dt>
                <dd>{stages(plan.reused_stages) || "无"}</dd>
                <dt>
                  {plan.entry_kind === "technical_recovery"
                    ? "恢复结果"
                    : "复核结果"}
                </dt>
                <dd>
                  {plan.entry_kind === "technical_recovery"
                    ? plan.recovery_outcome
                      ? plan.recovery_outcome.passed
                        ? "恢复完成，业务复核通过"
                        : "恢复完成，业务复核仍有未通过项"
                      : "待恢复"
                    : plan.outcome
                      ? plan.outcome.passed
                        ? "通过"
                        : "仍有未通过项"
                      : "待复核"}
                </dd>
              </>
            )}
            {!!plan.outcome?.resolved_checks?.length && (
              <>
                <dt>已解决</dt>
                <dd>{plan.outcome.resolved_checks.join("、")}</dd>
              </>
            )}
            {plan.before_counts && plan.outcome?.after_counts && (
              <>
                <dt>核查变化</dt>
                <dd>
                  未通过检查 {plan.before_counts.failed_checks} →{" "}
                  {plan.outcome.after_counts.failed_checks} 项；可定位证据{" "}
                  {plan.before_counts.evidence} →{" "}
                  {plan.outcome.after_counts.evidence} 条
                </dd>
              </>
            )}
            {!!plan.outcome?.remaining_checks?.length && (
              <>
                <dt>仍未通过</dt>
                <dd>{plan.outcome.remaining_checks.join("、")}</dd>
              </>
            )}
            {!!plan.outcome?.new_checks?.length && (
              <>
                <dt>新增问题</dt>
                <dd>{plan.outcome.new_checks.join("、")}</dd>
              </>
            )}
          </dl>
          {onRound && (
            <div className="toolbar">
              {Number.isInteger(plan.source_revision) && (
                <button onClick={() => onRound(plan.source_revision)}>
                  查看问题发生轮次
                </button>
              )}
              {plan.decision === "revise" &&
                Number.isInteger(plan.revision) && (
                  <button onClick={() => onRound(plan.revision)}>
                    查看调整后的执行图
                  </button>
                )}
            </div>
          )}
        </div>
      ))}
    </Panel>
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
        "node.failed": "节点本次尝试失败",
        "node.reused": "复用成功节点结果",
        "model.recoverable": "模型错误已确认可安全恢复",
        "review.retrying": "评审协议修正重试",
        "review.completed": "自动评审完成",
        "task.revising": "进入有界修订 / 错误恢复",
        "task.repair_planned": "已选择修复路径",
        "task.repair_stopped": "停止自动修订",
        "task.repair_reviewed": "修复路径复核完成",
        "task.succeeded": "任务成功",
        "task.partial": "部分交付",
      } as Record<string, string>
    )[type] || type
  );
}
