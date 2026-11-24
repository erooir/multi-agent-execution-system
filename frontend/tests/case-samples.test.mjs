import assert from "node:assert/strict";
import test from "node:test";
import { verifiedCaseSamples } from "../src/caseSamples.ts";

const now = Date.parse("2026-11-24");
const asset = { id: "data", project_id: "p", status: "active", classification: "public", expires_at: "2027-01-01" };
const task = {
  id: "old", project_id: "p", status: "succeeded", finished_at: "2026-11-23T12:00:00Z",
  quality_verdict: { passed: true }, result: { usage: { request_count: 4 } },
  request: { task_type: "case_analysis", case_id: "G03", case_scope: "sample", mode: "cloud", input_refs: ["data"] },
};
const select = (tasks, assets = [asset]) => verifiedCaseSamples("p", tasks, assets, now).get("G03");

test("select the most recent reviewed sample with actual model usage and retrievable inputs", () => {
  const latest = { ...task, id: "new", finished_at: "2026-11-24T06:00:00Z" };
  assert.equal(select([latest, task]), latest);
  assert.equal(select([task, latest]), latest);
});

test("only reusable samples with explicit public inputs from this project are offered", () => {
  for (const changes of [
    { project_id: "other" }, { task_case_id: "upstream-task" }, { deleted_at: "2026-11-24" },
    { status: "partial" }, { quality_verdict: { passed: false } }, { result: { usage: { request_count: 0 } } },
  ]) assert.equal(select([{ ...task, ...changes }]), undefined);
  for (const changes of [
    { mode: "local" }, { case_scope: "reference" }, { use_project_knowledge: true },
    { input_refs: [] }, { input_refs: ["data", "unavailable"] },
  ]) assert.equal(select([{ ...task, request: { ...task.request, ...changes } }]), undefined);
  for (const changes of [
    { status: "revoked" }, { classification: "local" }, { project_id: "other" },
    { task_case_id: "bound" }, { is_file_index: true }, { expires_at: "2025-01-01" },
  ]) assert.equal(select([task], [{ ...asset, ...changes }]), undefined);
});
