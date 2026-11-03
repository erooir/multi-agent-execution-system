import assert from "node:assert/strict";
import test from "node:test";
import {
  catalogMatches,
  eligibleAgents,
  toolGroup,
} from "../src/toolCatalogModel.ts";

test("目录按部署条目的实际标识分类，检索覆盖中文分组、操作名和工具标识", () => {
  const tool = {
    id: "series.baseline_anomaly",
    name: "baseline_anomaly",
    label: "基线异常",
  };
  assert.equal(toolGroup(tool), "analysis");
  assert.equal(toolGroup({ id: "data.read_source" }), "sources");
  assert.equal(toolGroup({ id: "evidence.pdf_page" }), "evidence");
  assert.equal(toolGroup({ id: "project.query", kind: "mcp" }), "mcp");
  assert.equal(toolGroup({ id: "external.example" }), "other");
  assert.equal(catalogMatches(tool, "统计 BASELINE", "统计与空间分析"), true);
  assert.equal(catalogMatches(tool, "MCP", "统计与空间分析"), false);
});

test("当前发布、同项目、智能体许可和匹配的冻结摘要缺一不可", () => {
  const item = { id: "geo.overlay_area", version: 1, checksum: "current" };
  const base = {
    id: "authorized",
    project_id: "p",
    status: "published",
    current_version: 2,
    role_contract: { grants: [item.id] },
    tool_refs: [{ tool_id: item.id, version: 1 }],
    runtime_bindings: { tools: [item] },
  };
  const candidates = [
    base,
    ...[
      { status: "draft" },
      { status: "disabled" },
      { project_id: "other" },
      { current_version: 0 },
      { role_contract: { grants: [] } },
      { tool_refs: [] },
      { tool_refs: [{ tool_id: item.id, version: 2 }] },
      { runtime_bindings: { tools: [{ ...item, checksum: "old" }] } },
      { runtime_bindings: { tools: [{ ...item, version: 2 }] } },
      { runtime_bindings: undefined },
    ].map((override) => ({ ...base, id: "excluded", ...override })),
  ];
  assert.deepEqual(eligibleAgents(candidates, "p", item, "tools"), [base]);
  assert.deepEqual(
    eligibleAgents(candidates, "p", { ...item, checksum: undefined }, "tools"),
    [],
  );
});

test("文档技能包按版本匹配持有智能体，其依赖不自动成为智能体工具授权", () => {
  const skill = {
    id: "evidence-analysis",
    version: "1.0.0",
    checksum: "s",
    required_tools: ["list_sources"],
  };
  const agent = {
    project_id: "p",
    status: "published",
    current_version: 1,
    skill_refs: [{ skill_id: skill.id, version: skill.version }],
    runtime_bindings: { skills: [skill] },
  };
  assert.deepEqual(eligibleAgents([agent], "p", skill, "skills"), [agent]);
  assert.deepEqual(eligibleAgents([agent], "p", skill, "tools"), []);
});
