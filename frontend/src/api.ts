export type RecordData = Record<string, any>;
export async function api<T = any>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const headers = new Headers(options.headers);
  if (options.body && !(options.body instanceof FormData))
    headers.set("Content-Type", "application/json");
  const response = await fetch(`/api${path}`, {
    ...options,
    headers,
    credentials: "same-origin",
  });
  if (!response.ok) {
    let data: any;
    try {
      data = await response.json();
    } catch {
      data = { detail: response.statusText };
    }
    const detail = data.detail;
    const message =
      typeof detail === "string"
        ? detail
        : Array.isArray(detail)
          ? detail
              .map((x) => `${x.loc?.slice(1).join(".") || "参数"}：${x.msg}`)
              .join("；")
          : detail
            ? JSON.stringify(detail)
            : `请求失败 (${response.status})`;
    const error = new Error(message) as Error & { status: number };
    error.status = response.status;
    throw error;
  }
  if (response.status === 204) return undefined as T;
  return response.json();
}
export const post = (path: string, body: unknown = {}) =>
  api(path, { method: "POST", body: JSON.stringify(body) });
export const put = (path: string, body: unknown = {}) =>
  api(path, { method: "PUT", body: JSON.stringify(body) });
export const remove = (path: string) => api(path, { method: "DELETE" });
// Skill / Tool / MCP 三层能力 API
export const testSkill = (id: string, body: unknown) =>
  post(`/skills/${encodeURIComponent(id)}/test`, body);
export const testTool = (
  id: string,
  input: Record<string, unknown>,
  mode: string,
) => post(`/tools/${encodeURIComponent(id)}/test`, { input, mode });
export const mcpHealth = (id: string) =>
  api(`/mcp-servers/${encodeURIComponent(id)}/health`);
export const mcpRefresh = (id: string) =>
  post(`/mcp-servers/${encodeURIComponent(id)}/refresh`);
export const capabilityEvents = (runId?: string) =>
  api(
    `/capability-events${runId ? `?run_id=${encodeURIComponent(runId)}` : ""}`,
  );
export const money = (v: unknown) => Number(v || 0).toFixed(4);
export const time = (v: any) =>
  v
    ? new Date(v).toLocaleString("zh-CN", {
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
      })
    : "—";
export const statusNames: Record<string, string> = {
  draft: "草稿",
  published: "已发布",
  queued: "排队中",
  running: "运行中",
  waiting_review: "待人工审核",
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消",
  interrupted: "已中断",
  pending: "待执行",
  skipped: "已跳过",
  ready: "可用",
  not_loaded: "按需加载",
  loading: "加载中",
  unavailable: "暂不可用",
  requires_model: "待配置模型",
  indexed: "已索引",
  reviewed: "已审核",
  approved: "已通过",
  rejected: "已退回",
  success: "成功",
  passed: "通过",
  done: "已完成",
  dry_run: "演练预检",
  blocked: "已拦截",
  timeout: "超时",
  disabled: "已禁用",
  degraded: "已降级",
};
