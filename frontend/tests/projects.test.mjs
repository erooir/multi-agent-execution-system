import assert from "node:assert/strict";
import test from "node:test";
import { projectLabel } from "../src/projects.ts";

test("unique names stay concise; duplicate names show a stable project identifier", () => {
  const projects = [
    { id: "b04847cf445f44c7", name: "我的工作空间" },
    { id: "074ee62371194ecb", name: "我的工作空间" },
    { id: "a", name: "研究项目" },
  ];
  assert.equal(projectLabel(projects[0], projects), "我的工作空间 · b04847cf");
  assert.equal(projectLabel(projects[1], projects), "我的工作空间 · 074ee623");
  assert.equal(projectLabel(projects[2], projects), "研究项目");
  const renamed = projects.map((p, i) =>
    i === 1 ? { ...p, name: "资料归档" } : p,
  );
  assert.equal(projectLabel(renamed[0], renamed), "我的工作空间");
});

test("identical short prefixes remain distinguishable regardless of list order", () => {
  const projects = [
    { id: "12345678a0", name: "同名" },
    { id: "12345678a1", name: "同名" },
    { id: "12345678", name: "同名" },
  ];
  const labels = projects.map((p) => projectLabel(p, projects));
  assert.equal(new Set(labels).size, 3);
  assert.deepEqual(
    projects.map((p) => projectLabel(p, [...projects].reverse())),
    labels,
  );
});
