import { useProject } from "./ProjectContext";
import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { remarkReportCitations } from "./remarkReportCitations";
import {
  Activity,
  ArrowLeft,
  ArrowRight,
  Check,
  CheckCheck,
  ChevronDown,
  ChevronRight,
  Clock,
  Download,
  Edit3,
  ExternalLink,
  FileCheck2,
  FileText,
  FlaskConical,
  History,
  LoaderCircle,
  Play,
  Plus,
  RefreshCw,
  Search,
  ShieldCheck,
  Square,
  Timer,
  Trash2,
  UserPlus,
  Users,
  X,
  Zap,
} from "lucide-react";
import { api, post, put, money, time, type RecordData } from "./api";
import { type PageProps, categoryNames, roleNames } from "./types";
import {
  ActionButton,
  Badge,
  Empty,
  Field,
  InlineMessage,
  JsonView,
  Modal,
  ModeBadge,
  Panel,
  SectionTitle,
} from "./ui";
import { EvidenceCard, TraceView, SkillTraceHeader } from "./ResourcePages";
import { capabilityErrorText } from "./capabilities";
function useTask(p: PageProps) {
  return async (fn: () => Promise<any>, message?: string) => {
    try {
      const r = await fn();
      await p.refresh();
      if (message) p.notify(message);
      return r;
    } catch (e) {
      p.notify((e as Error).message, true);
      return undefined;
    }
  };
}
function useRecord(path: string | null) {
  const [value, setValue] = useState<any>(null),
    [error, setError] = useState("");
  useEffect(() => {
    setValue(null);
    setError("");
    if (!path) return;
    let cancelled = false;
    const fetchData = async () => {
      try {
        const r = await api(path);
        if (!cancelled) {
          setValue(r);
          setError("");
        }
      } catch (e) {
        if (!cancelled) setError((e as Error).message);
      }
    };
    fetchData();
    const t = setInterval(fetchData, 3500);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, [path]);
  return { value, error, setValue };
}
export function RunsPage(p: PageProps) {
  const [project, setProject] = useProject();
  const [selected, setSelected] = useState<string | null>(
      p.selectedId && !p.selectedId.startsWith("workflow:")
        ? p.selectedId
        : null,
    ),
    [creating, setCreating] = useState(
      Boolean(p.selectedId?.startsWith("workflow:")),
    ),
    [workflow, setWorkflow] = useState(
      p.selectedId?.startsWith("workflow:")
        ? p.selectedId.slice(9)
        : p.data.workflows?.find(
            (w: any) =>
              w.status === "published" &&
              w.category ===
                p.data.projects?.find((x: any) => x.id === project)?.category,
          )?.id ||
            p.data.workflows?.find((w: any) => w.status === "published")?.id ||
            "",
    ),
    [prompt, setPrompt] = useState(""),
    [mode, setMode] = useState(p.data.system?.default_mode || "rehearsal"),
    [docIds, setDocIds] = useState<string[]>([]),
    [file, setFile] = useState<File | null>(null),
    [visibility, setVisibility] = useState("external"),
    [keepInLibrary, setKeepInLibrary] = useState(false),
    [uploads, setUploads] = useState<{ id: string; name: string }[]>([]),
    [filter, setFilter] = useState("all");
  const { value: run, error } = useRecord(
      selected ? `/runs/${selected}` : null,
    ),
    task = useTask(p);
  const uploadAttachment = async () => {
    const body = new FormData();
    body.append("file", file!);
    body.append("project_id", project);
    body.append("visibility", visibility);
    body.append("temporary", keepInLibrary ? "false" : "true");
    const doc = await api("/documents/upload", { method: "POST", body });
    setUploads((list) => [...list, { id: doc.id, name: doc.name }]);
    setDocIds((ids) => [...ids, doc.id]);
    setFile(null);
    return doc;
  };
  useEffect(() => {
    if (!creating || !workflow) return;
    const w = (p.data.workflows || []).find((x: any) => x.id === workflow);
    if (!w) return;
    setPrompt(w.source_prompt || "");
    setMode(w.preferred_mode || p.data.system?.default_mode || "rehearsal");
  }, [workflow, creating]);
  const items = (p.data.runs || []).filter(
    (r: any) => filter === "all" || r.status === filter,
  );
  return (
    <>
      <SectionTitle
        eyebrow="EXECUTION CENTER"
        title="运行中心"
        detail="查看节点执行、证据来源和模型用量，支持取消、重试与人工审核。"
        actions={
          <button
            className="button primary"
            disabled={!p.canEdit}
            onClick={() => setCreating(true)}
          >
            <Plus size={17} />
            发起研究任务
          </button>
        }
      />
      {selected ? (
        <>
          <button
            className="text-button back-link"
            onClick={() => setSelected(null)}
          >
            <ArrowLeft size={16} />
            返回全部运行
          </button>
          {error && <InlineMessage error>{error}</InlineMessage>}
          {run ? (
            <RunDetail run={run} p={p} onRetry={(r) => setSelected(r.id)} />
          ) : !error ? (
            <div className="loading">
              <LoaderCircle className="spin" />
              正在加载执行过程…
            </div>
          ) : null}
        </>
      ) : (
        <>
          <div className="toolbar">
            <div className="tabs">
              {[
                ["all", "全部任务"],
                ["running", "运行中"],
                ["waiting_review", "待审核"],
                ["completed", "已完成"],
                ["failed", "失败"],
              ].map(([id, label]) => (
                <button
                  key={id}
                  className={filter === id ? "active" : ""}
                  onClick={() => setFilter(id)}
                >
                  {label}
                </button>
              ))}
            </div>
            <span className="muted">{items.length} 个任务 · 自动刷新</span>
          </div>
          <Panel>
            {items.length ? (
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>研究任务</th>
                      <th>执行模式</th>
                      <th>进度</th>
                      <th>状态</th>
                      <th>创建时间</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {items.map((r: any) => (
                      <tr key={r.id}>
                        <td>
                          <button
                            className="table-title"
                            onClick={() => setSelected(r.id)}
                          >
                            {r.name || r.workflow_name || "研究任务"}
                          </button>
                          <small className="table-description">
                            {r.prompt?.slice(0, 65)}
                          </small>
                        </td>
                        <td>
                          <ModeBadge mode={r.mode} />
                        </td>
                        <td>
                          <div className="progress-inline">
                            <div className="progress-track">
                              <div
                                style={{
                                  width: `${normalizeProgress(r.progress)}%`,
                                }}
                              />
                            </div>
                            <small>{normalizeProgress(r.progress)}%</small>
                          </div>
                        </td>
                        <td>
                          <Badge status={r.status} />
                        </td>
                        <td className="muted">{time(r.created_at)}</td>
                        <td>
                          <button
                            className="icon-button"
                            title="运行详情"
                            onClick={() => setSelected(r.id)}
                          >
                            <ChevronRight size={17} />
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <Empty
                title="尚无研究任务"
                detail="选择一个研究流程，发起真实模型调用或明确标记的本地演练。"
              />
            )}
          </Panel>
        </>
      )}
      {creating && (
        <Modal title="发起研究任务" onClose={() => setCreating(false)} wide>
          <div className="form-grid">
            <Field label="研究流程">
              <select
                value={workflow}
                onChange={(e) => setWorkflow(e.target.value)}
              >
                <option value="">选择流程</option>
                {(p.data.workflows || []).map((w: any) => (
                  <option
                    key={w.id}
                    value={w.id}
                    disabled={w.status !== "published"}
                  >
                    {w.name} · {w.status === "published" ? "已发布" : "草稿"}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="研究项目">
              <select
                value={project}
                onChange={(e) => {
                  setProject(e.target.value);
                  setDocIds([]);
                }}
              >
                <option value="">选择项目</option>
                {(p.data.projects || []).map((x: any) => (
                  <option key={x.id} value={x.id}>
                    {x.name}
                  </option>
                ))}
              </select>
            </Field>
          </div>
          <Field label="研究需求">
            <textarea
              rows={4}
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              placeholder="明确研究对象、需要回答的问题和报告要求"
            />
          </Field>
          <Field label="执行模式">
            <select value={mode} onChange={(e) => setMode(e.target.value)}>
              <option value="rehearsal">
                本地演练 · 真实本地检索 + 确定性报告
              </option>
              <option value="live">真实模型 · DeepSeek（受预算限制）</option>
            </select>
          </Field>
          <details className="doc-selection">
            <summary>指定参考资料（未选时由流程检索当前项目资料）</summary>
            <div className="checkbox-grid">
              {(p.data.documents || [])
                .filter((d: any) => d.project_id === project)
                .map((d: any) => (
                  <label key={d.id} className="checkbox-label">
                    <input
                      type="checkbox"
                      checked={docIds.includes(d.id)}
                      onChange={(e) =>
                        setDocIds(
                          e.target.checked
                            ? [...docIds, d.id]
                            : docIds.filter((id) => id !== d.id),
                        )
                      }
                    />
                    {d.name}
                    {d.visibility === "local" && <small>本地限定</small>}
                  </label>
                ))}
            </div>
          </details>
          <Field
            label="上传参考资料"
            hint="上传后自动加入本次任务的指定资料，由文档解析节点处理后交给下游分析。仅限本地的资料不会发送给外部模型。"
          >
            <div className="upload-inline">
              <input
                type="file"
                aria-label="选择要上传的资料"
                accept=".txt,.md,.csv,.pdf,.docx,.png,.jpg,.jpeg,.webp"
                onChange={(e) => setFile(e.target.files?.[0] || null)}
              />
              <select
                aria-label="资料使用范围"
                value={visibility}
                onChange={(e) => setVisibility(e.target.value)}
              >
                <option value="external">允许外部模型使用</option>
                <option value="local">仅限本地检索</option>
              </select>
              <ActionButton
                className="button small"
                disabled={!file || !project}
                onClick={() =>
                  task(async () => {
                    await uploadAttachment();
                  }, "资料已上传并附加到本次任务")
                }
              >
                上传并附加
              </ActionButton>
            </div>
            <label className="checkbox-label">
              <input
                type="checkbox"
                checked={keepInLibrary}
                onChange={(e) => setKeepInLibrary(e.target.checked)}
              />
              保存到项目资料库（不勾选则仅本次任务使用，不进入资料库检索）
            </label>
            {uploads.length > 0 && (
              <div className="uploaded-chips">
                {uploads.map((u) => (
                  <span className="uploaded-chip" key={u.id}>
                    {u.name}
                    <button
                      type="button"
                      aria-label={`移除 ${u.name}`}
                      onClick={() => {
                        setUploads((list) => list.filter((x) => x.id !== u.id));
                        setDocIds((ids) => ids.filter((id) => id !== u.id));
                      }}
                    >
                      ×
                    </button>
                  </span>
                ))}
              </div>
            )}
          </Field>
          <InlineMessage>
            {mode === "live"
              ? `实际调用将计入统一预算。当前预算余量 ¥ ${Number(p.data.budget?.remaining_cny || 0).toFixed(2)}。本地限定资料不会外发。`
              : "演练不调用模型、不产生 API 费用；其结论仅用于验证流程，将在运行和报告中明确标识。"}
          </InlineMessage>
          <div className="modal-actions">
            <button className="button" onClick={() => setCreating(false)}>
              取消
            </button>
            <ActionButton
              className="button primary"
              disabled={!workflow || !project || !prompt.trim()}
              onClick={() =>
                task(async () => {
                  let ids = docIds;
                  if (file) {
                    const doc = await uploadAttachment();
                    ids = [...ids, doc.id];
                  }
                  const r = await post("/runs", {
                    workflow_id: workflow,
                    project_id: project,
                    prompt,
                    mode,
                    document_ids: ids.length ? ids : undefined,
                  });
                  setCreating(false);
                  setSelected(r.id);
                }, "研究任务已提交")
              }
            >
              <Play size={15} />
              开始运行
            </ActionButton>
          </div>
        </Modal>
      )}
    </>
  );
}
function normalizeProgress(value: any) {
  const n = Number(value || 0);
  return Math.max(0, Math.min(100, Math.round(n > 0 && n < 1 ? n * 100 : n)));
}
// 节点输出中的能力调用链：payload.trace 层级展示，错误按稳定错误码呈现。
function NodeCapabilityTrace({ run, step }: { run: any; step: any }) {
  const payload = step.payload;
  const trace = payload?.trace;
  const error = payload?.error;
  const failedCall = (run.capability_calls || []).find(
    (c: any) => c.node_id === step.node_id && c.error_code,
  );
  if (!trace?.tool_calls?.length && !trace?.tool_id && !error && !failedCall)
    return null;
  return (
    <div className="node-trace">
      {payload?.skill_id && (
        <SkillTraceHeader
          skillId={payload.skill_id}
          status={payload.status}
          durationMs={trace?.duration_ms}
        />
      )}
      <TraceView
        trace={trace}
        error={error}
        indent={payload?.skill_id ? 1 : 0}
      />
      {!error && failedCall && (
        <div className="trace-row error">
          <Badge status="failed">
            {capabilityErrorText(failedCall.error_code)}
          </Badge>
          <span className="muted">{failedCall.skill_id}</span>
        </div>
      )}
    </div>
  );
}
function RunDetail({
  run,
  p,
  onRetry,
}: {
  run: any;
  p: PageProps;
  onRetry: (r: any) => void;
}) {
  const [tab, setTab] = useState("steps"),
    [step, setStep] = useState<string | null>(null),
    [review, setReview] = useState(false),
    [feedback, setFeedback] = useState(""),
    [decision, setDecision] = useState("approve"),
    [reviewContent, setReviewContent] = useState("");
  const task = useTask(p),
    steps = run.steps || [],
    active = steps.find((s: any) => s.node_id === step);
  return (
    <>
      <Panel className="run-detail-summary">
        <div className="run-summary-top">
          <div>
            <div className="eyebrow">RUN · {run.id?.slice(-10)}</div>
            <h2>{run.name || run.workflow_name}</h2>
            <p>{run.prompt}</p>
          </div>
          <Badge status={run.status} />
        </div>
        <div className="run-info">
          <ModeBadge mode={run.mode} />
          <span>流程 v{run.workflow_version || 1}</span>
          <span>{time(run.created_at)}</span>
          <span>
            {
              steps.filter((x: any) =>
                ["completed", "success", "done"].includes(x.status),
              ).length
            }{" "}
            / {steps.length} 节点完成
          </span>
        </div>
        <div className="progress-track large">
          <div style={{ width: `${normalizeProgress(run.progress)}%` }} />
        </div>
        {run.error && (
          <InlineMessage error>
            {typeof run.error === "string"
              ? run.error
              : JSON.stringify(run.error)}
          </InlineMessage>
        )}
        <div className="run-controls">
          {["queued", "running", "waiting_review"].includes(run.status) && (
            <ActionButton
              disabled={!p.canEdit}
              className="button danger"
              onClick={() =>
                task(() => post(`/runs/${run.id}/cancel`), "任务已取消")
              }
            >
              <Square size={14} />
              取消运行
            </ActionButton>
          )}
          {["failed", "cancelled", "interrupted"].includes(run.status) && (
            <ActionButton
              disabled={!p.canEdit}
              onClick={() =>
                task(
                  async () => onRetry(await post(`/runs/${run.id}/retry`)),
                  "已提交重试任务",
                )
              }
            >
              <RefreshCw size={15} />
              重试
            </ActionButton>
          )}
          {run.status === "waiting_review" && (
            <button
              className="button primary"
              disabled={!p.canReview}
              onClick={() =>
                task(async () => {
                  const approval = (p.data.approvals || []).find(
                    (a: any) => a.run_id === run.id && a.status === "pending",
                  );
                  if (run.report_id) {
                    const draft = await api(`/reports/${run.report_id}`);
                    setReviewContent(draft.content || approval?.content || "");
                  } else {
                    setReviewContent(approval?.content || "");
                  }
                  setReview(true);
                })
              }
            >
              <ShieldCheck size={15} />
              审核当前结果
            </button>
          )}
          {run.report_id && (
            <button
              className="button"
              onClick={() => p.go("reports", run.report_id)}
            >
              <FileCheck2 size={15} />
              查看研究报告
              <ArrowRight size={14} />
            </button>
          )}
          {run.usage && (
            <span className="muted">模型用量：{JSON.stringify(run.usage)}</span>
          )}
        </div>
      </Panel>
      <div className="tabs content-tabs">
        {[
          ["steps", "节点时间线"],
          ["evidence", `引用证据 ${run.evidence?.length || 0}`],
          ["logs", "执行日志"],
        ].map(([id, label]) => (
          <button
            key={id}
            className={tab === id ? "active" : ""}
            onClick={() => setTab(id)}
          >
            {label}
          </button>
        ))}
      </div>
      {tab === "steps" ? (
        <div className="execution-grid">
          <Panel
            title="执行过程"
            detail="每个节点的状态由后端执行引擎持久化保存。"
          >
            <div className="timeline">
              {steps.length ? (
                steps.map((s: any, i: number) => (
                  <button
                    key={`${s.node_id}-${i}`}
                    className={`timeline-item ${s.status} ${step === s.node_id ? "selected" : ""}`}
                    onClick={() => setStep(s.node_id)}
                  >
                    <span className="timeline-marker">
                      {["completed", "success", "done"].includes(s.status) ? (
                        <Check size={14} />
                      ) : s.status === "running" ? (
                        <LoaderCircle size={14} className="spin" />
                      ) : (
                        i + 1
                      )}
                    </span>
                    <div>
                      <strong>{s.label || s.kind}</strong>
                      <small>
                        {s.finished_at
                          ? time(s.finished_at)
                          : s.started_at
                            ? time(s.started_at)
                            : "等待执行"}
                      </small>
                    </div>
                    <Badge status={s.status} />
                    <ChevronRight size={14} />
                  </button>
                ))
              ) : (
                <Empty title="等待执行引擎准备节点" />
              )}
            </div>
          </Panel>
          <Panel
            title={active ? active.label : "节点输出"}
            detail="选择一个节点查看其输入、输出及执行错误。"
          >
            {active ? (
              <>
                {active.error && (
                  <InlineMessage error>{active.error}</InlineMessage>
                )}
                <NodeCapabilityTrace run={run} step={active} />
                <JsonView
                  value={
                    active.payload ?? {
                      status: active.status,
                      note: "该节点暂无输出",
                    }
                  }
                />
              </>
            ) : (
              <Empty
                title="选择节点查看详情"
                detail="节点输出会随执行进度自动更新。"
              />
            )}
          </Panel>
        </div>
      ) : tab === "evidence" ? (
        <div className="evidence-results">
          {run.evidence?.length ? (
            run.evidence.map((e: any, i: number) => (
              <EvidenceCard key={e.id || i} evidence={e} index={i} />
            ))
          ) : (
            <Empty
              title="本次运行尚无证据"
              detail="检索节点执行后，实际匹配来源会显示在这里。"
            />
          )}
        </div>
      ) : (
        <Panel title="执行事件">
          <div className="log-list">
            {run.logs?.length ? (
              run.logs.map((l: any, i: number) => (
                <div className="log-entry" key={i}>
                  <span>{time(l.timestamp || l.created_at || l.time)}</span>
                  <code>
                    {typeof l === "string"
                      ? l
                      : l.message || l.event || JSON.stringify(l)}
                  </code>
                </div>
              ))
            ) : (
              <Empty title="暂无执行日志" />
            )}
          </div>
        </Panel>
      )}
      {review && (
        <Modal title="审核运行结果" onClose={() => setReview(false)} wide>
          <ModeBadge mode={run.mode} />
          <Field label="审核决定">
            <select
              value={decision}
              onChange={(e) => setDecision(e.target.value)}
            >
              <option value="approve">通过并继续执行</option>
              <option value="reject">退回 / 不通过</option>
            </select>
          </Field>
          <Field label="审核意见">
            <textarea
              rows={3}
              value={feedback}
              onChange={(e) => setFeedback(e.target.value)}
              placeholder="记录需要关注的证据、修订说明或退回原因"
            />
          </Field>
          <Field
            label="审核稿（可修改）"
            hint="如已有草稿，将自动载入。请保留证据引用和演练标识，修改会随审核一并提交。"
          >
            <textarea
              rows={12}
              value={reviewContent}
              onChange={(e) => setReviewContent(e.target.value)}
            />
          </Field>
          <div className="modal-actions">
            <button className="button" onClick={() => setReview(false)}>
              取消
            </button>
            <ActionButton
              className="button primary"
              onClick={() =>
                task(
                  async () => {
                    await post(`/runs/${run.id}/review`, {
                      decision,
                      feedback,
                      content: reviewContent || undefined,
                    });
                    setReview(false);
                  },
                  decision === "approve"
                    ? "审核通过，运行将继续"
                    : "已提交退回意见",
                )
              }
            >
              提交审核
            </ActionButton>
          </div>
        </Modal>
      )}
    </>
  );
}
export function ReportsPage(p: PageProps) {
  const [selected, setSelected] = useState<string | null>(p.selectedId || null),
    [editing, setEditing] = useState(false),
    [content, setContent] = useState(""),
    [title, setTitle] = useState(""),
    [versions, setVersions] = useState<any[] | null>(null),
    [citationIndex, setCitationIndex] = useState<number | null>(null),
    [tab, setTab] = useState("report");
  const {
      value: report,
      error,
      setValue,
    } = useRecord(selected ? `/reports/${selected}` : null),
    task = useTask(p),
    pending = (p.data.runs || []).filter(
      (r: any) => r.status === "waiting_review",
    );
  return (
    <>
      <SectionTitle
        eyebrow="REVIEW & DELIVERY"
        title="审核与报告"
        detail="审阅生成过程与证据，编辑报告并保留可恢复的版本记录。"
      />
      {pending.length > 0 && (
        <div className="review-banner">
          <ShieldCheck size={24} />
          <div>
            <strong>{pending.length} 项研究任务等待审核</strong>
            <p>审核节点已暂停执行，确认结果后才能进入后续步骤。</p>
          </div>
          <button
            className="button"
            onClick={() => p.go("runs", pending[0].id)}
          >
            进入审核
            <ArrowRight size={15} />
          </button>
        </div>
      )}
      {selected ? (
        <>
          <button
            className="text-button back-link"
            onClick={() => {
              setSelected(null);
              setEditing(false);
              setCitationIndex(null);
            }}
          >
            <ArrowLeft size={16} />
            返回报告列表
          </button>
          {error && <InlineMessage error>{error}</InlineMessage>}
          {report ? (
            <>
              <Panel className="report-header">
                <div>
                  <div className="report-mode">
                    <Badge status={report.status} />
                    {(report.mode ||
                      p.data.runs?.find((r: any) => r.id === report.run_id)
                        ?.mode) && (
                      <ModeBadge
                        mode={
                          report.mode ||
                          p.data.runs.find((r: any) => r.id === report.run_id)
                            ?.mode
                        }
                      />
                    )}
                  </div>
                  <h2>{report.title}</h2>
                  <p>
                    版本 {report.version || 1} · 最近更新{" "}
                    {time(report.updated_at)} ·{" "}
                    {categoryNames[report.category] || "研究报告"}
                  </p>
                </div>
                <div className="report-actions">
                  <ActionButton
                    className="button ghost"
                    onClick={() =>
                      task(async () =>
                        setVersions(
                          await api(`/reports/${report.id}/versions`),
                        ),
                      )
                    }
                  >
                    <History size={15} />
                    版本
                  </ActionButton>
                  <button
                    className="button"
                    disabled={!p.canReview && !p.canEdit}
                    onClick={() => {
                      setTitle(report.title);
                      setContent(report.content);
                      setEditing(true);
                    }}
                  >
                    <Edit3 size={15} />
                    编辑报告
                  </button>
                  <details className="download-menu">
                    <summary className="button primary">
                      <Download size={15} />
                      导出
                      <ChevronDown size={14} />
                    </summary>
                    <div>
                      {[
                        ["md", "Markdown"],
                        ["docx", "Word 文档"],
                        ["html", "HTML 页面"],
                      ].map(([f, label]) => (
                        <a
                          key={f}
                          href={`/api/reports/${report.id}/export?format=${f}`}
                        >
                          {label}
                        </a>
                      ))}
                    </div>
                  </details>
                </div>
              </Panel>
              <div className="tabs content-tabs">
                <button
                  className={tab === "report" ? "active" : ""}
                  onClick={() => setTab("report")}
                >
                  报告正文
                </button>
                <button
                  className={tab === "evidence" ? "active" : ""}
                  onClick={() => setTab("evidence")}
                >
                  引用来源 {report.citations?.length || 0}
                </button>
                <button onClick={() => p.go("runs", report.run_id)}>
                  查看运行过程
                  <ArrowRight size={13} />
                </button>
              </div>
              {tab === "report" ? (
                <article className="report-paper">
                  <div className="paper-label">RESEARCH BRIEF / 研究成果</div>
                  <div className="markdown">
                    <ReactMarkdown
                      remarkPlugins={[
                        remarkGfm,
                        [
                          remarkReportCitations,
                          { citations: report.citations || [] },
                        ],
                      ]}
                      components={{
                        a: ({ node, href, children, ...props }) => {
                          const marker =
                            node?.properties?.["data-report-citation"];
                          if (marker === "unresolved") {
                            return (
                              <span
                                className="report-citation-unresolved"
                                title="此来源 ID 不在当前报告的引用清单中，无法解析。"
                              >
                                {children}
                                <strong>无法解析</strong>
                              </span>
                            );
                          }
                          if (typeof marker === "string") {
                            const index = Number(marker) - 1;
                            const citation = report.citations?.[index];
                            if (citation && typeof citation !== "string") {
                              return (
                                <button
                                  type="button"
                                  className="report-citation"
                                  title={
                                    citation.document_name ||
                                    `引用来源 ${index + 1}`
                                  }
                                  aria-label={`查看引用 ${index + 1}：${citation.document_name || "来源详情"}`}
                                  onClick={() => setCitationIndex(index)}
                                >
                                  {children}
                                </button>
                              );
                            }
                          }
                          return (
                            <a href={href} {...props}>
                              {children}
                            </a>
                          );
                        },
                      }}
                    >
                      {report.content || "暂无报告内容。"}
                    </ReactMarkdown>
                  </div>
                </article>
              ) : (
                <div className="evidence-results">
                  {report.citations?.length ? (
                    report.citations.map((e: any, i: number) =>
                      typeof e === "string" ? (
                        <Panel key={i}>
                          <p>{e}</p>
                        </Panel>
                      ) : (
                        <EvidenceCard key={e.id || i} evidence={e} index={i} />
                      ),
                    )
                  ) : (
                    <Empty
                      title="此报告暂无引用来源"
                      detail="缺少来源的报告需要人工进一步核查。"
                    />
                  )}
                </div>
              )}
            </>
          ) : !error ? (
            <div className="loading">
              <LoaderCircle className="spin" />
              正在加载报告…
            </div>
          ) : null}
        </>
      ) : (
        <Panel title="研究成果库" detail="报告来源于实际执行的研究流程。">
          {p.data.reports?.length ? (
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>报告标题</th>
                    <th>领域</th>
                    <th>版本</th>
                    <th>审核状态</th>
                    <th>更新时间</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {p.data.reports.map((r: any) => (
                    <tr key={r.id}>
                      <td>
                        <button
                          className="table-title"
                          onClick={() => setSelected(r.id)}
                        >
                          <FileText size={17} />
                          {r.title}
                        </button>
                      </td>
                      <td>
                        {categoryNames[r.category] || r.category || "研究报告"}
                      </td>
                      <td>v{r.version || 1}</td>
                      <td>
                        <Badge status={r.status} />
                      </td>
                      <td className="muted">
                        {time(r.updated_at || r.created_at)}
                      </td>
                      <td>
                        <button
                          className="icon-button"
                          onClick={() => setSelected(r.id)}
                          title="阅读报告"
                        >
                          <ChevronRight size={17} />
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <Empty
              title="报告将在研究完成后汇集于此"
              detail="启动研究流程，完成检索、分析和审核后即可查看成果。"
            />
          )}
        </Panel>
      )}
      {citationIndex !== null && report?.citations?.[citationIndex] && (
        <Modal
          title={`引用来源 [${citationIndex + 1}]`}
          onClose={() => setCitationIndex(null)}
          wide
        >
          <EvidenceCard
            evidence={report.citations[citationIndex]}
            index={citationIndex}
          />
          {report.citations[citationIndex].document_id && (
            <div className="modal-actions">
              <a
                className="button"
                href={`/api/documents/${report.citations[citationIndex].document_id}/file`}
              >
                <Download size={15} />
                下载来源资料
              </a>
            </div>
          )}
          {report.citations[citationIndex].source_uri && (
            <div className="modal-actions">
              <a
                className="button"
                href={report.citations[citationIndex].source_uri}
                target="_blank"
                rel="noreferrer"
              >
                <ExternalLink size={15} />
                打开外部来源
              </a>
            </div>
          )}
        </Modal>
      )}
      {editing && report && (
        <Modal title="编辑研究报告" wide onClose={() => setEditing(false)}>
          <Field label="报告标题">
            <input value={title} onChange={(e) => setTitle(e.target.value)} />
          </Field>
          <Field
            label="Markdown 正文"
            hint="保存将产生新的历史版本。请保留来源编号和演练标记。"
          >
            <textarea
              className="code-input report-editor"
              rows={18}
              value={content}
              onChange={(e) => setContent(e.target.value)}
            />
          </Field>
          <div className="modal-actions">
            <button className="button" onClick={() => setEditing(false)}>
              取消
            </button>
            <ActionButton
              className="button primary"
              disabled={!title.trim() || !content.trim()}
              onClick={() =>
                task(async () => {
                  setValue(
                    await put(`/reports/${report.id}`, { title, content }),
                  );
                  setEditing(false);
                }, "报告已保存，新版本已记录")
              }
            >
              保存新版本
            </ActionButton>
          </div>
        </Modal>
      )}
      {versions && report && (
        <Modal title="报告版本历史" onClose={() => setVersions(null)}>
          {versions.length ? (
            versions.map((v: any, i: number) => (
              <div className="list-row" key={v.id || v.version || i}>
                <div>
                  <strong>
                    版本 {v.version || i + 1} · {v.title || report.title}
                  </strong>
                  <p>{time(v.created_at || v.updated_at)}</p>
                </div>
                <ActionButton
                  disabled={!p.canReview && !p.canEdit}
                  onClick={() =>
                    task(async () => {
                      setValue(
                        await post(`/reports/${report.id}/restore`, {
                          version: v.version,
                        }),
                      );
                      setVersions(null);
                    }, "报告版本已恢复")
                  }
                >
                  恢复
                </ActionButton>
              </div>
            ))
          ) : (
            <Empty title="暂无历史版本" />
          )}
        </Modal>
      )}
    </>
  );
}
export function EvaluationsPage(p: PageProps) {
  const [selectedSamples, setSelectedSamples] = useState<string[]>([]),
    [mode, setMode] = useState(p.data.system?.default_mode || "rehearsal"),
    [workflow, setWorkflow] = useState(""),
    [selected, setSelected] = useState<string | null>(null),
    [filter, setFilter] = useState("all");
  const { value: evaluation, error } = useRecord(
      selected ? `/evaluations/${selected}` : null,
    ),
    task = useTask(p),
    samples = (p.data.samples || []).filter(
      (s: any) => filter === "all" || s.category === filter,
    );
  return (
    <>
      <SectionTitle
        eyebrow="QUALITY EVALUATION"
        title="样本评测"
        detail="对公开安全样本执行实际流程，逐项检查证据、结构与完成状态。"
      />
      <div className="evaluation-layout">
        <Panel
          title="选择评测样本"
          detail="评测指标来自本次实际执行，不预填效果分数。"
          actions={
            <button
              className="text-button"
              onClick={() =>
                setSelectedSamples(
                  selectedSamples.length === samples.length
                    ? []
                    : samples.map((s: any) => s.id),
                )
              }
            >
              {selectedSamples.length === samples.length
                ? "取消全选"
                : "选择当前全部"}
            </button>
          }
        >
          <div className="evaluation-filters">
            <select
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              aria-label="样本分类"
            >
              <option value="all">全部领域</option>
              {Object.entries(categoryNames).map(([k, v]) => (
                <option key={k} value={k}>
                  {v}
                </option>
              ))}
            </select>
            <span>
              {selectedSamples.length} / {p.data.samples?.length || 0} 已选
            </span>
          </div>
          <div className="sample-list">
            {samples.map((s: any) => (
              <label className="sample-item" key={s.id}>
                <input
                  type="checkbox"
                  checked={selectedSamples.includes(s.id)}
                  onChange={(e) =>
                    setSelectedSamples(
                      e.target.checked
                        ? [...selectedSamples, s.id]
                        : selectedSamples.filter((id) => id !== s.id),
                    )
                  }
                />
                <div>
                  <strong>
                    {s.name || s.title || s.prompt?.slice(0, 40) || s.id}
                  </strong>
                  <p>{s.prompt || s.query || s.description}</p>
                  <Badge>
                    {categoryNames[s.category] || s.category || "通用"}
                  </Badge>
                </div>
              </label>
            ))}
          </div>
        </Panel>
        <div>
          <Panel title="评测配置">
            <Field label="执行模式">
              <select value={mode} onChange={(e) => setMode(e.target.value)}>
                <option value="rehearsal">本地演练</option>
                <option value="live">真实模型 · 计费</option>
              </select>
            </Field>
            <Field label="指定流程（可选）">
              <select
                value={workflow}
                onChange={(e) => setWorkflow(e.target.value)}
              >
                <option value="">按样本匹配流程</option>
                {(p.data.workflows || []).map((w: any) => (
                  <option key={w.id} value={w.id}>
                    {w.name}
                  </option>
                ))}
              </select>
            </Field>
            <InlineMessage>
              {mode === "live"
                ? "批量评测会产生真实 API 用量，所有请求仍受统一预算约束。"
                : "演练用于检验流程完整性和规则，不代表大模型的实际研究质量。"}
            </InlineMessage>
            <ActionButton
              className="button primary full-width"
              disabled={!p.canEdit || !selectedSamples.length}
              onClick={() =>
                task(async () => {
                  const r = await post("/evaluations", {
                    sample_ids: selectedSamples,
                    mode,
                    workflow_id: workflow || undefined,
                  });
                  setSelected(r.id);
                }, "评测任务已提交")
              }
            >
              <FlaskConical size={17} />
              运行 {selectedSamples.length} 个样本
            </ActionButton>
          </Panel>
          <Panel title="评测记录" className="evaluation-history">
            {p.data.evaluations?.length ? (
              p.data.evaluations.map((e: any) => (
                <button
                  className={`evaluation-record ${selected === e.id ? "active" : ""}`}
                  key={e.id}
                  onClick={() => setSelected(e.id)}
                >
                  <div>
                    <strong>
                      {e.total ?? e.sample_ids?.length ?? "—"} 个样本
                    </strong>
                    <small>{time(e.created_at)}</small>
                  </div>
                  <Badge status={e.status} />
                </button>
              ))
            ) : (
              <Empty title="尚无评测记录" detail="选择样本并开始第一次评测。" />
            )}
          </Panel>
        </div>
      </div>
      {error && <InlineMessage error>{error}</InlineMessage>}
      {evaluation && (
        <Panel
          title="本次评测结果"
          detail={`已完成 ${evaluation.completed || 0} / ${evaluation.total || 0} 个样本`}
          actions={
            <>
              <ModeBadge mode={evaluation.mode} />
              <Badge status={evaluation.status} />
            </>
          }
        >
          <div className="evaluation-stats">
            <div>
              <small>检查通过</small>
              <strong>{evaluation.passed ?? "—"}</strong>
            </div>
            <div>
              <small>检查失败</small>
              <strong>{evaluation.failed ?? "—"}</strong>
            </div>
            <div>
              <small>检查得分</small>
              <strong>
                {evaluation.score !== undefined
                  ? Number(evaluation.score).toFixed(2)
                  : "—"}
              </strong>
            </div>
          </div>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>样本</th>
                  <th>状态</th>
                  <th>检查项</th>
                  <th>得分</th>
                  <th>详情</th>
                </tr>
              </thead>
              <tbody>
                {(evaluation.results || []).map((r: any, i: number) => (
                  <tr key={r.sample_id || i}>
                    <td>
                      {r.prompt ||
                        p.data.samples?.find((s: any) => s.id === r.sample_id)
                          ?.prompt ||
                        r.sample_id}
                    </td>
                    <td>
                      <Badge status={r.status} />
                    </td>
                    <td>
                      <div className="check-results">
                        {Object.entries(r.checks || {}).map(([k, v]) => (
                          <span key={k} className={v ? "ok" : "no"}>
                            {v ? "✓" : "×"} {k}
                          </span>
                        ))}
                      </div>
                      {r.error && <span className="error-text">{r.error}</span>}
                    </td>
                    <td>{r.score ?? "—"}</td>
                    <td>
                      {r.run_id && (
                        <button
                          className="text-button"
                          onClick={() => p.go("runs", r.run_id)}
                        >
                          查看运行
                          <ArrowRight size={14} />
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {evaluation.metrics && (
            <details className="metric-details">
              <summary>完整评测指标</summary>
              <JsonView value={evaluation.metrics} />
            </details>
          )}
        </Panel>
      )}
    </>
  );
}
export function SystemPage(p: PageProps) {
  const [audits, setAudits] = useState<any[]>([]),
    [settings, setSettings] = useState<any>(null),
    [settingsText, setSettingsText] = useState(""),
    [error, setError] = useState("");
  const task = useTask(p),
    budget = p.data.budget || {},
    system = p.data.system || {};
  useEffect(() => {
    Promise.all([api("/audits"), api("/settings")])
      .then(([a, s]) => {
        setAudits(Array.isArray(a) ? a : []);
        setSettings(s);
        setSettingsText(JSON.stringify(s, null, 2));
      })
      .catch((e) => setError(e.message));
  }, []);
  return (
    <>
      <SectionTitle
        eyebrow="SYSTEM & GOVERNANCE"
        title="系统与审计"
        detail="统一管理模型预算、运行环境和关键操作记录。"
        actions={
          <ActionButton
            onClick={() =>
              task(async () => {
                setAudits(await api("/audits"));
                const s = await api("/settings");
                setSettings(s);
                setSettingsText(JSON.stringify(s, null, 2));
              }, "系统数据已刷新")
            }
          >
            <RefreshCw size={15} />
            刷新状态
          </ActionButton>
        }
      />
      {error && <InlineMessage error>{error}</InlineMessage>}
      <div className="system-columns">
        <Panel title="模型预算" detail="所有真实调用通过统一网关计费和预留。">
          <div className="budget-total">
            <small>可用预算 / 总额</small>
            <strong>
              ¥ {Number(budget.remaining_cny || 0).toFixed(2)}{" "}
              <span>/ ¥ {Number(budget.limit_cny || 300).toFixed(2)}</span>
            </strong>
          </div>
          <div className="budget-bar">
            <div
              style={{
                width: `${Math.min(100, (Number(budget.spent_cny || 0) / Number(budget.limit_cny || 300)) * 100)}%`,
              }}
            />
            <span
              style={{
                width: `${Math.min(100, (Number(budget.reserved_cny || 0) / Number(budget.limit_cny || 300)) * 100)}%`,
              }}
            />
          </div>
          <div className="budget-items">
            <div>
              <span>累计计费上界</span>
              <strong>¥ {money(budget.spent_cny)}</strong>
            </div>
            <div>
              <span>进行中预留</span>
              <strong>¥ {money(budget.reserved_cny)}</strong>
            </div>
            <div>
              <span>调用请求</span>
              <strong>{budget.request_count || 0} 次</strong>
            </div>
            <div>
              <span>待核对请求</span>
              <strong>{budget.uncertain_count || 0} 次</strong>
            </div>
            <div>
              <span>输入 Token</span>
              <strong>
                {Number(budget.input_tokens || 0).toLocaleString()}
              </strong>
            </div>
            <div>
              <span>输出 Token</span>
              <strong>
                {Number(budget.output_tokens || 0).toLocaleString()}
              </strong>
            </div>
          </div>
          <div className="budget-note">
            {budget.pricing_note ||
              "用量按预算网关记录展示，包含保守计费估计；以服务商实际账单为准。"}
          </div>
        </Panel>
        <Panel title="运行环境" detail="只展示安全配置，不返回凭据内容。">
          <div className="system-facts">
            {[
              ["运行框架", "Agno AgentOS"],
              ["Agno 版本", system.agno_version || "未报告"],
              ["模型", system.model || "deepseek-flash"],
              [
                "API Key",
                system.key_configured ? "已从用户环境变量加载" : "尚未配置",
              ],
              ["运行模式", system.mode || "local"],
              [
                "向量 / Embedding",
                typeof system.embedding_status === "object"
                  ? JSON.stringify(system.embedding_status)
                  : system.embedding_status || "未报告",
              ],
            ].map(([k, v]) => (
              <div key={k}>
                <span>{k}</span>
                <strong>{v}</strong>
              </div>
            ))}
          </div>
          <div className="workspace-note">
            <ShieldCheck size={22} />
            <p>
              累计预算最高 300
              元。没有凭据时可使用明确标记的本地演练；外部调用失败不会静默伪装成功。
            </p>
          </div>
        </Panel>
      </div>
      {settings && (
        <SettingsForm
          settings={settings}
          admin={p.data.user?.role === "admin"}
          onSave={(value) =>
            task(async () => {
              const s = await put("/settings", value);
              setSettings(s);
            }, "配置已更新")
          }
        />
      )}
      {p.data.user?.role === "admin" && <UsersPanel p={p} />}
      <Panel
        title="操作审计"
        detail="记录资源变更、执行、审批与系统操作。"
        className="audit-panel"
      >
        {audits.length ? (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>时间</th>
                  <th>操作人</th>
                  <th>操作</th>
                  <th>对象</th>
                  <th>详细记录</th>
                </tr>
              </thead>
              <tbody>
                {audits.slice(0, 100).map((a: any, i) => (
                  <tr key={a.id || i}>
                    <td className="muted nowrap">
                      {time(a.created_at || a.timestamp)}
                    </td>
                    <td>{a.user || a.username || "system"}</td>
                    <td>
                      <code>{a.action}</code>
                    </td>
                    <td>{a.entity}</td>
                    <td>
                      <details>
                        <summary>查看详情</summary>
                        <JsonView value={a.detail} />
                      </details>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty
            title="暂无审计记录"
            detail="资源修改和任务执行后会产生实际操作记录。"
          />
        )}
      </Panel>
    </>
  );
}

function SettingsForm({
  settings,
  admin,
  onSave,
}: {
  settings: any;
  admin: boolean;
  onSave: (value: any) => Promise<any>;
}) {
  const [limit, setLimit] = useState(String(settings.budget_limit_cny || 300)),
    [mode, setMode] = useState(settings.default_mode || "rehearsal"),
    [tokens, setTokens] = useState(String(settings.max_output_tokens || 2000));
  return (
    <Panel
      title="运行设置"
      detail="仅管理员可修改。总预算最高 300 元，模型服务地址由运行环境管理。"
    >
      <div className="settings-form">
        <div className="settings-grid">
          <Field label="累计预算上限（元）">
            <input
              type="number"
              min="0.01"
              max="300"
              step="0.01"
              disabled={!admin}
              value={limit}
              onChange={(e) => setLimit(e.target.value)}
            />
          </Field>
          <Field label="默认执行模式">
            <select
              disabled={!admin}
              value={mode}
              onChange={(e) => setMode(e.target.value)}
            >
              <option value="rehearsal">本地演练</option>
              <option value="live">真实模型</option>
            </select>
          </Field>
          <Field label="每次输出 Token 上限">
            <input
              type="number"
              min="256"
              max="6000"
              step="1"
              disabled={!admin}
              value={tokens}
              onChange={(e) => setTokens(e.target.value)}
            />
          </Field>
        </div>
        <ActionButton
          className="button primary"
          disabled={
            !admin ||
            Number(limit) <= 0 ||
            Number(limit) > 300 ||
            Number(tokens) < 256 ||
            Number(tokens) > 6000
          }
          onClick={() =>
            onSave({
              budget_limit_cny: Number(limit),
              default_mode: mode,
              max_output_tokens: Number(tokens),
            })
          }
        >
          保存运行设置
        </ActionButton>
      </div>
    </Panel>
  );
}

function UsersPanel({ p }: { p: PageProps }) {
  const [users, setUsers] = useState<any[] | null>(null),
    [creating, setCreating] = useState(false),
    [draft, setDraft] = useState({
      username: "",
      password: "",
      name: "",
      role: "operator",
    }),
    [editing, setEditing] = useState<any | null>(null),
    [error, setError] = useState("");
  const task = useTask(p);
  const load = async () => setUsers(await api("/users"));
  useEffect(() => {
    load().catch((e) => setError(e.message));
  }, []);
  return (
    <Panel
      title="用户管理"
      detail="仅管理员可见。注册入口只创建研究员账号，审核员由管理员在此创建。"
      actions={
        <button
          className="button primary small"
          onClick={() => {
            setDraft({
              username: "",
              password: "",
              name: "",
              role: "operator",
            });
            setCreating(true);
          }}
        >
          <UserPlus size={15} />
          新建用户
        </button>
      }
    >
      {error && <InlineMessage error>{error}</InlineMessage>}
      {users === null ? (
        <Empty title="正在加载用户列表" />
      ) : (
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>用户名</th>
                <th>显示名</th>
                <th>角色</th>
                <th>状态</th>
                <th>创建时间</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {users.map((u: any) => (
                <tr key={u.username}>
                  <td>
                    <code>{u.username}</code>
                  </td>
                  <td>{u.name}</td>
                  <td>
                    <Badge>{roleNames[u.role] || u.role}</Badge>
                  </td>
                  <td>
                    <Badge status={u.enabled === false ? "pending" : "ready"}>
                      {u.enabled === false ? "已停用" : "启用中"}
                    </Badge>
                  </td>
                  <td className="muted">{time(u.created_at)}</td>
                  <td>
                    <div className="row-actions">
                      <button
                        className="text-button"
                        onClick={() =>
                          setEditing({
                            username: u.username,
                            name: u.name,
                            role: u.role,
                            password: "",
                          })
                        }
                      >
                        <Edit3 size={14} />
                        编辑
                      </button>
                      {u.username !== p.data.user?.username && (
                        <ActionButton
                          className="text-button"
                          onClick={() =>
                            task(
                              async () => {
                                await put(`/users/${u.username}`, {
                                  enabled: u.enabled === false,
                                });
                                await load();
                              },
                              u.enabled === false
                                ? "账号已启用"
                                : "账号已停用，会话已注销",
                            )
                          }
                        >
                          {u.enabled === false ? "启用" : "停用"}
                        </ActionButton>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {creating && (
        <Modal title="新建用户" onClose={() => setCreating(false)}>
          <Field
            label="用户名"
            hint="3–32 位字母、数字或下划线，不区分大小写。"
          >
            <input
              value={draft.username}
              onChange={(e) => setDraft({ ...draft, username: e.target.value })}
              placeholder="例如 reviewer_li"
            />
          </Field>
          <Field label="显示名（选填）">
            <input
              value={draft.name}
              maxLength={40}
              onChange={(e) => setDraft({ ...draft, name: e.target.value })}
            />
          </Field>
          <Field
            label="初始密码"
            hint="8–128 个字符，请线下告知对方并尽快修改。"
          >
            <input
              type="password"
              value={draft.password}
              onChange={(e) => setDraft({ ...draft, password: e.target.value })}
            />
          </Field>
          <Field label="角色" hint="管理员账号不能通过界面创建。">
            <select
              value={draft.role}
              onChange={(e) => setDraft({ ...draft, role: e.target.value })}
            >
              <option value="operator">研究员 · 创建与运行任务</option>
              <option value="reviewer">审核员 · 审阅与人工确认</option>
            </select>
          </Field>
          <div className="modal-actions">
            <button className="button" onClick={() => setCreating(false)}>
              取消
            </button>
            <ActionButton
              className="button primary"
              disabled={!draft.username.trim() || draft.password.length < 8}
              onClick={() =>
                task(async () => {
                  await post("/users", {
                    username: draft.username.trim(),
                    password: draft.password,
                    name: draft.name.trim() || undefined,
                    role: draft.role,
                  });
                  setCreating(false);
                  await load();
                }, "用户已创建")
              }
            >
              创建用户
            </ActionButton>
          </div>
        </Modal>
      )}
      {editing && (
        <Modal
          title={`编辑用户 · ${editing.username}`}
          onClose={() => setEditing(null)}
        >
          <Field label="显示名">
            <input
              value={editing.name}
              maxLength={40}
              onChange={(e) => setEditing({ ...editing, name: e.target.value })}
            />
          </Field>
          <Field label="角色">
            <select
              value={editing.role}
              onChange={(e) => setEditing({ ...editing, role: e.target.value })}
            >
              <option value="operator">研究员</option>
              <option value="reviewer">审核员</option>
              {editing.role === "admin" && (
                <option value="admin">管理员</option>
              )}
            </select>
          </Field>
          <Field
            label="重置密码"
            hint="留空则不修改密码；重置后该用户的会话会被注销。"
          >
            <input
              type="password"
              value={editing.password}
              onChange={(e) =>
                setEditing({ ...editing, password: e.target.value })
              }
              placeholder="输入新密码（至少 8 位）"
            />
          </Field>
          <div className="modal-actions">
            <button className="button" onClick={() => setEditing(null)}>
              取消
            </button>
            <ActionButton
              className="button primary"
              disabled={
                !editing.name.trim() ||
                (editing.password && editing.password.length < 8)
              }
              onClick={() =>
                task(async () => {
                  await put(`/users/${editing.username}`, {
                    name: editing.name.trim(),
                    role: editing.role,
                    ...(editing.password ? { password: editing.password } : {}),
                  });
                  setEditing(null);
                  await load();
                }, "用户信息已更新")
              }
            >
              保存修改
            </ActionButton>
          </div>
        </Modal>
      )}
    </Panel>
  );
}
