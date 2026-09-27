import { test } from "node:test";
import assert from "node:assert/strict";
import {
  capabilityResolutionHint,
  getNodeCallDiagnostics,
  nodeCapabilityCalls,
} from "../src/callDiagnostics.ts";

const tool = (tool_id, status) => ({ tool_id, status, provider: "http" });
const call = (skill_id, status, tool_calls = [], extra = {}) => ({
  node_id: "retrieve",
  skill_id,
  status,
  tool_calls,
  ...(status === "failed" ? { error_code: "provider_unavailable" } : {}),
  ...extra,
});
const failed = call("literature", "failed", [tool("openalex", "failed")]);
const completed = { node_id: "retrieve", status: "completed" };
const run = (calls, steps = [completed]) => ({
  capability_calls: calls,
  steps,
});

test("completed node with later different-tool success displays a recoverable warning", () => {
  const result = getNodeCallDiagnostics(
    run([
      failed,
      call("knowledge", "completed", [tool("local.search", "completed")]),
    ]),
    completed,
  );
  assert.equal(result.severity, "warning");
  assert.equal(result.title, "出现工具调用失败，已更换其它可用工具");
  assert.equal(result.failures.length, 1);
  assert.equal(result.successes.length, 1);
  assert.match(result.hint, /核查结果是否覆盖/);
  assert.match(result.hint, /网络连接/);
});

test("same-tool retry uses retry wording even when the skill wrapper differs", () => {
  for (const skill of ["literature", "other-skill"]) {
    const result = getNodeCallDiagnostics(
      run([failed, call(skill, "completed", [tool("openalex", "completed")])]),
      completed,
    );
    assert.equal(result.title, "出现工具调用失败，后续重试已成功");
    assert.equal(result.severity, "warning");
  }
});

test("normal recipe continuation after a successful retry is not a tool replacement", () => {
  const result = getNodeCallDiagnostics(
    run([
      failed,
      call("literature", "completed", [
        tool("openalex", "completed"),
        tool("crossref", "completed"),
      ]),
    ]),
    completed,
  );
  assert.equal(result.title, "出现工具调用失败，后续重试已成功");
});

test("earlier successes, missing tool identity and dry runs do not prove recovery", () => {
  const success = call("knowledge", "completed", [
    tool("local.search", "completed"),
  ]);
  for (const calls of [
    [success, failed],
    [failed],
    [failed, call("knowledge", "completed")],
    [failed, call("knowledge", "dry_run", [tool("local.search", "dry_run")])],
  ]) {
    const result = getNodeCallDiagnostics(run(calls), completed);
    assert.equal(result.severity, "warning");
    assert.equal(result.title, "出现工具调用失败，节点已完成");
  }
});

test("actual node failure stays red even after successful calls", () => {
  const calls = [
    failed,
    call("knowledge", "completed", [tool("local.search", "completed")]),
  ];
  for (const step of [
    { ...completed, status: "failed" },
    { ...completed, status: "error" },
    { ...completed, error: "report generation failed" },
    {
      ...completed,
      payload: { error: { code: "tool_failed", message: "failed" } },
    },
  ]) {
    const result = getNodeCallDiagnostics(run(calls), step);
    assert.equal(result.severity, "error");
    assert.equal(result.title, "节点执行失败，请查看错误与调用详情");
  }
});

test("running, review, cancelled and interrupted states never claim completed recovery", () => {
  const calls = [
    failed,
    call("knowledge", "completed", [tool("local.search", "completed")]),
  ];
  for (const status of [
    "running",
    "waiting_review",
    "cancelled",
    "interrupted",
  ]) {
    const result = getNodeCallDiagnostics(run(calls), { ...completed, status });
    assert.equal(result.severity, "warning");
    assert.doesNotMatch(result.title, /已更换|重试已成功|节点已完成/);
  }
});

test("node filtering preserves global sequence without borrowing another node success", () => {
  const data = run([
    call("x", "completed", [], { node_id: "earlier" }),
    failed,
    call("knowledge", "completed", [tool("local.search", "completed")], {
      node_id: "later",
    }),
  ]);
  assert.deepEqual(
    nodeCapabilityCalls(data, "retrieve").map((row) => row.sequence),
    [2],
  );
  const result = getNodeCallDiagnostics(data, completed);
  assert.equal(result.calls.length, 1);
  assert.equal(result.title, "出现工具调用失败，节点已完成");
  assert.equal(nodeCapabilityCalls(data).length, 3);
});

test("successful, empty and legacy-absent records do not invent warnings", () => {
  for (const data of [
    undefined,
    {},
    run([]),
    run([call("knowledge", "completed")]),
  ]) {
    const result = getNodeCallDiagnostics(data, completed);
    assert.equal(result.severity, null);
    assert.equal(result.title, "");
    assert.equal(result.hint, "");
  }
  assert.deepEqual(
    nodeCapabilityCalls({ steps: [{ ...completed, payload: { trace: {} } }] }),
    [],
  );
  assert.equal(getNodeCallDiagnostics(run([failed]), undefined).severity, null);
});

test("legacy nested traces retain failures and are not duplicated by run records", () => {
  const step = {
    ...completed,
    payload: {
      skill_id: "legacy",
      status: "completed",
      trace: {
        tool_calls: [
          tool("openalex", "failed"),
          tool("local.search", "completed"),
        ],
      },
    },
  };
  const result = getNodeCallDiagnostics({ steps: [step] }, step);
  assert.equal(result.calls.length, 1);
  assert.equal(result.failures.length, 1);
  assert.equal(result.title, "出现工具调用失败，已更换其它可用工具");
  const current = run([failed], [step]);
  assert.equal(nodeCapabilityCalls(current).length, 1);
  assert.equal(nodeCapabilityCalls(current)[0].skill_id, "literature");
});

test("nested tool failures and blocked skill calls count as failures", () => {
  for (const row of [
    call("knowledge", "completed", [tool("local.search", "failed")]),
    call("knowledge", "blocked", [], { error_code: "permission_denied" }),
    call("knowledge", "unknown", [], { error_code: "tool_failed" }),
  ]) {
    const result = getNodeCallDiagnostics(run([row]), completed);
    assert.equal(result.severity, "warning");
    assert.equal(result.failures.length, 1);
  }
});

test("guidance is actionable without inventing rate limiting from a provider error", () => {
  assert.match(capabilityResolutionHint("provider_unavailable"), /网络连接/);
  assert.doesNotMatch(
    capabilityResolutionHint("provider_unavailable"),
    /429|限流/,
  );
  assert.match(
    capabilityResolutionHint("provider_unavailable", { http_status: 429 }),
    /限流/,
  );
  assert.match(
    capabilityResolutionHint("data_egress_blocked"),
    /保留仅本地资料的外发限制/,
  );
  assert.match(capabilityResolutionHint("mcp_connection_failed"), /MCP 服务页/);
  assert.match(capabilityResolutionHint("unknown"), /查看调用详情/);
});

test("derivation does not mutate persisted records", () => {
  const data = run([
    failed,
    call("knowledge", "completed", [tool("local.search", "completed")]),
  ]);
  const before = JSON.stringify(data);
  getNodeCallDiagnostics(data, completed);
  nodeCapabilityCalls(data);
  assert.equal(JSON.stringify(data), before);
});
