// Derive presentation from persisted outcomes, never from model-written prose.
type Data = Record<string, any>;

export type CapabilityCallRow = Data & {
  sequence: number;
  failed: boolean;
  status: string;
  error_code?: string;
  tool_calls: Data[];
};

export type NodeCallDiagnostics = {
  calls: CapabilityCallRow[];
  failures: CapabilityCallRow[];
  successes: CapabilityCallRow[];
  severity: "warning" | "error" | null;
  title: string;
  hint: string;
};

const failureStatuses = new Set([
  "failed",
  "error",
  "blocked",
  "unavailable",
  "timeout",
]);
const successStatuses = new Set(["completed", "success", "done"]);

function hasError(error: any): boolean {
  if (typeof error === "string") return Boolean(error.trim());
  return Boolean(error && (error.code || error.message));
}

function isFailure(call: Data): boolean {
  return (
    failureStatuses.has(call.status) ||
    Boolean(call.error_code) ||
    hasError(call.error)
  );
}

function normalizeCall(call: Data, sequence: number): CapabilityCallRow {
  const toolCalls = Array.isArray(call.tool_calls)
    ? call.tool_calls.filter((tool: any) => tool && typeof tool === "object")
    : [];
  const errorCode =
    call.error_code ||
    call.error?.code ||
    toolCalls.find((tool: Data) => isFailure(tool))?.error_code;
  return {
    ...call,
    sequence,
    status: call.status || "unknown",
    error_code: errorCode,
    tool_calls: toolCalls,
    failed: isFailure(call) || toolCalls.some(isFailure),
  };
}

/** Persisted call order is kept, including global sequence numbers after filtering.
 * Legacy step traces are used only where a node has no run-level records.
 */
export function nodeCapabilityCalls(
  run: Data | undefined | null,
  nodeId?: string,
): CapabilityCallRow[] {
  const rawCalls: Data[] = Array.isArray(run?.capability_calls)
    ? run.capability_calls.filter(
        (call: any) => call && typeof call === "object",
      )
    : [];
  const rows = rawCalls.map((call, index) => normalizeCall(call, index + 1));
  const recordedNodes = new Set(rawCalls.map((call) => call.node_id));
  const steps: Data[] = Array.isArray(run?.steps) ? run.steps : [];
  for (const step of steps) {
    if (!step || recordedNodes.has(step.node_id)) continue;
    const payload = step.payload || {};
    const trace = payload.trace;
    if (!trace || typeof trace !== "object") continue;
    const toolCalls = Array.isArray(trace.tool_calls)
      ? trace.tool_calls
      : trace.tool_id
        ? [
            {
              tool_id: trace.tool_id,
              provider: trace.provider,
              status: trace.status,
              duration_ms: trace.duration_ms,
            },
          ]
        : [];
    if (!toolCalls.length && !hasError(payload.error)) continue;
    rows.push(
      normalizeCall(
        {
          ...trace,
          node_id: step.node_id,
          skill_id: payload.skill_id || step.skill_id,
          agent_id: payload.agent_id,
          status: payload.status || trace.status || step.status,
          error_code: payload.error?.code || trace.error_code,
          tool_calls: toolCalls,
          source: "legacy_trace",
        },
        rows.length + 1,
      ),
    );
    recordedNodes.add(step.node_id);
  }
  return nodeId === undefined
    ? rows
    : rows.filter((call) => call.node_id === nodeId);
}

/** HTTP throttling is reported only when a structured response status confirms it. */
export function capabilityResolutionHint(
  code?: string | null,
  details?: { status_code?: number; http_status?: number },
): string {
  if (details?.status_code === 429 || details?.http_status === 429) {
    return "工具服务触发限流，请降低调用频率，等待服务恢复后重试失败查询。";
  }
  const hints: Record<string, string> = {
    provider_unavailable:
      "请检查网络连接和工具服务状态；本地工具还需检查依赖与数据文件，恢复后重试失败查询。",
    mcp_connection_failed:
      "请在技能工具箱的 MCP 服务页检查连接和服务配置，恢复服务后重试。",
    tool_timeout: "请检查工具服务响应情况，缩小查询范围或分批处理，稍后重试。",
    schema_validation_failed:
      "请检查调用参数、必填项及格式，按工具输入要求修正后重试。",
    permission_denied:
      "请联系管理员核对当前智能体与用户的工具授权，再重试所需调用。",
    data_egress_blocked:
      "请改用获准外发的公开资料或本地工具，保留仅本地资料的外发限制。",
    confirmation_required: "请先完成所需的人工确认，再重新执行相关调用。",
    capability_not_found:
      "请检查流程和智能体引用的技能或工具是否已登记，并更新引用后重试。",
    capability_disabled: "请检查技能或工具的启用状态，由管理员确认配置后重试。",
    tool_result_invalid:
      "请检查工具返回格式和查询条件，可在技能工具箱测试该工具后重试。",
    budget_exceeded:
      "请在系统设置查看剩余预算和待结算预留，缩小任务范围；累计 300 元预算上限保持有效。",
    tool_failed:
      "请查看失败调用的错误码及参数要求，在技能工具箱检查相关工具后重试。",
  };
  return (
    hints[code || ""] ||
    "请查看调用详情定位失败工具，检查其配置和服务状态，再按需重试失败查询。"
  );
}

function recoveryKind(
  calls: CapabilityCallRow[],
): "alternative" | "retry" | null {
  const failedTools = new Set<string>();
  let retried = false;
  let alternative = false;
  for (const call of calls) {
    for (const tool of call.tool_calls) {
      if (!tool.tool_id) continue;
      if (isFailure(tool)) {
        failedTools.add(tool.tool_id);
      } else if (successStatuses.has(tool.status) && failedTools.size) {
        if (failedTools.has(tool.tool_id)) retried = true;
        else alternative = true;
        // Once a retry succeeds, a recipe's later normal tool steps are not
        // evidence that the failed tool was replaced.
        failedTools.clear();
      }
    }
  }
  return alternative ? "alternative" : retried ? "retry" : null;
}

export function getNodeCallDiagnostics(
  run: Data | undefined | null,
  step: Data | undefined | null,
): NodeCallDiagnostics {
  const calls = step?.node_id ? nodeCapabilityCalls(run, step.node_id) : [];
  const failures = calls.filter((call) => call.failed);
  const successes = calls.filter(
    (call) => !call.failed && successStatuses.has(call.status),
  );
  const nodeError = Boolean(
    failureStatuses.has(step?.status) ||
    hasError(step?.error) ||
    hasError(step?.payload?.error),
  );
  const severity = nodeError ? "error" : failures.length ? "warning" : null;
  let title = "";
  if (severity === "error") {
    title = "节点执行失败，请查看错误与调用详情";
  } else if (severity === "warning") {
    if (successStatuses.has(step?.status)) {
      const recovered = recoveryKind(calls);
      title =
        recovered === "alternative"
          ? "出现工具调用失败，已更换其它可用工具"
          : recovered === "retry"
            ? "出现工具调用失败，后续重试已成功"
            : "出现工具调用失败，节点已完成";
    } else if (step?.status === "running") {
      title = "出现工具调用失败，节点仍在执行";
    } else if (step?.status === "waiting_review") {
      title = "出现工具调用失败，当前等待人工审核";
    } else if (step?.status === "cancelled") {
      title = "出现工具调用失败，节点已取消";
    } else if (step?.status === "interrupted") {
      title = "出现工具调用失败，节点执行已中断";
    } else {
      title = "出现工具调用失败，请查看调用详情";
    }
  }
  const codes = [...new Set(failures.map((call) => call.error_code))];
  const nodeCode = step?.payload?.error?.code || step?.error?.code;
  if (nodeCode && !codes.includes(nodeCode)) codes.unshift(nodeCode);
  const resolution = [
    ...new Set(
      (codes.length ? codes : [undefined]).map((code) =>
        capabilityResolutionHint(code),
      ),
    ),
  ].join(" ");
  const coverage =
    severity === "warning" && successStatuses.has(step?.status)
      ? "节点完成不代表每次失败查询都已补齐，请核查结果是否覆盖研究目标。"
      : "";
  return {
    calls,
    failures,
    successes,
    severity,
    title,
    hint: severity ? `${resolution}${coverage ? ` ${coverage}` : ""}` : "",
  };
}
