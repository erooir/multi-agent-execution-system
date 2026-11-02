import assert from "node:assert/strict";
import test from "node:test";
import {
  callContext,
  findFindingCall,
  roundNodes,
} from "../src/taskTraceModel.ts";

test("报告按实际 ID、版本、结果摘要和错误关联调用，旧轮次不会互相替代", () => {
  const call = {
    id: "second",
    capability_id: "evidence.pdf_page",
    version: "1",
    status: "completed",
    result_checksum: "second-sha",
  };
  const calls = [{ ...call, id: "first", result_checksum: "first-sha" }, call];
  const finding = {
    tool_id: call.capability_id,
    tool_call_id: call.id,
    version: 1,
    status: "completed",
    result_checksum: "second-sha",
  };
  assert.equal(findFindingCall(finding, calls), call);
  for (const change of [
    { tool_call_id: null },
    { version: 2 },
    { result_checksum: null },
    { result_checksum: "first-sha" },
    { tool_id: "other" },
  ])
    assert.equal(findFindingCall({ ...finding, ...change }, calls), undefined);
  const failed = {
    ...call,
    status: "failed",
    result_checksum: null,
    error_code: "unsupported_tool_input",
  };
  const unavailable = {
    ...finding,
    status: "unavailable",
    result_checksum: null,
    error_code: failed.error_code,
  };
  assert.equal(findFindingCall(unavailable, [failed]), failed);
  assert.equal(
    findFindingCall({ ...unavailable, error_code: "changed" }, [failed]),
    undefined,
  );
});

test("流程调整可追溯原轮与复用节点，工具标签取自实际节点的冻结版本", () => {
  const prepare = { id: "original", node_id: "prepare", attempt: 1 };
  const analyze = { id: "previous", node_id: "analyze", attempt: 1 };
  const adjusted = {
    id: "adjusted",
    node_id: "analyze",
    attempt: 2,
    instance_id: "second-instance",
    agent_id: "analyst",
    agent_version: 2,
  };
  const task = {
    nodes: [prepare, analyze, adjusted],
    repair_history: [
      {
        decision: "revise",
        revision: 1,
        reused_nodes: { prepare: prepare.id },
      },
    ],
    snapshot: {
      agents: [
        {
          agent_id: "analyst",
          version: 2,
          runtime_bindings: {
            tools: [{ id: "tool", version: 1, label: "冻结的名称" }],
          },
        },
      ],
    },
  };
  assert.deepEqual(roundNodes(task, 0), [prepare, analyze]);
  assert.deepEqual(roundNodes(task, 1), [
    adjusted,
    { ...prepare, reusedFromAttempt: 1 },
  ]);
  const call = {
    id: "call",
    kind: "tool",
    revision: 1,
    capability_id: "tool",
    version: "1",
    instance_id: adjusted.instance_id,
  };
  assert.equal(callContext(task, call).label, "冻结的名称");
  assert.equal(callContext(task, { ...call, version: "3" }).label, "tool");
  assert.equal(
    callContext(task, { ...call, instance_id: "missing", node_id: "analyze" })
      .node,
    undefined,
  );
});
