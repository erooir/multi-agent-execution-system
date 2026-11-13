import { useEffect, useState } from "react";
import { FlaskConical, RefreshCw } from "lucide-react";
import { api, post } from "./api";
import { time } from "./domain";
import { Empty, ErrorLine, Panel } from "./ui";
import type { Item } from "./types";
import { taskCaseStatus } from "./taskCases";

const labels: Record<string, string> = {
  candidate: "待独立评测",
  evaluating: "正在重放评测",
  evaluated: "评测已完成",
  promoted: "已入复用库",
};
const gates: Record<string, string> = {
  independent_development_and_holdout: "包含独立开发样本与留出样本",
  all_candidate_quality_passed: "候选全部产物通过审核",
  all_charges_settled: "所有模型费用已结算",
  bounded_cost_regression: "费用增长在允许范围内",
  measured_quality_or_cost_gain: "存在可测的质量或费用改善",
  independent_samples: "独立开发与留出样本",
  all_passed: "全部产物通过审核",
  no_hard_regression: "无硬性质量回归",
  accounting_complete: "模型费用已结算",
  cost_not_increased: "费用未增加",
};
const cny = (v: unknown) => (typeof v === "number" ? `¥${v.toFixed(6)}` : "—");

export default function TaskAgentCandidates({
  projectId,
  canEdit,
  isAdmin,
  onTask,
}: {
  projectId: string;
  canEdit: boolean;
  isAdmin: boolean;
  onTask: (id: string) => void;
}) {
  const [candidates, setCandidates] = useState<Item[]>([]);
  const [cases, setCases] = useState<Item[]>([]);
  const [selected, setSelected] = useState("");
  const [detail, setDetail] = useState<Item | null>(null);
  const [source, setSource] = useState("");
  const [samples, setSamples] = useState<string[]>([]);
  const [budget, setBudget] = useState("4");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function load() {
      try {
        const [rows, tasks] = await Promise.all([
          api<Item[]>(
            `/v2/agent-candidates?project_id=${encodeURIComponent(projectId)}`,
            { signal: controller.signal },
          ),
          api<Item[]>(
            `/v2/task-cases?project_id=${encodeURIComponent(projectId)}`,
            { signal: controller.signal },
          ),
        ]);
        if (controller.signal.aborted) return;
        setCandidates(rows);
        setCases(tasks);
        setError("");
        setSelected((old) =>
          rows.some((r) => r.id === old) ? old : rows[0]?.id || "",
        );
      } catch (e) {
        if (!controller.signal.aborted) {
          setError((e as Error).message);
          setCandidates([]);
          setCases([]);
          setDetail(null);
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
  }, [projectId, tick]);
  useEffect(() => {
    if (!selected) {
      setDetail(null);
      return;
    }
    const controller = new AbortController();
    setDetail((old) => (old?.id === selected ? old : null));
    setSamples([]);
    let timer: ReturnType<typeof setTimeout>;
    async function load() {
      try {
        const row = await api<Item>(
          `/v2/agent-candidates/${encodeURIComponent(selected)}`,
          { signal: controller.signal },
        );
        if (!controller.signal.aborted) setDetail(row);
      } catch (e) {
        if (!controller.signal.aborted) {
          setError((e as Error).message);
          setDetail(null);
        }
      } finally {
        if (!controller.signal.aborted) timer = setTimeout(load, 5000);
      }
    }
    void load();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [selected, tick]);
  async function action(kind: "extract" | "evaluate" | "promote") {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      const row =
        kind === "extract"
          ? await post(
              `/v2/task-cases/${encodeURIComponent(source)}/candidates`,
            )
          : await post(
              `/v2/agent-candidates/${encodeURIComponent(selected)}/${kind}`,
              kind === "evaluate"
                ? { task_case_ids: samples, max_cost_cny: Number(budget) }
                : {},
            );
      if (row?.id) setSelected(row.id);
      setTick((n) => n + 1);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const eligible = cases.filter((c) => c.candidate_eligible === true);
  const evaluationCases = cases.filter(
    (c) =>
      c.evaluation_eligible === true && c.id !== detail?.source_task_case_id,
  );
  const evaluation = detail?.evaluation;
  const evaluationRows = (evaluation?.samples || []).map(
    (sample: Item) =>
      evaluation?.observations?.find(
        (observation: Item) => observation.task_case_id === sample.task_case_id,
      ) || sample,
  );
  const canPromote =
    detail?.status === "evaluated" && evaluation?.verdict === "passed";
  return (
    <Panel
      title="任务智能体复用候选"
      className="task-agent-candidates"
      actions={
        <button
          aria-label="刷新任务智能体候选"
          onClick={() => setTick((n) => n + 1)}
        >
          <RefreshCw size={15} />
        </button>
      }
    >
      <p className="muted">
        从成功的自主任务中提取不含原资料的能力配置。基线和候选必须在独立开发样本与留出样本上实际重放，通过门槛后才进入复用库；当前任务交付不等待此过程。
      </p>
      {error && <ErrorLine>{error}</ErrorLine>}
      {canEdit && (
        <div className="candidate-extract">
          <label>
            候选来源任务
            <select
              value={source}
              onChange={(e) => setSource(e.target.value)}
              disabled={busy}
            >
              <option value="">选择已有成功自主任务</option>
              {eligible.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.title || c.goal}
                </option>
              ))}
            </select>
          </label>
          <button
            disabled={!source || busy}
            onClick={() => void action("extract")}
          >
            提取去数据化候选
          </button>
          {!eligible.length && !loading && (
            <small>
              当前没有可提取的成功自主任务。固定案例运行不直接作为任意任务智能体的复用证明。
            </small>
          )}
        </div>
      )}
      {loading ? (
        <p role="status">正在读取候选与评测记录…</p>
      ) : !candidates.length ? (
        <Empty
          title="尚无任务智能体候选"
          description="先完成真实任务，再积累独立评测样本。不会使用预设分数或单次任务结果代替复用评测。"
        />
      ) : (
        <div className="candidate-workspace">
          <div className="candidate-list" aria-label="任务智能体候选列表">
            {candidates.map((c) => (
              <button
                key={c.id}
                className={selected === c.id ? "selected" : ""}
                onClick={() => setSelected(c.id)}
              >
                <strong>{c.name}</strong>
                <span>
                  {labels[c.status] || c.status}
                  {c.evaluation_verdict === "failed"
                    ? " · 未通过"
                    : c.evaluation_verdict === "passed"
                      ? " · 通过"
                      : ""}
                </span>
                <small>{time(c.created_at)}</small>
              </button>
            ))}
          </div>
          {detail && detail.id === selected ? (
            <article className="candidate-detail">
              <h3>{detail.name}</h3>
              <p>{labels[detail.status] || detail.status}</p>
              <a
                href={`/?project=${encodeURIComponent(projectId)}&task_case=${encodeURIComponent(detail.source_task_case_id)}`}
              >
                查看来源任务
              </a>
              {!!detail.steps?.length && (
                <details>
                  <summary>候选的步骤与能力边界</summary>
                  <ol>
                    {detail.steps.map((step: Item, i: number) => (
                      <li key={i}>
                        <strong>{step.name}</strong>
                        <p>工具：{step.tool_ids?.join("、") || "未声明"}</p>
                        <p>技能：{step.skill_ids?.join("、") || "未声明"}</p>
                      </li>
                    ))}
                  </ol>
                </details>
              )}
              {!!detail.limitations?.length && (
                <ul className="candidate-limitations">
                  {detail.limitations.map((l: string, i: number) => (
                    <li key={i}>{l}</li>
                  ))}
                </ul>
              )}
              {canEdit &&
                detail.status !== "evaluating" &&
                detail.status !== "promoted" && (
                  <section className="candidate-evaluate">
                    <h4>独立评测</h4>
                    <p>
                      至少选择两个任务，不能使用来源任务或相同目标与输入的重复样本。最后一个样本作为留出集；服务端会再次校验样本独立性。
                    </p>
                    <div className="candidate-samples">
                      {evaluationCases.length ? (
                        evaluationCases.map((c) => (
                          <label key={c.id}>
                            <input
                              type="checkbox"
                              checked={samples.includes(c.id)}
                              disabled={busy}
                              onChange={(e) =>
                                setSamples((ids) =>
                                  e.target.checked
                                    ? [...ids, c.id]
                                    : ids.filter((id) => id !== c.id),
                                )
                              }
                            />
                            <span>
                              {c.title || c.goal}
                              <small>
                                {taskCaseStatus[c.status] ||
                                  c.status ||
                                  "状态未知"}
                                {samples.at(-1) === c.id && samples.length >= 2
                                  ? " · 留出样本"
                                  : ""}
                              </small>
                            </span>
                          </label>
                        ))
                      ) : (
                        <p className="muted">
                          尚无足够的独立任务样本，候选将保留在待评测状态。
                        </p>
                      )}
                    </div>
                    <label className="candidate-budget">
                      本次对照评测累计费用上限（元）
                      <input
                        type="number"
                        min="0.2"
                        max="10"
                        step="0.1"
                        value={budget}
                        onChange={(e) => setBudget(e.target.value)}
                      />
                    </label>
                    <p className="muted">
                      每个样本分别重放基线与候选，至少产生 4
                      次任务运行。模型调用统一计入原历史预算账本。
                    </p>
                    <button
                      disabled={
                        busy ||
                        samples.length < 2 ||
                        Number(budget) < 0.2 ||
                        Number(budget) > 10
                      }
                      onClick={() => void action("evaluate")}
                    >
                      <FlaskConical size={16} />
                      运行真实对照评测
                    </button>
                  </section>
                )}
              {evaluation && (
                <section className="candidate-evaluation">
                  <h4>本次评测结果</h4>
                  <p>
                    {evaluation.status === "running"
                      ? "正在运行，尚未形成最终判断。"
                      : evaluation.verdict === "passed"
                        ? "评测通过，可按授权政策进入复用库。"
                        : evaluation.verdict === "failed"
                          ? "评测未通过，候选仍不能进入复用库。"
                          : "尚未形成最终判断。"}
                  </p>
                  {!!evaluation.checks?.length && (
                    <ul className="candidate-gates">
                      {evaluation.checks.map((check: Item, i: number) => (
                        <li key={i}>
                          <span className={check.passed ? "good" : "bad"}>
                            {check.passed ? "通过" : "未通过"}
                          </span>
                          {gates[check.name] || check.name}
                        </li>
                      ))}
                    </ul>
                  )}
                  <div className="table-scroll">
                    <table>
                      <thead>
                        <tr>
                          <th>样本</th>
                          <th>基线结果</th>
                          <th>候选结果</th>
                        </tr>
                      </thead>
                      <tbody>
                        {evaluationRows.map((sample: Item, i: number) => (
                          <tr key={sample.task_case_id || i}>
                            <td>
                              {sample.split === "holdout" ? "留出" : "开发"}样本{" "}
                              {i + 1}
                              <small>
                                {cases.find((c) => c.id === sample.task_case_id)
                                  ?.title || sample.task_case_id}
                              </small>
                            </td>
                            {["baseline", "candidate"].map((kind) => (
                              <td key={kind}>
                                {sample.results?.[kind] ? (
                                  <>
                                    <span>
                                      {sample.results[kind].passed === true
                                        ? "通过"
                                        : sample.results[kind].passed === false
                                          ? "未通过"
                                          : "待判断"}
                                    </span>
                                    <small>
                                      {cny(sample.results[kind].cost_cny)}
                                      {sample.results[kind]
                                        .accounting_complete === false
                                        ? " · 费用待确认"
                                        : ""}
                                    </small>
                                    {typeof sample.results[kind]
                                      .hard_failures === "number" &&
                                      sample.results[kind].hard_failures >
                                        0 && (
                                        <small>
                                          硬性问题：
                                          {
                                            sample.results[kind].hard_failures
                                          }{" "}
                                          项
                                        </small>
                                      )}
                                    {Array.isArray(
                                      sample.results[kind].hard_failures,
                                    ) &&
                                      !!sample.results[kind].hard_failures
                                        .length && (
                                        <small>
                                          硬性问题：
                                          {sample.results[kind].hard_failures
                                            .map((f: any) =>
                                              typeof f === "string"
                                                ? f
                                                : f.code || f.message,
                                            )
                                            .join("、")}
                                        </small>
                                      )}
                                  </>
                                ) : (
                                  <span className="muted">
                                    运行中或尚无结果
                                  </span>
                                )}
                                {sample[kind] && (
                                  <button
                                    className="text-button"
                                    onClick={() => onTask(sample[kind])}
                                  >
                                    查看执行记录
                                  </button>
                                )}
                              </td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  {evaluation.totals && (
                    <p>
                      基线累计 {cny(evaluation.totals.baseline_cost_cny)} ·
                      候选累计 {cny(evaluation.totals.candidate_cost_cny)}
                    </p>
                  )}
                </section>
              )}
              {isAdmin && canPromote && (
                <button
                  className="primary"
                  disabled={busy}
                  onClick={() => void action("promote")}
                >
                  将通过评测的候选加入复用库
                </button>
              )}
              {detail.status === "promoted" && (
                <p className="good">
                  已入库。新任务可匹配这一能力，既有任务保留原快照。
                </p>
              )}
              <small className="candidate-checksum">
                冻结校验值：{detail.checksum || "—"}
              </small>
            </article>
          ) : (
            <p role="status">正在读取候选详情…</p>
          )}
        </div>
      )}
    </Panel>
  );
}
