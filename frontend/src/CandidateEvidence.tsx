import type { Item } from "./types";
import { time } from "./domain";
import { Badge, JsonView } from "./ui";

const stageNames: Record<string, string> = {
  prepare: "资料解析",
  analyze: "专业分析",
  review: "独立核查",
};
const stageList = (ids: string[] = []) =>
  ids.map((id) => stageNames[id] || id).join("、") || "无";
export default function CandidateEvidence({
  candidate,
  evaluations,
  tasks,
  onTask,
}: {
  candidate: Item;
  evaluations: Item[];
  tasks: Item[];
  onTask: (id: string) => void;
}) {
  const repair = candidate.source_repair;
  const evidence = evaluations
    .filter(
      (e) =>
        e.project_id === candidate.project_id &&
        (e.candidate_id === candidate.id ||
          (!e.candidate_id && e.id === candidate.evaluation_id)),
    )
    .sort((a, b) =>
      String(b.evaluated_at ?? "").localeCompare(String(a.evaluated_at ?? "")),
    );
  function taskLink(id: string) {
    const task = tasks.find(
      (t) => t.id === id && t.project_id === candidate.project_id,
    );
    return task ? (
      <button className="text-button" onClick={() => onTask(id)}>
        {task.name || task.request?.name || id}
      </button>
    ) : (
      "任务记录不可用"
    );
  }
  return (
    <>
      <Badge status={candidate.status} />
      {candidate.content_withheld ? (
        <p className="warning">来源已撤回、过期或不可访问，评测详情已隐藏。</p>
      ) : (
        <>
          <p>
            {candidate.target_kind === "workflow" ? "工作流版本" : "智能体版本"}{" "}
            · 基线 v{candidate.baseline_version ?? "—"}
            {candidate.candidate_version
              ? " · 已发布候选 v" + candidate.candidate_version
              : ""}
          </p>
          {candidate.target_kind === "workflow" && (
            <>
              <p className="note">
                完整重放冻结的本地流程。节点、智能体版本和权限保持受控，运行中的任务继续使用原快照。
              </p>
              <JsonView
                value={candidate.baseline?.nodes}
                label="基线流程依赖与参数"
              />
              <JsonView
                value={candidate.candidate?.nodes}
                label="候选流程依赖与参数"
              />
            </>
          )}
          {repair && (
            <section className="candidate-repair">
              <h3>触发改进的审核记录</h3>
              <dl className="role-facts">
                <dt>修复动作</dt>
                <dd>{repair.label || repair.action}</dd>
                <dt>来源任务</dt>
                <dd>
                  {taskLink(repair.task_id)} · 第{" "}
                  {(repair.source_revision ?? 0) + 1} 轮
                </dd>
                <dt>未通过检查</dt>
                <dd>{repair.failed_checks?.join("、") || "未记录"}</dd>
                <dt>复用步骤</dt>
                <dd>{stageList(repair.reused_stages)}</dd>
                <dt>重新执行</dt>
                <dd>{stageList(repair.rerun_stages)}</dd>
              </dl>
            </section>
          )}
          <h3>关联评测证据</h3>
          {evidence.length === 0 && (
            <p className="muted">尚无可见的冻结评测记录。</p>
          )}
          {evidence.map((e) => (
            <section key={e.id} className="candidate-evaluation">
              <div className="toolbar">
                <Badge status={e.status} />
                <small>{time(e.evaluated_at)}</small>
              </div>
              {e.content_withheld ? (
                <p className="warning">此记录的来源已不可用。</p>
              ) : (
                <>
                  {e.verdict && (
                    <>
                      <p>
                        基线通过 {e.verdict.baseline_passed ?? "—"} /{" "}
                        {e.verdict.sample_count ?? "—"} 个样本，候选通过{" "}
                        {e.verdict.candidate_passed ?? "—"} /{" "}
                        {e.verdict.sample_count ?? "—"} 个样本。
                      </p>
                      <p>
                        留出样本
                        {e.verdict.holdout_passed === true
                          ? "通过"
                          : e.verdict.holdout_passed === false
                            ? "未通过"
                            : "未记录"}{" "}
                        · 退化
                        {e.verdict.regression === true
                          ? "发生"
                          : e.verdict.regression === false
                            ? "未检出"
                            : "未记录"}
                      </p>
                    </>
                  )}
                  {Array.isArray(e.samples) && e.samples.length > 0 && (
                    <div className="table-scroll">
                      <table>
                        <thead>
                          <tr>
                            <th>样本任务</th>
                            <th>用途</th>
                            <th>基线</th>
                            <th>候选</th>
                          </tr>
                        </thead>
                        <tbody>
                          {e.samples.map((s: Item) => (
                            <tr key={s.task_id}>
                              <td>{taskLink(s.task_id)}</td>
                              <td>
                                {s.split === "development"
                                  ? "开发样本"
                                  : s.split === "holdout"
                                    ? "独立留出"
                                    : "未记录"}
                              </td>
                              {["baseline", "candidate"].map((k) => (
                                <td key={k}>
                                  <Badge
                                    status={
                                      s[k]?.quality?.passed === true
                                        ? "passed"
                                        : s[k]?.quality?.passed === false
                                          ? "rejected"
                                          : "unknown"
                                    }
                                  />
                                  {typeof s[k]?.attempts === "number" && (
                                    <small> {s[k].attempts} 轮</small>
                                  )}
                                </td>
                              ))}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                  <JsonView value={e} label="完整评测记录" />
                </>
              )}
            </section>
          ))}
          <JsonView value={candidate} label="候选配置与版本引用" />
        </>
      )}
    </>
  );
}
