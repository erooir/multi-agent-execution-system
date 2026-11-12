import { textValue } from "./taskCases";

const validationLabels: Record<string, string> = {
  unknown_tool: "工具不在可用目录中",
  unknown_skill: "技能不在可用目录中",
  skill_dependency_unavailable: "技能所需工具不可用",
  tool_limit_exceeded: "步骤授权工具数量超出上限",
  unavailable_reusable_agent: "拟复用智能体不可用",
  reuse_binding_mismatch: "拟复用智能体与所需工具或技能不匹配",
  repair_no_change: "未针对审核问题调整原流程",
  invalid_plan_contract: "规划响应不符合执行合同",
};

function validationLabel(error: Record<string, any>) {
  const step = Number.isInteger(error.step) ? `步骤 ${error.step}：` : "";
  const label =
    validationLabels[error.code] ||
    `规划校验未通过（${error.code || "未提供代码"}）`;
  const identifiers = [error.skill_id, error.id].filter(Boolean);
  return `${step}${label}${identifiers.length ? ` · ${identifiers.join(" / ")}` : ""}`;
}

function normalizationLabel(change: Record<string, any>) {
  const step = Number.isInteger(change.step) ? `步骤 ${change.step}：` : "";
  if (change.action === "canonical_tool_id")
    return `${step}工具标识规范化：${change.from} → ${change.to}`;
  if (change.action === "skill_dependency_added")
    return `${step}补齐技能 ${change.skill_id} 所需工具 ${change.tool_id}`;
  return `${step}规划调整（${change.action || "未提供类型"}）`;
}

export default function ExecutionPlanHistory({
  plans,
}: {
  plans: Record<string, any>[];
}) {
  if (!plans.length) return null;
  return (
    <div className="taskcase-execution-section">
      <h3>执行计划与规划校验</h3>
      <p className="taskcase-muted">
        冻结表示计划可用于执行；实际执行情况以下方智能体和工具调用记录为准。
      </p>
      {plans.map((plan, index) => {
        const rejected = plan.status === "rejected";
        const requiresData = plan.status === "requires_data";
        const executable = plan.status === "frozen";
        const statusLabel = rejected
          ? "已拒绝 · 未执行"
          : requiresData
            ? "需补充资料 · 未执行"
            : executable
              ? "已冻结 · 可执行"
              : "历史计划 · 状态未单列";
        const corrections: string[] = Array.isArray(plan.rejected_attempts)
          ? plan.rejected_attempts
          : [];
        return (
          <details
            className={`taskcase-plan${rejected ? " is-rejected" : ""}`}
            key={plan.id || index}
            open={index === plans.length - 1}
          >
            <summary>
              计划 v{plan.version || index + 1} · {statusLabel}
              {plan.attempt ? ` · 第 ${plan.attempt} 次规划尝试` : ""}
            </summary>
            {plan.reason && <p>{plan.reason}</p>}
            {rejected && (
              <p className="taskcase-notice">
                该计划未通过执行前校验，其中的步骤没有据此执行。后续纠正单独保留，原拒绝记录不会覆盖。
              </p>
            )}
            {requiresData && (
              <p className="taskcase-notice">
                规划识别出资料缺口；这些步骤尚未进入执行。
              </p>
            )}
            {!!plan.validation_errors?.length && (
              <div className="taskcase-plan-validation">
                <strong>拒绝原因</strong>
                <ul>
                  {plan.validation_errors.map(
                    (error: Record<string, any>, i: number) => (
                      <li key={i}>{validationLabel(error)}</li>
                    ),
                  )}
                </ul>
              </div>
            )}
            {!!corrections.length && (
              <p className="taskcase-plan-correction">
                已针对{" "}
                {corrections
                  .map((id) => {
                    const original = plans.find((item) => item.id === id);
                    return original
                      ? `计划 v${original.version || plans.indexOf(original) + 1}`
                      : "先前计划";
                  })
                  .join("、")}{" "}
                的拒绝原因重新规划并通过校验。
              </p>
            )}
            {!!plan.normalizations?.length && (
              <div className="taskcase-plan-validation">
                <strong>执行前规范化与依赖补齐</strong>
                <ul>
                  {plan.normalizations.map(
                    (change: Record<string, any>, i: number) => (
                      <li key={i}>{normalizationLabel(change)}</li>
                    ),
                  )}
                </ul>
                <small>
                  以上是目录内标识规范化或技能依赖补齐，工具是否被调用仍以实际记录为准。
                </small>
              </div>
            )}
            {!!plan.steps?.length && (
              <>
                <p>
                  <strong>
                    {rejected || requiresData ? "未执行的拟定步骤" : "计划步骤"}
                  </strong>
                </p>
                <ol>
                  {plan.steps.map((step: Record<string, any>, i: number) => (
                    <li key={step.id || i}>
                      <strong>{step.label || step.name || step.id}</strong>
                      {step.success_criteria && (
                        <p>{textValue(step.success_criteria)}</p>
                      )}
                      {!!step.tool_ids?.length && (
                        <small>
                          {executable ? "授权工具" : "计划工具"}：
                          {step.tool_ids.join("、")}
                        </small>
                      )}
                      {!!step.skill_ids?.length && (
                        <small>技能：{step.skill_ids.join("、")}</small>
                      )}
                    </li>
                  ))}
                </ol>
              </>
            )}
          </details>
        );
      })}
    </div>
  );
}
