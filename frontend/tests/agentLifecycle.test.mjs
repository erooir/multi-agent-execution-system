import { test } from "node:test";
import assert from "node:assert/strict";
import {
  agentAvailable,
  agentOptionLabel,
  filterAgents,
  lifecycleStatus,
  lifecycleEventDetail,
} from "../src/agentLifecycle.ts";

test("tombstones and disabled records never become selectable through compatibility fields", () => {
  assert.equal(
    lifecycleStatus({ lifecycle_status: "destroyed", enabled: true }),
    "destroyed",
  );
  assert.equal(
    agentAvailable({ lifecycle_status: "destroyed", enabled: true }),
    false,
  );
  assert.equal(
    agentAvailable({ lifecycle_status: "disabled", enabled: true }),
    false,
  );
  assert.equal(agentAvailable({ enabled: false }), false);
  assert.equal(agentAvailable(undefined), false);
  assert.equal(agentAvailable({ enabled: true }), true);
});

test("event summaries retain the historical snapshot limitation and clone source", () => {
  assert.equal(
    lifecycleEventDetail({ version: 3, notice: "此前配置版本未知" }),
    "配置 v3 · 此前配置版本未知",
  );
  assert.equal(
    lifecycleEventDetail({ source_name: "航空检索", source_version: 2 }),
    "源自 航空检索 · v2",
  );
  assert.match(
    lifecycleEventDetail({ reason: "waiting_review" }),
    /等待人工审核/,
  );
});

test("default directory hides tombstones while all and status search keep them inspectable", () => {
  const agents = [
    { id: "a", name: "航空研究", enabled: true },
    { id: "b", name: "检索", enabled: false },
    { id: "c", name: "航空旧版", lifecycle_status: "destroyed" },
  ];
  assert.deepEqual(
    filterAgents(agents, "available", "").map((a) => a.id),
    ["a", "b"],
  );
  assert.deepEqual(
    filterAgents(agents, "all", " 航空 ").map((a) => a.id),
    ["a", "c"],
  );
  assert.deepEqual(
    filterAgents(agents, "destroyed", "C").map((a) => a.id),
    ["c"],
  );
});

test("workflow bindings explain version, cross-workflow reuse, and unavailable state", () => {
  assert.equal(
    agentOptionLabel({
      name: "检索",
      version: 3,
      usage: { workflow_count: 2 },
      enabled: false,
    }),
    "检索 · v3 · 2 个流程 · 已停用",
  );
  assert.equal(
    agentOptionLabel({ name: "研究", lifecycle_status: "destroyed" }),
    "研究 · v1 · 0 个流程 · 已销毁",
  );
});
