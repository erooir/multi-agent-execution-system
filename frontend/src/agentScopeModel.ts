import type { Item } from "./types";

export function isTaskScoped(agent: Item): boolean {
  return agent.scope === "task";
}

export function isRestrictedAgent(agent: Item): boolean {
  return ["task", "evaluation", "candidate"].includes(agent.scope);
}

export function agentScopeLabel(agent: Item): string {
  return agent.scope === "task"
    ? "仅当前任务"
    : agent.scope === "evaluation"
      ? "仅独立评测"
      : agent.scope === "candidate"
        ? "候选未入库"
        : "可复用资产";
}

export const candidateStatusLabels: Record<string, string> = {
  candidate: "候选 · 待独立评测",
  evaluating: "正在独立评测",
  evaluated: "已完成评测",
  passed: "评测通过 · 尚未入库",
  rejected: "评测未通过",
  promoted: "已通过评测入库",
  rolled_back: "复用版本已回滚",
};
