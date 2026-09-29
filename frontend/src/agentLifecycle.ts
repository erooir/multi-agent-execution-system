export type LifecycleStatus = "active" | "disabled" | "destroyed";
export type AgentRecord = Record<string, any>;

export const lifecycleNames: Record<LifecycleStatus, string> = {
  active: "已启用",
  disabled: "已停用",
  destroyed: "已销毁",
};

export function lifecycleStatus(agent: AgentRecord): LifecycleStatus {
  if (agent.lifecycle_status === "destroyed") return "destroyed";
  if (agent.lifecycle_status === "disabled" || agent.enabled === false)
    return "disabled";
  return "active";
}

export function agentAvailable(agent?: AgentRecord): boolean {
  return !!agent && lifecycleStatus(agent) === "active";
}

export function agentOptionLabel(agent: AgentRecord): string {
  const status = lifecycleStatus(agent);
  return `${agent.name} · v${agent.version || 1} · ${agent.usage?.workflow_count ?? 0} 个流程${status === "active" ? "" : ` · ${lifecycleNames[status]}`}`;
}

export function filterAgents(
  agents: AgentRecord[],
  filter: LifecycleStatus | "available" | "all",
  search: string,
): AgentRecord[] {
  const query = search.trim().toLocaleLowerCase();
  return agents.filter((agent) => {
    const status = lifecycleStatus(agent);
    return (
      (filter === "all" ||
        (filter === "available"
          ? status !== "destroyed"
          : status === filter)) &&
      `${agent.name || ""} ${agent.description || ""} ${agent.id || ""}`
        .toLocaleLowerCase()
        .includes(query)
    );
  });
}

export const instanceStatusNames: Record<string, string> = {
  idle: "待执行",
  running: "执行中",
  released: "已释放",
};

export const lifecycleActionNames: Record<string, string> = {
  create: "创建智能体",
  edit: "更新配置",
  update: "更新配置",
  enable: "启用智能体",
  disable: "停用智能体",
  clone: "创建独立副本",
  destroy: "销毁智能体",
  instance_create: "创建执行上下文",
  instance_acquire: "使用执行上下文",
  instance_reuse: "复用执行上下文",
  instance_release: "释放执行上下文",
  context_created: "创建执行上下文",
  context_started: "开始执行节点",
  context_reused: "任务内复用执行上下文",
  context_finished: "结束节点执行",
  context_released: "释放执行上下文",
  legacy_snapshot_captured: "保存历史任务配置快照",
};

export const releaseReasonNames: Record<string, string> = {
  waiting_review: "等待人工审核",
  completed: "任务已完成",
  failed: "任务失败",
  cancelled: "任务已取消",
  interrupted: "任务已中断",
  retry: "重新执行任务",
  rejected: "审核退回",
  process_restarted: "服务重新启动",
};

export function lifecycleEventDetail(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (!detail || typeof detail !== "object") return "";
  const value = detail as AgentRecord;
  const parts = [
    value.source_name
      ? `源自 ${value.source_name} · v${value.source_version || 1}`
      : "",
    value.version != null ? `配置 v${value.version}` : "",
    value.node_id ? `节点 ${value.node_id}` : "",
    value.use_count != null ? `累计使用 ${value.use_count} 次` : "",
    value.mode ? (value.mode === "live" ? "真实模型调用" : "本地演练") : "",
    value.outcome
      ? `执行结果：${releaseReasonNames[value.outcome] || ({ success: "成功", skipped: "已跳过" } as Record<string, string>)[value.outcome] || value.outcome}`
      : "",
    value.reason
      ? `原因：${releaseReasonNames[value.reason] || value.reason}`
      : "",
    value.notice || "",
  ].filter(Boolean);
  return parts.length ? parts.join(" · ") : JSON.stringify(detail);
}
