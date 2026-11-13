import test from "node:test";
import assert from "node:assert/strict";
import {
  taskCaseCanStart,
  taskCaseActive,
  taskCaseTerminal,
  bytesLabel,
  citationSourceUrl,
  taskCaseEditable,
} from "../src/taskCases.ts";

test("data-free ready tasks start only within the current cloud authorization", () => {
  const task = {
    status: "ready",
    mode: "cloud",
    missing: [],
    public_egress_authorized: false,
  };
  assert.equal(taskCaseCanStart(task), false);
  assert.equal(
    taskCaseCanStart({ ...task, public_egress_authorized: true }),
    true,
  );
  assert.equal(taskCaseCanStart({ ...task, mode: "local" }), true);
  assert.equal(
    taskCaseCanStart({ ...task, mode: "local", missing: ["Missing coverage"] }),
    false,
  );
  assert.equal(
    taskCaseCanStart({ ...task, mode: "local", status: "awaiting_data" }),
    false,
  );
  assert.equal(
    taskCaseCanStart({ ...task, mode: "local", status: "succeeded" }),
    false,
  );
});

test("source citation opens only the authenticated task source route and keeps the real page", () => {
  assert.equal(
    citationSourceUrl({
      source_url: "/api/v2/task-cases/case/sources/file/content",
      page: 3,
    }),
    "/api/v2/task-cases/case/sources/file/content#page=3",
  );
  assert.equal(
    citationSourceUrl({ source_url: "javascript:alert(1)", page: 1 }),
    null,
  );
  assert.equal(
    citationSourceUrl({ source_url: "https://untrusted.example/source" }),
    null,
  );
  assert.equal(
    citationSourceUrl({
      source_url: "/api/v2/task-cases/case/sources/file/content",
      page: -1,
    }),
    "/api/v2/task-cases/case/sources/file/content",
  );
});

test("awaiting user data does not appear as an active execution or completed result", () => {
  assert.equal(taskCaseActive.has("awaiting_data"), false);
  assert.equal(taskCaseTerminal.has("awaiting_data"), false);
  assert.equal(taskCaseActive.has("revising"), true);
  assert.equal(taskCaseTerminal.has("partial"), true);
});

test("unsuccessful terminal runs can accept a new submission while success stays frozen", () => {
  for (const status of ["failed", "partial", "cancelled"])
    assert.equal(taskCaseEditable.has(status), true);
  for (const status of ["succeeded", "running", "analyzing_requirements"])
    assert.equal(taskCaseEditable.has(status), false);
});

test("source size keeps missing different from a real empty file", () => {
  assert.equal(bytesLabel(undefined), "");
  assert.equal(bytesLabel(0), "0 B");
  assert.equal(bytesLabel(1536), "1.5 KB");
});
