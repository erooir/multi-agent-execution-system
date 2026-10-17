import type { Item } from "./types.ts";
export const modeName = (mode: string) =>
  (
    ({
      local: "本地工具",
      cloud: "网关模型",
      rehearsal: "演练 · 非推理",
    }) as Record<string, string>
  )[mode] ||
  mode ||
  "—";
export const taskTypes: Record<string, string> = {
  structured_transform: "结构化数据整理",
  source_summary: "来源文本整理",
  text_analysis: "文本分析",
  image_inspect: "图片元数据检查",
  audio_transcribe: "音频转写",
  video_analyze: "视频分析",
};
export const statusNames: Record<string, string> = {
  candidate: "候选待评测",
  evaluated: "已评测",
  unconfigured: "尚未对接",
  stopped: "已停止",
  accepted: "已受理",
  planning: "规划中",
  queued: "排队中",
  running: "运行中",
  evaluating: "自动评审中",
  revising: "修订中",
  succeeded: "成功",
  partial: "部分交付",
  failed: "失败",
  cancelled: "已取消",
  draft: "草稿",
  published: "已发布",
  disabled: "已停用",
  retired: "已退役",
  destroyed: "已销毁",
  active: "有效",
  revoked: "已撤回",
  released: "已释放",
  abandoned: "异常释放",
  pending: "待执行",
  completed: "已完成",
  passed: "通过",
  rejected: "未通过",
  promoted: "已晋升",
  rolled_back: "已回滚",
  ready: "就绪",
  online: "在线",
  offline: "离线",
  healthy: "正常",
  stale: "心跳过期",
  unknown: "未知",
  enabled: "已启用",
  skipped: "已跳过",
  unsupported: "未支持",
};
export const reasonNames: Record<string, string> = {
  insufficient_data: "输入数据不足",
  unsupported_capability: "尚无已验证能力",
  quality_not_met: "未达到质量要求",
  deadline_exceeded: "超过任务时限",
  budget_exhausted: "预算不足",
  execution_uncertain: "调用结果不确定",
  permission_denied: "权限不足",
  data_revoked: "输入已撤回",
  model_unavailable: "模型不可用",
};
export const terminal = new Set([
  "succeeded",
  "partial",
  "failed",
  "cancelled",
]);
export const statusTone = (status: string) =>
  [
    "succeeded",
    "passed",
    "published",
    "active",
    "released",
    "ready",
    "healthy",
    "online",
    "promoted",
    "completed",
  ].includes(status)
    ? "good"
    : ["failed", "rejected", "revoked", "abandoned", "destroyed"].includes(
          status,
        )
      ? "bad"
      : [
            "partial",
            "revising",
            "disabled",
            "retired",
            "stale",
            "unknown",
          ].includes(status)
        ? "warn"
        : "neutral";
export const splitList = (value: string) => [
  ...new Set(
    value
      .split(/[,，\n]/)
      .map((x) => x.trim())
      .filter(Boolean),
  ),
];
export function parseObject(value: string, name = "JSON"): Item {
  const parsed = JSON.parse(value || "{}");
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed))
    throw new Error(`${name} 必须是 JSON 对象`);
  return parsed;
}
export const money = (value: unknown) =>
  typeof value === "number" && Number.isFinite(value) ? value.toFixed(4) : "—";
export const time = (value: any) =>
  value && !isNaN(new Date(value).getTime())
    ? new Date(value).toLocaleString("zh-CN", {
        timeZone: "Asia/Shanghai",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
        hour12: false,
      })
    : "—";
export function projectItems(items: Item[] | undefined, projectId: string) {
  return (items || []).filter((item) => item.project_id === projectId);
}
export function taskSummary(tasks: Item[]) {
  const done = tasks.filter((t) => terminal.has(t.status));
  return {
    total: tasks.length,
    active: tasks.length - done.length,
    succeeded: done.filter((t) => t.status === "succeeded").length,
    failed: done.filter((t) => t.status === "failed").length,
    terminal: done.length,
  };
}
export function graphLayout(
  items: Item[],
): (Item & { graphId: string; position: { x: number; y: number } })[] {
  const byId = new Map(items.map((n) => [String(n.node_id || n.id), n]));
  const depths = new Map<string, number>();
  const depth = (id: string, seen = new Set<string>()): number => {
    if (depths.has(id)) return depths.get(id)!;
    if (seen.has(id)) return 0;
    const next = new Set(seen).add(id);
    const parents = (byId.get(id)?.depends_on || []).filter((p: string) =>
      byId.has(p),
    );
    const result = parents.length
      ? Math.max(...parents.map((p: string) => depth(p, next))) + 1
      : 0;
    depths.set(id, result);
    return result;
  };
  const lanes = new Map<number, number>();
  return items.map((n) => {
    const id = String(n.node_id || n.id),
      column = depth(id),
      row = lanes.get(column) || 0;
    lanes.set(column, row + 1);
    return { ...n, graphId: id, position: { x: column * 368, y: row * 224 } };
  });
}
