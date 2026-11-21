import { useEffect, useState } from "react";
import { api } from "./api";
import { ErrorLine, Panel } from "./ui";
import { time } from "./domain";
import type { Item } from "./types";
import TaskAgentCandidates from "./TaskAgentCandidates";

type Ref = { id: string; version: string | number; checksum: string };
type Step = {
  label: string;
  agent_id?: string;
  agent_version?: number;
  depends_on: string[];
  components?: Record<"tools" | "skills" | "mcp_servers", Ref[]>;
  success_criteria?: string[];
};
type Detail = {
  id: string;
  title?: string;
  phase?: string;
  requirements?: {
    id: string;
    title: string;
    required: boolean;
    formats: string[];
  }[];
  reason?: string;
  version: number;
  steps: Step[];
  checksum?: string;
  checks?: { name: string; passed: boolean }[];
  publication?: Ref;
  lessons?: string[];
  repairs?: {
    check: string;
    guidance: string;
    revision: number;
    before_failed: number;
    after_failed: number;
  }[];
  inputs?: { id: string; source_version: string; checksum: string }[];
};
type Overview = {
  plans: {
    id: string;
    status: string;
    phase?: string;
    workflow_id?: string;
    task_id: string;
    version: number;
    created_at: string;
  }[];
  publications: { candidate_id: string; name: string; workflow_id: string }[];
};
type Tab = "library" | "plans" | "publications" | "evaluate";
const statusLabels: Record<string, string> = {
  frozen: "已冻结",
  rejected: "规划被拒绝",
  requires_data: "待接入资料",
};
const checkLabels: Record<string, string> = {
  independent_development_and_holdout: "独立开发与留出样本",
  all_candidate_quality_passed: "候选质量通过",
  all_charges_settled: "费用已结算",
  bounded_cost_regression: "费用增长在限额内",
  measured_quality_or_cost_gain: "质量或费用改善",
};
type Props = {
  projectId: string;
  canEdit?: boolean;
  isAdmin?: boolean;
  onTask?: (id: string) => void;
  workflows: Item[];
  onWorkflow: (id: string) => void;
  onCreateWorkflow: () => void;
};

export default function WorkflowLearning(props: Props) {
  return <ProjectLearning key={props.projectId} {...props} />;
}

function ProjectLearning({
  projectId,
  canEdit = false,
  isAdmin = false,
  onTask,
  workflows,
  onWorkflow,
  onCreateWorkflow,
}: Props) {
  const [tab, setTab] = useState<Tab>("library"),
    [summary, setSummary] = useState<Overview | null>(null);
  const [selected, setSelected] = useState(""),
    [detail, setDetail] = useState<Detail | null>(null);
  const [error, setError] = useState(""),
    [refresh, setRefresh] = useState(0),
    [workflowFilter, setWorkflowFilter] = useState("");
  useEffect(() => {
    const ctr = new AbortController();
    setError("");
    setDetail(null);
    setSelected("");
    setSummary(null);
    if (tab === "evaluate") return;
    api<Overview>(
      "/v2/workflow-learning?project_id=" + encodeURIComponent(projectId),
      { signal: ctr.signal },
    )
      .then((s) => {
        if (!ctr.signal.aborted) {
          setSummary(s);
          setSelected(
            tab === "plans"
              ? s.plans.find((plan) => !workflowFilter || plan.workflow_id === workflowFilter)?.id || ""
              : tab === "publications" ? s.publications[0]?.candidate_id || "" : "",
          );
        }
      })
      .catch((e) => {
        if (!ctr.signal.aborted) setError(e.message);
      });
    return () => ctr.abort();
  }, [projectId, tab, refresh, workflowFilter]);
  useEffect(() => {
    if (!selected || (tab !== "plans" && tab !== "publications")) return;
    const ctr = new AbortController();
    setDetail(null);
    setError("");
    api<Detail>(
      "/v2/workflow-learning/" + tab + "/" + encodeURIComponent(selected),
      { signal: ctr.signal },
    )
      .then((d) => {
        if (!ctr.signal.aborted) setDetail(d);
      })
      .catch((e) => {
        if (!ctr.signal.aborted) setError(e.message);
      });
    return () => ctr.abort();
  }, [projectId, selected, tab, refresh]);
  const tabs: { id: Tab; title: string }[] = [
    { id: "library", title: "工作流库" },
    { id: "plans", title: "任务拆解" },
    { id: "publications", title: "已固化流程" },
  ];
  if (canEdit && onTask) tabs.push({ id: "evaluate", title: "评测与入库" });
  const plans = summary?.plans.filter((plan) => !workflowFilter || plan.workflow_id === workflowFilter) || [];
  return (
    <Panel
      title="流程与改进经验"
      actions={
        <>
          {tab === "library" && canEdit && (
            <button className="primary" onClick={onCreateWorkflow}>创建流程</button>
          )}
          <button onClick={() => setRefresh((n) => n + 1)}>刷新</button>
        </>
      }
    >
      <div className="segmented">
        {tabs.map((t) => (
          <button
            key={t.id}
            aria-pressed={tab === t.id}
            className={tab === t.id ? "selected" : ""}
            onClick={() => {
              setSelected("");
              setDetail(null);
              setWorkflowFilter("");
              setTab(t.id);
            }}
          >
            {t.title}
          </button>
        ))}
      </div>
      {tab === "library" && (
        <div className="table-scroll">
          <table>
            <thead><tr><th>工作流</th><th>来源</th><th>版本</th><th>操作</th></tr></thead>
            <tbody>{workflows.filter((workflow) => !workflow.task_case_id || workflow.source === "task_planning").map((workflow) => (
              <tr key={workflow.id}>
                <td>{workflow.name}</td>
                <td>{workflow.source === "task_planning" ? "任务自动生成" : "项目工作流"}</td>
                <td>{workflow.current_version ? `v${workflow.current_version}` : "草稿"}</td>
                <td className="actions">
                  <button onClick={() => onWorkflow(workflow.id)}>查看流程</button>
                  {summary?.plans.some((plan) => plan.workflow_id === workflow.id) && (
                    <button onClick={() => { setWorkflowFilter(workflow.id); setTab("plans"); }}>执行与经验</button>
                  )}
                </td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      )}
      {tab === "evaluate" && canEdit && onTask && (
        <TaskAgentCandidates
          projectId={projectId}
          canEdit={canEdit}
          isAdmin={isAdmin}
          onTask={onTask}
        />
      )}
      {error && <ErrorLine>{error}</ErrorLine>}
      {(tab === "plans" || tab === "publications") && !summary && !error && (
        <p role="status">正在读取流程记录…</p>
      )}
      {(tab === "plans" || tab === "publications") && summary && (
        <div>
          {tab === "plans" ? (
            plans.length ? (
              <label className="field">
                <span>最近 50 个计划版本</span>
                <select
                  value={selected}
                  onChange={(e) => setSelected(e.target.value)}
                >
                  {plans.map((p) => (
                    <option key={p.id} value={p.id}>
                      {time(p.created_at)} ·{" "}
                      {p.phase === "requirements" ? "资料需求" : "执行规划"} ·{" "}
                      {p.task_id.slice(0, 8)} · v{p.version} ·{" "}
                      {statusLabels[p.status] || p.status}
                    </option>
                  ))}
                </select>
              </label>
            ) : (
              <p>
                项目暂无执行计划。任务开始规划后，这里会显示步骤、依赖及验收条件。
              </p>
            )
          ) : summary.publications.length ? (
            <label className="field">
              <span>已评测流程</span>
              <select
                value={selected}
                onChange={(e) => setSelected(e.target.value)}
              >
                {summary.publications.map((p) => (
                  <option key={p.candidate_id} value={p.candidate_id}>
                    {p.name}
                  </option>
                ))}
              </select>
            </label>
          ) : (
            <p>
              项目暂无已固化流程。候选通过独立开发、留出样本评测并发布后可供后续任务复用。
            </p>
          )}
          {detail && (
            <div>
              <h3>
                {detail.title || (tab === "plans" ? "执行计划" : "已发布流程")}{" "}
                · v{detail.version}
              </h3>
              {detail.reason && <p>{detail.reason}</p>}
              {detail.phase === "requirements" && (
                <div>
                  <h4>资料需求分析</h4>
                  <ul>
                    {detail.requirements?.map((r) => (
                      <li key={r.id}>
                        {r.title} · {r.required ? "必需" : "可选"} ·{" "}
                        {r.formats.join("、")}
                      </li>
                    ))}
                  </ul>
                  <p>任务接入阶段的记录；执行节点在后续计划版本中展示。</p>
                </div>
              )}
              {detail.publication && (
                <p>
                  复用流程 {detail.publication.id} · v
                  {detail.publication.version}
                </p>
              )}
              <ol>
                {detail.steps.map((step, i) => (
                  <li key={i}>
                    <strong>{step.label || "步骤 " + (i + 1)}</strong>
                    <p>
                      {step.depends_on.length
                        ? "前置步骤 " + step.depends_on.join("、")
                        : "无前置步骤"}
                      {step.agent_id &&
                        " · 智能体 " +
                          step.agent_id +
                          " v" +
                          step.agent_version}
                    </p>
                    {!!step.success_criteria?.length && (
                      <p>验收条件 · {step.success_criteria.join("、")}</p>
                    )}
                    {step.components && (
                      <details>
                        <summary>
                          冻结工具 {step.components.tools.length} · 技能{" "}
                          {step.components.skills.length} · MCP 服务{" "}
                          {step.components.mcp_servers.length}
                        </summary>
                        {[
                          ...step.components.tools,
                          ...step.components.skills,
                          ...step.components.mcp_servers,
                        ].map((ref) => (
                          <p key={ref.id}>
                            <code>{ref.id}</code> · v{ref.version} ·{" "}
                            <code>{ref.checksum}</code>
                          </p>
                        ))}
                      </details>
                    )}
                  </li>
                ))}
              </ol>
              {!!detail.inputs?.length && (
                <details>
                  <summary>本次输入版本 · {detail.inputs.length}</summary>
                  {detail.inputs.map((ref) => (
                    <p key={ref.id}>
                      {ref.id} · v{ref.source_version} ·{" "}
                      <code>{ref.checksum}</code>
                    </p>
                  ))}
                </details>
              )}
              {!!detail.checks?.length && (
                <details>
                  <summary>晋升依据</summary>
                  {detail.checks.map((c) => (
                    <p key={c.name}>
                      {checkLabels[c.name] || c.name} ·{" "}
                      {c.passed ? "通过" : "未通过"}
                    </p>
                  ))}
                </details>
              )}
              {!!detail.lessons?.length && (
                <div>
                  <h4>规划时参考的评审经验</h4>
                  {detail.lessons.map((t) => (
                    <p key={t}>{t}</p>
                  ))}
                </div>
              )}
              {detail.repairs && (
                <div>
                  <h4>已验证的修订经验</h4>
                  {detail.repairs.length ? (
                    detail.repairs.map((r, i) => (
                      <p key={i}>
                        第 {r.revision} 次修订 · {r.check} · 失败项{" "}
                        {r.before_failed} → {r.after_failed} · {r.guidance}
                      </p>
                    ))
                  ) : (
                    <p>来源任务没有可核验的失败后修订记录。</p>
                  )}
                </div>
              )}
              {detail.checksum && (
                <small>
                  版本校验值 <code>{detail.checksum}</code>
                </small>
              )}
            </div>
          )}
        </div>
      )}
    </Panel>
  );
}
