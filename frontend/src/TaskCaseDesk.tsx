import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
} from "react";
import {
  ArrowRight,
  Check,
  CheckCircle2,
  ChevronLeft,
  Circle,
  ClipboardList,
  Download,
  FileText,
  LoaderCircle,
  Plus,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  Trash2,
  Upload,
  X,
} from "lucide-react";
import { api, ApiError, post, remove } from "./api";
import { time, statusNames } from "./domain";
import { Confirm, Empty, ErrorLine } from "./ui";
import { QuestionProducts } from "./QuestionProducts";
import MonthlyResultChart from "./MonthlyResultChart";
import ScriptExecution from "./ScriptExecution";
import ExecutionPlanHistory from "./ExecutionPlanHistory";
import CitationBoundaryRepair from "./CitationBoundaryRepair";
import {
  bytesLabel,
  citationSourceUrl,
  taskCheckLabels,
  repairLabel,
  repairMeasurements,
  stageLabels,
  taskCaseActive,
  taskCaseCanStart,
  taskCaseEditable,
  taskCaseHeading,
  taskCaseResumable,
  taskCaseStatus,
  taskCaseTerminal,
  textValue,
  type TaskCase,
} from "./taskCases";
import "./question-desk.css";
import "./task-cases.css";

const base = "/v2/task-cases";
const path = (id: string) => `${base}/${encodeURIComponent(id)}`;

function State({ status }: { status: string }) {
  return (
    <span className={`taskcase-status is-${status}`}>
      {taskCaseActive.has(status) ? (
        <LoaderCircle size={13} className="taskcase-spin" />
      ) : status === "succeeded" ? (
        <CheckCircle2 size={13} />
      ) : (
        <Circle size={11} />
      )}
      {taskCaseStatus[status] || status}
    </span>
  );
}

export default function TaskCaseDesk({
  projectId,
  canSubmit,
}: {
  projectId: string;
  canSubmit: boolean;
}) {
  const [tasks, setTasks] = useState<TaskCase[]>([]);
  const [selected, setSelected] = useState("");
  const [detail, setDetail] = useState<TaskCase | null>(null);
  const [composing, setComposing] = useState(false);
  const [loading, setLoading] = useState(true);
  const [listError, setListError] = useState("");
  const [detailError, setDetailError] = useState("");
  const [tick, setTick] = useState(0);
  const [notice, setNotice] = useState("");
  const deletedIds = useRef(new Set<string>());
  const selectedRef = useRef(selected);
  selectedRef.current = selected;
  const key = "hangzhi.task-case.selection." + projectId;

  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function load() {
      try {
        const rows = await api<TaskCase[]>(
          `${base}?project_id=${encodeURIComponent(projectId)}`,
          { signal: controller.signal },
        );
        if (controller.signal.aborted) return;
        const visibleRows = rows.filter(
          (row) => !deletedIds.current.has(row.id),
        );
        setTasks(visibleRows);
        setListError("");
        setSelected((old) => {
          let saved =
            new URLSearchParams(window.location.search).get("task_case") || "";
          try {
            saved ||= sessionStorage.getItem(key) || "";
          } catch {
            /* Optional session preference. */
          }
          return visibleRows.some((r) => r.id === (old || saved))
            ? old || saved
            : visibleRows[0]?.id || "";
        });
      } catch (e) {
        if (controller.signal.aborted) return;
        setListError((e as Error).message);
        if (e instanceof ApiError && [401, 403, 404].includes(e.status)) {
          setTasks([]);
          setDetail(null);
          setSelected("");
        }
      } finally {
        if (!controller.signal.aborted) {
          setLoading(false);
          timer = setTimeout(load, 7000);
        }
      }
    }
    void load();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [projectId, key, tick]);

  useEffect(() => {
    if (!selected) {
      setDetail(null);
      return;
    }
    try {
      sessionStorage.setItem(key, selected);
    } catch {
      /* History remains on the server. */
    }
    setDetail((old) => (old?.id === selected ? old : null));
    setDetailError("");
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function load() {
      let delay = 6000;
      try {
        const row = await api<TaskCase>(path(selected), {
          signal: controller.signal,
        });
        if (
          controller.signal.aborted ||
          selectedRef.current !== selected ||
          deletedIds.current.has(row.id)
        )
          return;
        setDetail(row);
        setDetailError("");
        setTasks((rows) => rows.map((t) => (t.id === row.id ? row : t)));
        if (taskCaseActive.has(row.status)) delay = 1800;
      } catch (e) {
        if (controller.signal.aborted) return;
        setDetailError((e as Error).message);
        // Do not retain readable results after source/project access is revoked.
        if (e instanceof ApiError && [401, 403, 404].includes(e.status))
          setDetail(null);
      } finally {
        if (!controller.signal.aborted) timer = setTimeout(load, delay);
      }
    }
    void load();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [selected, key, tick]);

  const accept = useCallback((row: TaskCase) => {
    setTasks((rows) => [row, ...rows.filter((t) => t.id !== row.id)]);
    setSelected(row.id);
    setDetail(row);
    setComposing(false);
    setDetailError("");
    setNotice("");
    setTick((n) => n + 1);
  }, []);

  function discard(id: string) {
    deletedIds.current.add(id);
    const next = tasks.find((task) => task.id !== id)?.id || "";
    selectedRef.current = next;
    setTasks((rows) => rows.filter((task) => task.id !== id));
    setSelected(next);
    setDetail(null);
    setDetailError("");
    setComposing(false);
    setNotice("任务记录已删除。");
    try {
      sessionStorage.removeItem(key);
    } catch {
      /* Optional session preference. */
    }
    const url = new URL(window.location.href);
    if (url.searchParams.get("task_case") === id) {
      url.searchParams.delete("task_case");
      window.history.replaceState(null, "", url);
    }
    setTick((n) => n + 1);
  }

  return (
    <div className="taskcase-desk">
      <div className="taskcase-heading">
        <div>
          <h1>我的任务</h1>
        </div>
        {canSubmit && (
          <button className="primary" onClick={() => setComposing(true)}>
            <Plus size={17} />
            新建任务
          </button>
        )}
      </div>
      {notice && (
        <p role="status" className="taskcase-muted">
          {notice}
        </p>
      )}
      {listError && (
        <ErrorLine>
          {listError}
          <button onClick={() => setTick((n) => n + 1)}>重新连接</button>
        </ErrorLine>
      )}
      <div className="taskcase-layout">
        <aside className="taskcase-history" aria-label="任务列表">
          <div className="taskcase-row">
            <h2>
              任务记录 <small>{tasks.length}</small>
            </h2>
            <button
              className="icon"
              aria-label="刷新任务"
              onClick={() => setTick((n) => n + 1)}
            >
              <RefreshCw size={16} />
            </button>
          </div>
          {loading ? (
            <p role="status">正在读取任务…</p>
          ) : tasks.length ? (
            <ul>
              {tasks.map((t) => (
                <li key={t.id}>
                  <button
                    className={
                      !composing && selected === t.id ? "selected" : ""
                    }
                    aria-current={
                      !composing && selected === t.id ? "true" : undefined
                    }
                    onClick={() => {
                      setSelected(t.id);
                      setComposing(false);
                    }}
                  >
                    <strong title={taskCaseHeading(t)}>
                      {taskCaseHeading(t)}
                    </strong>
                    <State status={t.status} />
                    <small>{time(t.created_at)}</small>
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <p className="taskcase-muted">还没有任务。从右侧创建第一个任务。</p>
          )}
        </aside>
        <div className="taskcase-content">
          {composing || (!loading && !tasks.length && canSubmit) ? (
            <CreateTask
              projectId={projectId}
              onCreated={accept}
              onBack={tasks.length ? () => setComposing(false) : undefined}
            />
          ) : (
            <>
              {detailError && <ErrorLine>{detailError}</ErrorLine>}
              {detail && detail.id === selected ? (
                <TaskDetail
                  key={detail.id}
                  task={detail}
                  canSubmit={canSubmit}
                  onChange={accept}
                  onDeleted={discard}
                />
              ) : detailError ? null : loading || selected ? (
                <div className="taskcase-card" role="status">
                  正在读取任务详情…
                </div>
              ) : (
                <Empty
                  title="暂无可查看的任务"
                  description="任务执行后，结果与审核记录会显示在这里。"
                />
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function CreateTask({
  projectId,
  onCreated,
  onBack,
}: {
  projectId: string;
  onCreated: (task: TaskCase) => void;
  onBack?: () => void;
}) {
  const [goal, setGoal] = useState("");
  const [output, setOutput] = useState("");
  const [mode, setMode] = useState("cloud");
  const [budget, setBudget] = useState("2");
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const submission = useRef<{ fingerprint: string; key: string } | null>(null);
  async function submit(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setError("");
    const body = {
      project_id: projectId,
      goal: goal.trim(),
      output_requirements: output.trim(),
      mode,
      max_cost_cny: Number(budget),
      public_egress_authorized: mode === "cloud" && consent,
    };
    const fingerprint = JSON.stringify(body);
    if (submission.current?.fingerprint !== fingerprint)
      submission.current = { fingerprint, key: crypto.randomUUID() };
    try {
      let task: TaskCase = await post(base, {
        ...body,
        idempotency_key: submission.current.key,
      });
      // A task requiring no supplied data proceeds without an artificial upload step.
      if (taskCaseCanStart(task)) task = await post(path(task.id) + "/start");
      onCreated(task);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <form className="taskcase-card taskcase-create" onSubmit={submit}>
      {onBack && (
        <button type="button" className="text-button" onClick={onBack}>
          <ChevronLeft size={16} />
          返回任务
        </button>
      )}
      <div className="taskcase-card-heading">
        <span className="taskcase-symbol">
          <Sparkles size={23} />
        </span>
        <div>
          <h2>你希望完成什么工作？</h2>
        </div>
      </div>
      <label>
        任务目标
        <textarea
          required
          minLength={5}
          maxLength={12000}
          rows={6}
          value={goal}
          onChange={(e) => setGoal(e.target.value)}
          placeholder="例如：核查这份公开报告中的月度统计与总数是否一致，说明时间覆盖、遗漏和不确定性，并给出原文位置。"
        />
      </label>
      <label>
        交付要求 <small>可选</small>
        <textarea
          maxLength={4000}
          rows={2}
          value={output}
          onChange={(e) => setOutput(e.target.value)}
          placeholder="例如：中文结论、逐月明细表、复算过程和页码引用。"
        />
      </label>
      <details className="taskcase-settings">
        <summary>执行设置</summary>
        <div className="taskcase-two">
          <label>
            处理方式
            <select value={mode} onChange={(e) => setMode(e.target.value)}>
              <option value="cloud">云模型与本地工具</option>
              <option value="local">仅本地能力</option>
            </select>
          </label>
          <label>
            本任务累计费用上限（元）
            <input
              type="number"
              min="0"
              max="10"
              step="0.1"
              required
              value={budget}
              onChange={(e) => setBudget(e.target.value)}
            />
          </label>
        </div>
      </details>
      {mode === "cloud" && (
        <label className="taskcase-check taskcase-consent">
          <input
            type="checkbox"
            checked={consent}
            onChange={(e) => setConsent(e.target.checked)}
          />
          <span>
            允许平台通过统一网关，使用云模型处理本任务说明及我明确授权的公开资料。
          </span>
        </label>
      )}
      {error && <ErrorLine>{error}</ErrorLine>}
      <div className="taskcase-form-footer">
        <button className="primary" disabled={busy || !goal.trim()}>
          {busy ? (
            <LoaderCircle size={17} className="taskcase-spin" />
          ) : (
            <ArrowRight size={17} />
          )}
          {busy ? "正在分析任务…" : "创建任务"}
        </button>
      </div>
    </form>
  );
}

function TaskDetail({
  task,
  canSubmit,
  onChange,
  onDeleted,
}: {
  task: TaskCase;
  canSubmit: boolean;
  onChange: (task: TaskCase) => void;
  onDeleted: (id: string) => void;
}) {
  const [cancel, setCancel] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState("");
  const withheld = task.content_withheld || task.result?.content_withheld;
  const editable = canSubmit && taskCaseEditable.has(task.status);
  const resumable = taskCaseResumable.has(task.status);
  const resultFirst = task.status === "succeeded" && !!task.result;
  const heading = taskCaseHeading(task);
  const deletable =
    taskCaseEditable.has(task.status) || taskCaseTerminal.has(task.status);
  const inputRecord = (
    <>
      {task.requirements_analysis?.summary && (
        <section className="taskcase-card">
          <h2>平台对任务的理解</h2>
          <p>{task.requirements_analysis.summary}</p>
          {!!task.requirements_analysis.assumptions?.length && (
            <details>
              <summary>分析时采用的假设</summary>
              <ul>
                {task.requirements_analysis.assumptions.map((assumption, i) => (
                  <li key={i}>{assumption}</li>
                ))}
              </ul>
            </details>
          )}
        </section>
      )}
      {(editable ||
        !!task.requirements?.length ||
        !!task.profiles?.length ||
        !!task.submissions?.length) && (
        <Requirements task={task} editable={editable} onChange={onChange} />
      )}
      {resultFirst &&
        !editable &&
        !task.requirements_analysis?.summary &&
        !task.requirements?.length &&
        !task.profiles?.length &&
        !task.submissions?.length && (
          <p className="taskcase-muted">
            本任务没有需展示的资料需求或输入记录。
          </p>
        )}
    </>
  );
  return (
    <>
      <section className="taskcase-card taskcase-overview">
        <div className="taskcase-row">
          <State status={task.status} />
          <small>创建于 {time(task.created_at)}</small>
        </div>
        <h2 className="taskcase-goal">{heading}</h2>
        {heading !== task.goal && <p className="taskcase-goal">{task.goal}</p>}
        {task.output_requirements && (
          <p className="taskcase-muted">交付要求：{task.output_requirements}</p>
        )}
        <ol className="taskcase-progress" aria-label="任务进度">
          {(task.progress || []).map((step) => (
            <li
              key={step.key}
              className={"is-" + step.status}
              aria-current={step.status === "active" ? "step" : undefined}
            >
              <span>
                {step.status === "completed" ? (
                  <Check size={15} />
                ) : step.status === "active" ? (
                  <LoaderCircle size={15} className="taskcase-spin" />
                ) : (
                  <Circle size={12} />
                )}
              </span>
              {step.label}
            </li>
          ))}
        </ol>
        {taskCaseActive.has(task.status) && (
          <p className="taskcase-notice" role="status">
            {task.status === "analyzing_requirements"
              ? "平台正在分析任务目标与资料需求。若无需补充资料，将自动进入执行；关闭页面后任务仍会继续。"
              : "平台正在自主执行，关闭页面后任务仍会继续。审核发现问题时会在允许的次数与费用内调整处理流程。"}
          </p>
        )}
        {error && <ErrorLine>{error}</ErrorLine>}
        {canSubmit && (
          <div className="taskcase-actions">
            {!taskCaseTerminal.has(task.status) && (
              <button
                className="text-button taskcase-cancel"
                onClick={() => setCancel(true)}
              >
                取消任务
              </button>
            )}
            <button
              className="text-button taskcase-delete"
              disabled={!deletable}
              title={
                deletable
                  ? undefined
                  : "请先取消任务，等待执行停止后再删除记录。"
              }
              onClick={() => setDeleting(true)}
            >
              <Trash2 size={16} />
              删除任务记录
            </button>
          </div>
        )}
        {canSubmit && resumable && (
          <p className="taskcase-notice">
            本轮记录已保留。你可以补充资料后继续同一任务，也可以沿用已有资料再次执行。新一轮与历史运行共享本任务累计费用上限，不会重置已发生费用。
          </p>
        )}
      </section>
      {withheld ? (
        <div className="taskcase-card">
          <ErrorLine>
            资料授权已失效，派生结果已隐藏。请核对资料的访问范围或有效期。
          </ErrorLine>
        </div>
      ) : (
        <>
          {resultFirst ? (
            <>
              <Result task={task} />
              <details className="taskcase-card taskcase-input-record">
                <summary>资料需求与输入记录</summary>
                <div className="taskcase-input-record-body">{inputRecord}</div>
              </details>
            </>
          ) : (
            <>
              {inputRecord}
              {task.result && <Result task={task} />}
            </>
          )}
          {taskCaseTerminal.has(task.status) && !task.result && (
            <div className="taskcase-card">
              <h3>任务已结束</h3>
              <p>
                {task.status === "cancelled"
                  ? "任务已取消，历史执行与已发生费用仍然保留。"
                  : "未生成可交付结果。下方记录保留了实际执行情况。"}
              </p>
              {task.reason_code && <p>原因：{task.reason_code}</p>}
              {!!task.missing?.length && (
                <ul>
                  {task.missing.map((m, i) => (
                    <li key={i}>{m}</li>
                  ))}
                </ul>
              )}
            </div>
          )}
          <Execution task={task} />
        </>
      )}
      {cancel && (
        <Confirm
          title="取消这项任务"
          description="平台会停止后续执行，已经发生的模型费用与执行记录会保留。"
          onClose={() => setCancel(false)}
          onConfirm={async () => {
            try {
              onChange(await post(path(task.id) + "/cancel"));
              setCancel(false);
            } catch (e) {
              setError((e as Error).message);
              setCancel(false);
            }
          }}
        />
      )}
      {deleting && (
        <Confirm
          title="删除这项任务记录"
          description="删除后，本任务将从任务记录中移除，上游提交入口也会失效。已上传资料、执行与审核记录及历史费用仍保留。"
          danger
          confirm="删除记录"
          onClose={() => setDeleting(false)}
          onConfirm={async () => {
            try {
              await remove(path(task.id));
            } catch (error) {
              if (
                error instanceof ApiError &&
                error.code === "task_case_not_terminal"
              )
                throw new Error(
                  "任务仍在运行，请先取消任务并等待执行停止后再删除。",
                );
              if (
                error instanceof ApiError &&
                error.code === "task_case_cleanup_pending"
              )
                throw new Error("任务正在完成执行清理，请稍后再删除记录。");
              throw error;
            }
            onDeleted(task.id);
          }}
        />
      )}
    </>
  );
}

function Requirements({
  task,
  editable,
  onChange,
}: {
  task: TaskCase;
  editable: boolean;
  onChange: (task: TaskCase) => void;
}) {
  const [selected, setSelected] = useState<string[]>([]);
  const [files, setFiles] = useState<File[]>([]);
  const [isPublic, setIsPublic] = useState(false);
  const [consent, setConsent] = useState(task.public_egress_authorized);
  const [busy, setBusy] = useState(false);
  const [stage, setStage] = useState("");
  const [error, setError] = useState("");
  const [requirementId, setRequirementId] = useState("");
  const input = useRef<HTMLInputElement>(null);
  const savedUploads = useRef(new Map<File, string>());
  const pendingSubmission = useRef<{ fingerprint: string; key: string } | null>(
    null,
  );
  const assets = task.available_assets || [];
  const submitted = new Set(
    (task.submissions || []).flatMap((s) => s.input_refs),
  );
  const requirements = task.requirements || [];
  const resumable = taskCaseResumable.has(task.status);
  const blockedLocal =
    task.mode === "cloud" &&
    ((files.length > 0 && !isPublic) ||
      selected.some(
        (id) => assets.find((a) => a.id === id)?.classification !== "public",
      ));

  async function deliver() {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      let latest = task;
      for (let i = 0; i < files.length; i++) {
        const file = files[i];
        setStage(`正在上传 ${i + 1}/${files.length}：${file.name}`);
        let key = savedUploads.current.get(file);
        if (!key) {
          key = crypto.randomUUID();
          savedUploads.current.set(file, key);
        }
        const data = new FormData();
        data.set("file", file);
        data.set("classification", isPublic ? "public" : "local");
        data.set("idempotency_key", key);
        if (requirementId) data.set("requirement_id", requirementId);
        latest = await api<TaskCase>(path(task.id) + "/upload", {
          method: "POST",
          body: data,
        });
      }
      setStage("正在校验资料与授权…");
      const body = {
        input_refs: selected,
        requirement_ids: requirementId ? [requirementId] : [],
        public_egress_authorized: consent,
      };
      // A continuation is a new intent even if it reuses the same inputs. Retain
      // its key for network retries, but never replay a prior run's receipt.
      const fingerprint = JSON.stringify({
        ...body,
        continuation: resumable
          ? `${task.status}:${task.task_id || task.updated_at}`
          : task.task_id || "intake",
      });
      if (pendingSubmission.current?.fingerprint !== fingerprint)
        pendingSubmission.current = { fingerprint, key: crypto.randomUUID() };
      latest = await post(path(task.id) + "/submissions", {
        ...body,
        idempotency_key: pendingSubmission.current.key,
      });
      setFiles([]);
      setSelected([]);
      if (input.current) input.current.value = "";
      if (taskCaseCanStart(latest)) {
        setStage("正在生成执行计划…");
        latest = await post(path(task.id) + "/start");
      }
      onChange(latest);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
      setStage("");
    }
  }
  return (
    <section className="taskcase-card">
      <div className="taskcase-card-heading">
        <span className="taskcase-symbol">
          <FileText size={21} />
        </span>
        <div>
          <h2>{resumable ? "补充资料并继续" : "这项任务需要的资料"}</h2>
          <p>
            {resumable
              ? "已提交的资料继续保留。补充新的依据或重新执行后，平台会创建新的运行记录，原有审核与费用仍然可追溯。"
              : requirements.length
                ? "平台根据任务目标整理了以下需求。可上传原文件，也可使用项目中已有的授权资料。"
                : "平台判断这项任务无需上传参考资料，可以直接执行。"}
          </p>
        </div>
      </div>
      {!!requirements.length && (
        <ul className="taskcase-requirements">
          {requirements.map((r) => (
            <li key={r.id}>
              <div className="taskcase-row">
                <strong>{r.title}</strong>
                <span className={"taskcase-requirement-status is-" + r.status}>
                  {r.status === "satisfied"
                    ? "已满足"
                    : r.status === "provided"
                      ? "资料已读取"
                      : r.status === "available"
                        ? "有可选资料"
                        : r.required
                          ? "待提供"
                          : "可选"}
                </span>
              </div>
              <p>{r.description}</p>
              {(r.time_scope || r.spatial_scope || r.unit_scope) && (
                <p className="taskcase-muted">
                  {[
                    r.time_scope && `时间范围：${r.time_scope}`,
                    r.spatial_scope && `空间范围：${r.spatial_scope}`,
                    r.unit_scope && `单位与口径：${r.unit_scope}`,
                  ]
                    .filter(Boolean)
                    .join("；")}
                </p>
              )}
              {!!r.fields?.length && (
                <p>
                  <span className="taskcase-muted">需要包含：</span>
                  {r.fields.join("、")}
                </p>
              )}
              {!!r.formats?.length && (
                <div className="taskcase-tags">
                  {r.formats.map((f) => (
                    <span key={f}>{f}</span>
                  ))}
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
      {!!task.missing?.length && (
        <div className="taskcase-notice">
          <strong>尚需补充</strong>
          <ul>
            {task.missing.map((m, i) => (
              <li key={i}>{m}</li>
            ))}
          </ul>
        </div>
      )}
      {!!task.profiles?.length && (
        <details className="taskcase-data-profiles">
          <summary>已读取资料的结构 · {task.profiles.length} 项</summary>
          <p className="taskcase-muted">
            这里只确认文件可读与结构信息，内容是否足以完成任务由后续分析和审核判断。
          </p>
          <ul>
            {task.profiles.map((profile) => (
              <li key={profile.asset_id}>
                <strong>{profile.name}</strong>
                <span>
                  {profile.readable === false
                    ? "无法读取"
                    : profile.status === "ready" || profile.readable === true
                      ? "已读取"
                      : "状态待核验"}
                  {profile.record_count != null
                    ? ` · ${profile.record_count} 条记录`
                    : ""}
                  {profile.page_count != null
                    ? ` · ${profile.page_count} 页`
                    : ""}
                </span>
                {!!profile.fields?.length && (
                  <small>字段：{profile.fields.join("、")}</small>
                )}
                {!!profile.issues?.length && <p>{profile.issues.join("；")}</p>}
              </li>
            ))}
          </ul>
        </details>
      )}
      {editable && (
        <>
          {!!assets.length && (
            <fieldset className="taskcase-asset-list">
              <legend>选择已有资料</legend>
              {assets.map((asset) => (
                <label key={asset.id} className="taskcase-check">
                  <input
                    type="checkbox"
                    checked={
                      submitted.has(asset.id) || selected.includes(asset.id)
                    }
                    disabled={busy || submitted.has(asset.id)}
                    onChange={(e) =>
                      setSelected((ids) =>
                        e.target.checked
                          ? [...ids, asset.id]
                          : ids.filter((id) => id !== asset.id),
                      )
                    }
                  />
                  <span>
                    <strong>{asset.name}</strong>
                    <small>
                      {bytesLabel(asset.bytes)}
                      {asset.bytes !== undefined ? " · " : ""}
                      {asset.classification === "public"
                        ? "公开资料"
                        : "仅本地"}
                      {submitted.has(asset.id) ? " · 已提交" : ""}
                    </small>
                  </span>
                </label>
              ))}
            </fieldset>
          )}
          <div className="taskcase-upload">
            <label className="taskcase-upload-trigger">
              <Upload size={23} />
              <strong>上传任务资料</strong>
              <span>保留原始格式；具体所需内容见上方需求。</span>
              <input
                ref={input}
                aria-label="上传任务资料"
                type="file"
                multiple
                disabled={busy}
                onChange={(e) => setFiles(Array.from(e.target.files || []))}
              />
            </label>
            {!!files.length && (
              <>
                <ul className="taskcase-files">
                  {files.map((file, i) => (
                    <li key={`${file.name}-${i}`}>
                      <span>
                        {file.name}
                        <small>{bytesLabel(file.size)}</small>
                      </span>
                      <button
                        type="button"
                        aria-label={`移除 ${file.name}`}
                        disabled={busy}
                        onClick={() =>
                          setFiles((old) => old.filter((_, j) => i !== j))
                        }
                      >
                        <X size={15} />
                      </button>
                    </li>
                  ))}
                </ul>
                {requirements.length > 1 && (
                  <label>
                    对应资料需求
                    <select
                      value={requirementId}
                      onChange={(e) => setRequirementId(e.target.value)}
                    >
                      <option value="">由平台根据资料判断</option>
                      {requirements.map((r) => (
                        <option key={r.id} value={r.id}>
                          {r.title}
                        </option>
                      ))}
                    </select>
                  </label>
                )}
                <label className="taskcase-check">
                  <input
                    type="checkbox"
                    checked={isPublic}
                    disabled={busy}
                    onChange={(e) => setIsPublic(e.target.checked)}
                  />
                  <span>
                    这些文件是允许用于本任务的公开资料
                    <small>
                      未勾选的文件仅限本地处理；上传不等于授权云端使用。
                    </small>
                  </span>
                </label>
              </>
            )}
          </div>
          {task.mode === "cloud" && (
            <label className="taskcase-check taskcase-consent">
              <input
                type="checkbox"
                checked={consent}
                disabled={busy}
                onChange={(e) => setConsent(e.target.checked)}
              />
              <span>
                授权本任务通过统一网关使用云模型处理任务说明及所选公开资料
              </span>
            </label>
          )}
          {blockedLocal && (
            <p className="taskcase-notice">
              本任务使用云模型。请选择已授权的公开资料，或创建仅本地处理的任务。
            </p>
          )}
          {error && <ErrorLine>{error}</ErrorLine>}
          <div className="taskcase-form-footer">
            {stage && <span role="status">{stage}</span>}
            <button
              className="primary"
              disabled={
                busy || blockedLocal || (task.mode === "cloud" && !consent)
              }
              onClick={() => void deliver()}
            >
              {busy ? (
                <LoaderCircle size={16} className="taskcase-spin" />
              ) : (
                <ArrowRight size={16} />
              )}
              {resumable
                ? files.length || selected.length
                  ? "补充资料并继续此任务"
                  : "沿用已提交资料继续"
                : task.status === "ready" && !files.length && !selected.length
                  ? "开始分析"
                  : "提交资料并开始分析"}
            </button>
          </div>
          <DataApi task={task} />
        </>
      )}
      {!editable && !!task.submissions?.length && (
        <p className="taskcase-muted">
          已保存 {task.submissions.length}{" "}
          次资料提交，执行使用其冻结的来源版本。
        </p>
      )}
    </section>
  );
}

function DataApi({ task }: { task: TaskCase }) {
  const [credential, setCredential] = useState<{
    id: string;
    token: string;
    expires_at: string;
    endpoint: string;
  } | null>(null);
  const [hours, setHours] = useState("24");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);
  async function generate() {
    setBusy(true);
    setError("");
    try {
      setCredential(
        await post(path(task.id) + "/ingest-tokens", {
          expires_hours: Number(hours),
        }),
      );
      setCopied(false);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function revoke() {
    if (!credential) return;
    setBusy(true);
    setError("");
    try {
      await api(
        path(task.id) + "/ingest-tokens/" + encodeURIComponent(credential.id),
        { method: "DELETE" },
      );
      setCredential(null);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <details className="taskcase-api-help">
      <summary>上游提交资料</summary>
      <p>
        接入凭据仅能向本任务提交资料，不能读取任务结果或其他项目内容。资料仍须经过同一套权限、来源和格式校验。
      </p>
      <p>文本或 JSON 接口：</p>
      <code>POST /api/v2/task-cases/{task.id}/ingest</code>
      <p>文件接口（multipart）：</p>
      <code>POST /api/v2/task-cases/{task.id}/ingest/upload</code>
      <p>
        请求头：<code>Authorization: Bearer &lt;接入凭据&gt;</code>
      </p>
      {!credential ? (
        <div className="taskcase-api-actions">
          <label>
            凭据有效期（小时）
            <input
              type="number"
              min="1"
              max="168"
              step="1"
              value={hours}
              onChange={(e) => setHours(e.target.value)}
            />
          </label>
          <button
            disabled={
              busy ||
              !Number.isInteger(Number(hours)) ||
              Number(hours) < 1 ||
              Number(hours) > 168
            }
            onClick={() => void generate()}
          >
            生成本任务接入凭据
          </button>
        </div>
      ) : (
        <div className="taskcase-api-credential">
          <label>
            接入凭据
            <input
              type="password"
              readOnly
              value={credential.token}
              autoComplete="off"
            />
          </label>
          <p>
            有效至 {time(credential.expires_at)}
            。凭据仅在本次页面中可见，请私下交给接入系统，勿放入任务说明或资料内容。
          </p>
          <div className="taskcase-api-actions">
            <button
              onClick={async () => {
                try {
                  await navigator.clipboard.writeText(credential.token);
                  setCopied(true);
                } catch {
                  setError("浏览器未允许复制，请选中凭据手动复制。");
                }
              }}
            >
              {copied ? "已复制" : "复制凭据"}
            </button>
            <button disabled={busy} onClick={() => void revoke()}>
              撤销此凭据
            </button>
          </div>
        </div>
      )}
      {error && <ErrorLine>{error}</ErrorLine>}
      <details>
        <summary>查看 JSON 提交示例</summary>
        <pre>
          {JSON.stringify(
            {
              name: "月度统计.csv",
              media_type: "text/csv",
              content: "month,value\n2023-01,10",
              classification: "public",
              source_id: "customer-monthly-statistics",
              source_version: "1",
              provenance: "甲方提供的公开样例",
              idempotency_key: "replace-with-unique-submission-key",
            },
            null,
            2,
          )}
        </pre>
      </details>
    </details>
  );
}

function Result({ task }: { task: TaskCase }) {
  const result = task.result!;
  // Legacy API projections include every successful sandbox exit as an artifact.
  // Only generated_files identifies outputs actually referenced by this delivery.
  const reportArtifacts = (result.artifacts || []).filter(
    (artifact) =>
      artifact.type !== "script_output" &&
      artifact.quality_status !== "execution_output",
  );
  return (
    <section className="taskcase-card taskcase-result">
      <div className="taskcase-row">
        <h2>交付结果</h2>
        <a
          className="taskcase-download"
          href={`/api${path(task.id)}/result/download`}
        >
          <Download size={16} />
          下载结果记录
        </a>
      </div>
      {task.status === "partial" && (
        <p className="taskcase-notice">
          这是一份部分交付，以下缺口尚未解决。请结合证据和适用范围使用。
        </p>
      )}
      {!!result.answer && (
        <div className="taskcase-answer">{result.answer}</div>
      )}
      {!!result.generated_files?.length && (
        <div className="taskcase-generated-files">
          <h3>{task.status === "partial" ? "部分交付文件" : "交付文件"}</h3>
          <p className="taskcase-muted">
            这些文件由本轮最终结果引用。文件生成完成与逐项内容审核分别记录，请结合任务审核和结果限制使用。
          </p>
          <ul>
            {result.generated_files.map((file, index) => (
              <li key={`${file.checksum || file.name}:${index}`}>
                <div>
                  <strong>{file.name}</strong>
                  <small>
                    {[bytesLabel(file.bytes), file.media_type]
                      .filter(Boolean)
                      .join(" · ")}
                  </small>
                  <small>
                    {file.quality_status === "referenced_output"
                      ? "最终结果引用的脚本产物"
                      : file.quality_status === "execution_output"
                        ? "执行产物，尚未确认内容质量"
                        : file.quality_status
                          ? `产物状态：${file.quality_status}`
                          : "逐文件审核状态未单列"}
                    {task.status === "partial" ? " · 部分交付" : ""}
                  </small>
                  {file.checksum && (
                    <details>
                      <summary>文件校验值</summary>
                      <code>{file.checksum}</code>
                    </details>
                  )}
                </div>
                {file.download_url?.startsWith(`/api${path(task.id)}/`) &&
                !/[\r\n\\]/.test(file.download_url) ? (
                  <a
                    className="taskcase-download"
                    href={file.download_url}
                    download={file.name}
                  >
                    <Download size={16} />
                    下载 {file.name}
                  </a>
                ) : (
                  <span className="taskcase-muted">下载暂不可用</span>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
      <MonthlyResultChart tables={result.tables} />
      <QuestionProducts tables={result.tables} maps={result.maps} />
      {!!result.warnings?.length && (
        <div className="taskcase-notice">
          <strong>结果限制</strong>
          <ul>
            {result.warnings.map((w, i) => (
              <li key={i}>{w}</li>
            ))}
          </ul>
        </div>
      )}
      {!!result.missing?.length && (
        <div className="taskcase-notice">
          <strong>未解决的缺口</strong>
          <ul>
            {result.missing.map((w, i) => (
              <li key={i}>{w}</li>
            ))}
          </ul>
        </div>
      )}
      {!!result.citations?.length && (
        <details className="taskcase-citations">
          <summary>查看原文证据 · {result.citations.length} 条</summary>
          <ol>
            {result.citations.map((c, i) => (
              <li key={c.id || i}>
                <strong>
                  {c.id ? `[${c.id}] ` : ""}
                  {c.source}
                </strong>
                <small>{c.location}</small>
                {citationSourceUrl(c) && (
                  <a
                    className="taskcase-source-link"
                    href={citationSourceUrl(c)!}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    查看原件{c.page ? ` · 第 ${c.page} 页` : ""}
                  </a>
                )}
                <blockquote>{c.excerpt}</blockquote>
                {c.quote_truncated && (
                  <p className="taskcase-muted">
                    摘录已截断，请查看原件上下文。
                  </p>
                )}
              </li>
            ))}
          </ol>
        </details>
      )}
      {!!reportArtifacts.length && (
        <div className="taskcase-artifacts">
          {reportArtifacts.map((a, i) =>
            a.download_url ? (
              <a key={a.id || i} href={a.download_url}>
                <Download size={15} />
                {a.name ||
                  (
                    {
                      case_report: "完整分析报告",
                      script_output: "脚本执行产物",
                      text: "分析文本",
                    } as Record<string, string>
                  )[a.type] ||
                  a.type ||
                  "产物"}
              </a>
            ) : null,
          )}
        </div>
      )}
    </section>
  );
}

function Execution({ task }: { task: TaskCase }) {
  const trace = task.trace || {};
  const plans = task.plans || [];
  const agents: Record<string, any>[] = trace.agents || [];
  const models: Record<string, any>[] = trace.models || [];
  const tools: Record<string, any>[] = trace.tools || [];
  const revisions: Record<string, any>[] = trace.revisions || [];
  const assessmentReviews: Record<string, any>[] =
    trace.assessment_reviews || [];
  const review = task.review;
  const checks: Record<string, any>[] = review?.checks || [];
  const issues: Record<string, any>[] = review?.issues || [];
  return (
    <section className="taskcase-card taskcase-execution">
      <details open={taskCaseActive.has(task.status) || undefined}>
        <summary>
          <ClipboardList size={19} />
          <span>规划、执行与审核记录</span>
        </summary>
        <p className="taskcase-muted">
          以下来自实际保存的记录。节点执行完成与结果通过审核分别展示。
        </p>
        {!plans.length && !agents.length && !review && (
          <p>资料就绪后，平台会生成执行计划；实际调用记录将在运行后出现。</p>
        )}
        {!!task.runs?.length && (
          <div className="taskcase-execution-section">
            <h3>实际执行阶段</h3>
            <ol className="taskcase-run-list">
              {task.runs.map((run, index) => (
                <li key={run.id}>
                  <div className="taskcase-row">
                    <strong>
                      {run.phase === "requirements" ||
                      run.id === task.requirements_analysis?.task_id
                        ? "资料需求分析"
                        : `任务处理 ${index + 1}`}
                    </strong>
                    <State status={run.status} />
                  </div>
                  <small>
                    开始：{time(run.created_at)}
                    {run.finished_at ? ` · 结束：${time(run.finished_at)}` : ""}
                  </small>
                  {run.reason_code && <p>{run.reason_code}</p>}
                </li>
              ))}
            </ol>
          </div>
        )}
        <ExecutionPlanHistory plans={plans} />
        {!!agents.length && (
          <div className="taskcase-execution-section">
            <h3>实际参与的智能体</h3>
            <div className="taskcase-agent-grid">
              {agents.map((a, i) => (
                <div
                  key={`${a.task_id || ""}:${a.id || i}:${a.version || ""}:${i}`}
                >
                  <strong>{a.name || a.id}</strong>
                  <p>
                    版本 {a.version || "—"} ·{" "}
                    {a.scope === "task"
                      ? "本任务创建"
                      : a.scope === "project" || a.scope === "reusable"
                        ? "复用资产"
                        : textValue(a.scope)}
                  </p>
                  <small>
                    {a.instances != null
                      ? `执行实例：${Array.isArray(a.instances) ? a.instances.length : a.instances}`
                      : ""}
                    {a.status
                      ? ` · ${statusNames[a.status] || textValue(a.status)}`
                      : ""}
                  </small>
                </div>
              ))}
            </div>
          </div>
        )}
        {!!models.length && (
          <div className="taskcase-execution-section">
            <h3>实际模型与费用</h3>
            <div className="taskcase-table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>模型</th>
                    <th>请求次数</th>
                    <th>已记录费用（元）</th>
                  </tr>
                </thead>
                <tbody>
                  {models.map((m, i) => (
                    <tr key={m.model || i}>
                      <td>{m.model}</td>
                      <td>{m.requests ?? "—"}</td>
                      <td>
                        {typeof m.cost_cny === "number"
                          ? m.cost_cny.toFixed(6)
                          : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="taskcase-muted">
              审核与修订共享本任务 ¥{task.max_cost_cny.toFixed(2)}{" "}
              上限。费用以网关账本为准。
            </p>
            {task.usage &&
              (task.usage.accounting_complete === false ||
                (task.usage.reserved_cny || 0) > 0) && (
                <p className="taskcase-notice">
                  费用尚未全部确认。当前保留预算 ¥
                  {typeof task.usage.reserved_cny === "number"
                    ? task.usage.reserved_cny.toFixed(6)
                    : "待核验"}
                  ；未决请求 {task.usage.uncertain_count ?? "待核验"} 次。
                </p>
              )}
          </div>
        )}
        {!!tools.length && (
          <div className="taskcase-execution-section">
            <h3>实际工具调用 · {tools.length} 次</h3>
            <div className="taskcase-table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>工具</th>
                    <th>轮次</th>
                    <th>状态</th>
                    <th>耗时</th>
                  </tr>
                </thead>
                <tbody>
                  {tools.map((t, i) => (
                    <tr key={t.id || i}>
                      <td>
                        <code>{t.capability_id}</code>
                      </td>
                      <td>{(t.revision || 0) + 1}</td>
                      <td>
                        {t.status === "completed"
                          ? "执行完成"
                          : t.status === "failed"
                            ? "执行失败"
                            : t.status}
                      </td>
                      <td>
                        {t.duration_ms == null ? "—" : `${t.duration_ms} ms`}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
        <ScriptExecution scripts={trace.scripts} runs={trace.sandbox_runs} />
        {!!revisions.length && (
          <div className="taskcase-execution-section">
            <h3>审核不通过后的针对性调整</h3>
            {revisions.map((r, i) =>
              r.action === "citation_span_realign" ||
              r.outcome?.scope === "source_span_only" ? (
                <CitationBoundaryRepair repair={r} key={r.id || i} />
              ) : (
                <article className="taskcase-repair" key={r.id || i}>
                  <h4>
                    第 {r.revision ?? i + 1} 次修订 · {repairLabel(r)}
                  </h4>
                  {r.reason && <p>{textValue(r.reason)}</p>}
                  {!!r.failed_checks?.length && (
                    <p>
                      <strong>发现的问题：</strong>
                      {r.failed_checks
                        .map((name: string) => taskCheckLabels[name] || name)
                        .join("、")}
                    </p>
                  )}
                  {(r.targets || r.target_source_ids) && (
                    <p>
                      <strong>修复对象：</strong>
                      {Array.isArray(r.targets || r.target_source_ids)
                        ? (r.targets || r.target_source_ids)
                            .map(
                              (id: string) =>
                                task.available_assets?.find(
                                  (asset) => asset.id === id,
                                )?.name ||
                                task.result?.citations?.find(
                                  (citation) => citation.source_id === id,
                                )?.source ||
                                id,
                            )
                            .join("、")
                        : textValue(r.targets || r.target_source_ids)}
                    </p>
                  )}
                  {(r.rerun_stages || r.rerun_nodes) && (
                    <p>
                      <strong>重新执行：</strong>
                      {stageLabels(r.rerun_stages || r.rerun_nodes)}
                    </p>
                  )}
                  {(r.reused_nodes || r.reused_stages) && (
                    <p>
                      <strong>复用已验证步骤：</strong>
                      {stageLabels(r.reused_nodes || r.reused_stages)}
                    </p>
                  )}
                  {!!repairMeasurements(r).length && (
                    <div className="taskcase-table-scroll">
                      <table>
                        <thead>
                          <tr>
                            <th>核查项</th>
                            <th>修订前</th>
                            <th>修订后</th>
                          </tr>
                        </thead>
                        <tbody>
                          {repairMeasurements(r).map((metric) => (
                            <tr key={metric.key}>
                              <td>{metric.label}</td>
                              <td>{metric.before ?? "—"}</td>
                              <td>{metric.after ?? "等待复核"}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                  {(r.before || r.after || r.outcome) && (
                    <details>
                      <summary>查看修订前后核查记录</summary>
                      <pre>
                        {JSON.stringify(
                          {
                            before: r.before,
                            after: r.after,
                            outcome: r.outcome,
                          },
                          null,
                          2,
                        )}
                      </pre>
                    </details>
                  )}
                </article>
              ),
            )}
          </div>
        )}
        {review && (
          <div className="taskcase-execution-section">
            <h3>
              <ShieldCheck size={18} /> 自动审核
            </h3>
            <p
              className={
                review.passed ? "taskcase-review-pass" : "taskcase-review-fail"
              }
            >
              {review.passed === true
                ? "结果通过自动审核"
                : review.passed === false
                  ? "结果尚未通过审核"
                  : "审核记录"}
              {review.verdict &&
                !["passed", "failed"].includes(review.verdict) &&
                ` · ${statusNames[review.verdict] || textValue(review.verdict)}`}
            </p>
            {!!checks.length && (
              <ul className="taskcase-checks">
                {checks.map((c, i) => (
                  <li key={i}>
                    <span className={c.passed ? "pass" : "fail"}>
                      {c.passed ? "通过" : "未通过"}
                    </span>
                    {c.label ||
                      taskCheckLabels[c.name || c.code] ||
                      c.name ||
                      c.code}
                    {c.message && `：${c.message}`}
                  </li>
                ))}
              </ul>
            )}
            {!!issues.length && (
              <ul>
                {issues.map((issue, i) => (
                  <li key={i}>{issue.message || issue.code}</li>
                ))}
              </ul>
            )}
            {review.model_review && (
              <details>
                <summary>模型独立复核说明</summary>
                <p>
                  模型复核与规则校验分别记录；模型意见本身不替代来源与数值检查。
                </p>
                <pre>
                  {typeof review.model_review === "string"
                    ? review.model_review
                    : JSON.stringify(review.model_review, null, 2)}
                </pre>
              </details>
            )}
          </div>
        )}
        {!!assessmentReviews.length && (
          <div className="taskcase-execution-section">
            <h3>文字与月度数据一致性核验</h3>
            <p className="taskcase-muted">
              此项检查针对表述中明确写出的月份数量。它不代表对所有文字含义的完整审核。
            </p>
            {assessmentReviews.map((r, i) => (
              <details
                className="taskcase-assessment"
                key={`${r.task_id || ""}:${r.node_id || ""}:${r.revision || 0}:${i}`}
                open={r.status === "corrected"}
              >
                <summary>
                  {r.agent_name || r.agent_id || "智能体"} · 第{" "}
                  {(r.revision || 0) + 1} 轮 ·{" "}
                  {r.status === "corrected"
                    ? "发现矛盾并修正"
                    : r.checks?.length
                      ? "已核对显式月份表述"
                      : "未发现可核验的显式月份表述"}
                </summary>
                {r.facts && (
                  <dl className="taskcase-month-facts">
                    {Object.entries({
                      monthly_records: "月度记录",
                      unique_months: "覆盖月份",
                      complete_months: "完整月",
                      partial_months: "部分月",
                      unclassified_months: "未分类记录",
                    }).map(([key, label]) =>
                      r.facts[key] != null ? (
                        <div key={key}>
                          <dt>{label}</dt>
                          <dd>{r.facts[key]}</dd>
                        </div>
                      ) : null,
                    )}
                  </dl>
                )}
                {r.status === "corrected" && (
                  <>
                    <h4>原始表述（未采用）</h4>
                    <blockquote className="taskcase-rejected-prose">
                      {r.original_assessment}
                    </blockquote>
                    <h4>核验后的表述</h4>
                    <p>{r.assessment}</p>
                  </>
                )}
                {r.status !== "corrected" && r.assessment && (
                  <p>{r.assessment}</p>
                )}
                {!!r.checks?.length && (
                  <ul className="taskcase-checks">
                    {r.checks.map((check: Record<string, any>, j: number) => (
                      <li key={j}>
                        <span className={check.passed ? "pass" : "fail"}>
                          {check.passed ? "一致" : "矛盾"}
                        </span>
                        <span>
                          {check.claim || check.name}：原表述{" "}
                          {textValue(check.observed)}，工具核验值{" "}
                          {textValue(check.expected)}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
                {r.status === "corrected" && !!r.post_checks?.length && (
                  <p
                    className={
                      r.post_checks.every(
                        (check: Record<string, any>) => check.passed === true,
                      )
                        ? "taskcase-review-pass"
                        : "taskcase-review-fail"
                    }
                  >
                    {r.post_checks.every(
                      (check: Record<string, any>) => check.passed === true,
                    )
                      ? "修正后的显式月份表述复核通过。"
                      : "修正后的表述仍存在未通过的检查。"}
                  </p>
                )}
              </details>
            ))}
          </div>
        )}
        <p className="taskcase-id">
          任务标识：{task.id}
          {task.task_id ? ` · 执行标识：${task.task_id}` : ""}
        </p>
      </details>
    </section>
  );
}
