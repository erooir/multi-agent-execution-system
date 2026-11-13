import test from "node:test";
import assert from "node:assert/strict";
import {
  isTaskScoped,
  isRestrictedAgent,
  agentScopeLabel,
} from "../src/agentScopeModel.ts";

test("published task snapshots are not presented as reusable assets", () => {
  const agent = { scope: "task", status: "published", current_version: 1 };
  assert.equal(isTaskScoped(agent), true);
  assert.equal(agentScopeLabel(agent), "仅当前任务");
  assert.equal(
    agentScopeLabel({ ...agent, candidate_status: "promoted" }),
    "仅当前任务",
  );
  assert.equal(agentScopeLabel({ ...agent, scope: "project" }), "可复用资产");
  assert.equal(isTaskScoped({ status: "published" }), false);
  assert.equal(
    isRestrictedAgent({ scope: "evaluation", status: "published" }),
    true,
  );
  assert.equal(
    agentScopeLabel({ scope: "evaluation", status: "published" }),
    "仅独立评测",
  );
});
