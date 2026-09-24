// Skill / Tool / MCP 三层能力的展示助手：状态文案、错误码、表单与 trace 展平。
// 状态与错误一律按后端稳定 code 分支，不解析中文 message。

export const capabilityStatusNames: Record<string, string> = {
  completed: "已完成",
  failed: "失败",
  dry_run: "演练预检（未实际调用）",
  blocked: "已被策略拦截",
  ready: "可用",
  unavailable: "暂不可用",
  requires_model: "待配置模型",
  disabled: "已禁用",
  degraded: "已降级",
  timeout: "调用超时",
};

export const capabilityErrorNames: Record<string, string> = {
  capability_not_found: "能力不存在",
  capability_disabled: "能力未启用",
  schema_validation_failed: "参数不符合输入约束",
  permission_denied: "权限不足，已被拦截",
  data_egress_blocked: "数据外发被策略拦截",
  confirmation_required: "需要人工确认后执行",
  provider_unavailable: "执行提供方不可用",
  mcp_connection_failed: "MCP 服务连接失败",
  tool_timeout: "工具调用超时",
  tool_result_invalid: "工具返回结果无效",
  budget_exceeded: "超出预算限制",
  tool_failed: "工具执行失败",
};

export function capabilityStatusText(
  status: string | undefined | null,
): string {
  if (!status) return "未知状态";
  return capabilityStatusNames[status] || status;
}

export function capabilityErrorText(code: string | undefined | null): string {
  if (!code) return "未知错误";
  return capabilityErrorNames[code] || `错误码 ${code}`;
}

export const providerNames: Record<string, string> = {
  local: "本地",
  http: "HTTP",
  mcp: "MCP",
};

export const egressNames: Record<string, string> = {
  none: "不外发",
  query: "仅检索词",
  derived: "派生摘要",
  raw: "原始数据",
};

export const networkNames: Record<string, string> = {
  none: "离线",
  required: "需要网络",
};

export const nodeKindNames: Record<string, string> = {
  start: "开始",
  parse: "需求解析",
  retrieve: "知识检索",
  condition: "条件判断",
  batch: "批量处理",
  analyze: "分析推理",
  review: "人工审核",
  report: "报告生成",
  end: "结束",
};

const EGRESS_RANK: Record<string, number> = {
  none: 0,
  query: 1,
  derived: 2,
  raw: 3,
};

export type ToolLike = {
  id?: string;
  name?: string;
  provider?: string;
  data_egress?: string;
  [key: string]: any;
};

export type SkillLike = {
  id?: string;
  name?: string;
  enabled?: boolean;
  node_kinds?: string[];
  allowed_tools?: string[];
  [key: string]: any;
};

// 技能与节点类型兼容：node_kinds 包含该节点类型。
export function skillCompatibleWithNode(
  skill: SkillLike,
  nodeKind: string,
): boolean {
  return Array.isArray(skill.node_kinds) && skill.node_kinds.includes(nodeKind);
}

// 工作流编辑器“使用技能”下拉：仅兼容当前节点类型且已启用的技能。
export function skillsForNodeKind(
  skills: SkillLike[],
  nodeKind: string,
): SkillLike[] {
  return (skills || []).filter(
    (skill) =>
      skill.enabled !== false && skillCompatibleWithNode(skill, nodeKind),
  );
}

// 技能授权工具数量与外发等级摘要（取授权工具中的最高外发等级）。
export function skillToolSummary(
  skill: SkillLike,
  tools: ToolLike[],
): { count: number; egress: string; egressText: string } {
  const byId = new Map((tools || []).map((tool) => [tool.id, tool]));
  const granted = (skill.allowed_tools || [])
    .map((id) => byId.get(id))
    .filter((tool): tool is ToolLike => Boolean(tool));
  const egress = granted.reduce(
    (level, tool) =>
      (EGRESS_RANK[tool.data_egress || "none"] ?? 0) > (EGRESS_RANK[level] ?? 0)
        ? tool.data_egress || "none"
        : level,
    "none",
  );
  return {
    count: (skill.allowed_tools || []).length,
    egress,
    egressText: egressNames[egress] || egress,
  };
}

// 工具健康：本地/HTTP 工具始终可用；MCP 工具取决于所属服务是否启用。
export function toolHealth(
  tool: ToolLike,
  mcpServers: { id: string; enabled?: boolean }[],
): { status: string; label: string } {
  if (tool.provider === "mcp") {
    const serverId = tool.id?.split(".")[1] || "";
    const server = (mcpServers || []).find((item) => item.id === serverId);
    if (!server) return { status: "unavailable", label: "所属 MCP 服务未登记" };
    return server.enabled
      ? { status: "ready", label: "MCP 服务已启用" }
      : { status: "disabled", label: "所属 MCP 服务已禁用" };
  }
  if (tool.provider === "http")
    return { status: "ready", label: "HTTP 工具 · 调用时校验网络" };
  return { status: "ready", label: "本地可用" };
}

export type ToolInputField = {
  name: string;
  kind: "text" | "number" | "boolean";
  required: boolean;
  description?: string;
  array?: boolean;
};

// 简化 JSON Schema → 表单字段：按 properties 生成文本/数字/布尔输入，array 以逗号分隔文本收集。
export function toolInputFields(schema: any): ToolInputField[] {
  const properties = schema?.properties;
  if (!properties || typeof properties !== "object") return [];
  const required = new Set(
    Array.isArray(schema.required) ? schema.required : [],
  );
  return Object.entries(properties).map(([name, spec]: [string, any]) => {
    const type = spec?.type;
    return {
      name,
      kind:
        type === "integer" || type === "number"
          ? "number"
          : type === "boolean"
            ? "boolean"
            : "text",
      required: required.has(name),
      description:
        typeof spec?.description === "string" ? spec.description : undefined,
      array: type === "array",
    };
  });
}

// 把表单值转换回按 schema 类型的 input 对象；空字符串的选填字段省略。
export function collectToolInput(
  fields: ToolInputField[],
  values: Record<string, string | boolean>,
): Record<string, unknown> {
  const input: Record<string, unknown> = {};
  for (const field of fields) {
    const value = values[field.name];
    if (field.kind === "boolean") {
      if (value !== undefined) input[field.name] = Boolean(value);
      continue;
    }
    const text = String(value ?? "").trim();
    if (!text) continue;
    if (field.array) {
      input[field.name] = text
        .split(/[,，]/)
        .map((item) => item.trim())
        .filter(Boolean);
    } else if (field.kind === "number") {
      const number = Number(text);
      if (!Number.isNaN(number)) input[field.name] = number;
    } else {
      input[field.name] = text;
    }
  }
  return input;
}

export type TraceRow = {
  depth: number;
  tool_id: string;
  provider: string;
  status?: string;
  duration_ms?: number;
  version?: string;
};

// 统一 trace（ToolTrace）展平为带缩进层级的 Tool 调用行；Skill 层标题由调用方渲染。
// 直接测试 Tool 时 trace.tool_id 与唯一的 tool_calls 记录相同，合并为一行。
export function flattenTrace(trace: any): TraceRow[] {
  if (!trace || typeof trace !== "object") return [];
  const rows: TraceRow[] = [];
  const calls: any[] = Array.isArray(trace.tool_calls) ? trace.tool_calls : [];
  if (trace.tool_id) {
    const own =
      calls.length === 1 && calls[0]?.tool_id === trace.tool_id
        ? calls[0]
        : null;
    rows.push({
      depth: 0,
      tool_id: trace.tool_id,
      provider: trace.provider || "",
      status: own?.status,
      duration_ms: trace.duration_ms,
      version: trace.tool_version || undefined,
    });
    if (own) return rows;
  }
  for (const call of calls) {
    rows.push({
      depth: trace.tool_id ? 1 : 0,
      tool_id: call.tool_id,
      provider: call.provider || "",
      status: call.status,
      duration_ms: call.duration_ms,
      version: call.tool_version || undefined,
    });
  }
  return rows;
}

// MCP 健康检查状态文案：disabled 是正常配置状态，不是错误。
export function mcpHealthText(health: any): { status: string; label: string } {
  const status = health?.status || "unavailable";
  if (status === "disabled")
    return { status, label: "已禁用（配置未启用，非故障）" };
  if (status === "ready")
    return { status, label: `连接正常 · 发现 ${health?.tools ?? 0} 个工具` };
  if (status === "degraded")
    return { status, label: "已降级（部分能力不可用）" };
  return {
    status: "unavailable",
    label: health?.error
      ? `不可用 · ${capabilityErrorText(health.error)}`
      : "不可用（连接失败）",
  };
}
