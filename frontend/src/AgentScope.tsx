import type { Item } from "./types";
import {
  candidateStatusLabels,
  isRestrictedAgent,
  isTaskScoped,
} from "./agentScopeModel";

export default function AgentScope({ agent }: { agent: Item }) {
  if (!isRestrictedAgent(agent) && !agent.candidate_status) return null;
  const candidate = agent.candidate_status;
  return (
    <section className="agent-task-scope">
      <h3>{isTaskScoped(agent) ? "任务专属智能体" : "复用评测记录"}</h3>
      {isTaskScoped(agent) && (
        <p>
          此版本只为所属任务冻结执行。单次任务完成不代表已通过跨任务复用评测。
        </p>
      )}
      {agent.scope === "evaluation" && (
        <p>此版本仅用于独立评测重放，不属于可复用资产。</p>
      )}
      <dl>
        {agent.task_case_id && (
          <>
            <dt>所属任务</dt>
            <dd>
              <a
                href={`/?project=${encodeURIComponent(agent.project_id)}&task_case=${encodeURIComponent(agent.task_case_id)}`}
              >
                查看任务进度与结果
              </a>
            </dd>
          </>
        )}
        <dt>复用评测状态</dt>
        <dd>
          {candidate
            ? candidateStatusLabels[candidate] || candidate
            : "尚无独立评测记录"}
        </dd>
        {agent.evaluation_id && (
          <>
            <dt>评测记录</dt>
            <dd className="mono">{agent.evaluation_id}</dd>
          </>
        )}
        {agent.promoted_agent_id && (
          <>
            <dt>入库后的智能体</dt>
            <dd className="mono">{agent.promoted_agent_id}</dd>
          </>
        )}
      </dl>
    </section>
  );
}
