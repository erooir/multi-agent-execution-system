import { test } from "node:test";
import assert from "node:assert/strict";
import {
  capabilityErrorText,
  capabilityStatusText,
  collectToolInput,
  flattenTrace,
  mcpHealthText,
  skillToolSummary,
  skillsForNodeKind,
  toolHealth,
  toolInputFields,
} from "../src/capabilities.ts";

test("capability error codes and statuses map to stable Chinese labels", () => {
  assert.equal(
    capabilityErrorText("data_egress_blocked"),
    "数据外发被策略拦截",
  );
  assert.equal(capabilityErrorText("permission_denied"), "权限不足，已被拦截");
  assert.equal(capabilityErrorText("tool_timeout"), "工具调用超时");
  assert.equal(
    capabilityErrorText("mcp_connection_failed"),
    "MCP 服务连接失败",
  );
  assert.equal(capabilityErrorText("budget_exceeded"), "超出预算限制");
  assert.equal(capabilityErrorText("unknown_code"), "错误码 unknown_code");
  assert.equal(capabilityErrorText(undefined), "未知错误");
  assert.equal(capabilityStatusText("dry_run"), "演练预检（未实际调用）");
  assert.equal(capabilityStatusText("blocked"), "已被策略拦截");
  assert.equal(capabilityStatusText("unavailable"), "暂不可用");
});

test("toolInputFields derives simplified fields from input_schema properties", () => {
  const fields = toolInputFields({
    type: "object",
    properties: {
      query: { type: "string" },
      limit: { type: "integer" },
      semantic: { type: "boolean" },
      document_ids: { type: "array", items: { type: "string" } },
    },
    required: ["query"],
  });
  assert.deepEqual(
    fields.map((f) => [f.name, f.kind, f.required, Boolean(f.array)]),
    [
      ["query", "text", true, false],
      ["limit", "number", false, false],
      ["semantic", "boolean", false, false],
      ["document_ids", "text", false, true],
    ],
  );
  assert.deepEqual(toolInputFields({ type: "object" }), []);
  assert.deepEqual(toolInputFields(undefined), []);
});

test("collectToolInput converts values by field kind and omits empty optional fields", () => {
  const fields = toolInputFields({
    type: "object",
    properties: {
      query: { type: "string" },
      limit: { type: "integer" },
      semantic: { type: "boolean" },
      document_ids: { type: "array", items: { type: "string" } },
      note: { type: "string" },
    },
  });
  const input = collectToolInput(fields, {
    query: " 复合材料 ",
    limit: "8",
    semantic: true,
    document_ids: "doc-1，doc-2, doc-3",
    note: "",
  });
  assert.deepEqual(input, {
    query: "复合材料",
    limit: 8,
    semantic: true,
    document_ids: ["doc-1", "doc-2", "doc-3"],
  });
});

test("skillsForNodeKind keeps only enabled skills compatible with the node kind", () => {
  const skills = [
    {
      id: "knowledge_search",
      enabled: true,
      node_kinds: ["retrieve", "analyze"],
    },
    { id: "document_parse", enabled: true, node_kinds: ["parse"] },
    { id: "ocr", enabled: false, node_kinds: ["parse"] },
    { id: "legacy", node_kinds: ["retrieve"] },
  ];
  assert.deepEqual(
    skillsForNodeKind(skills, "retrieve").map((s) => s.id),
    ["knowledge_search", "legacy"],
  );
  assert.deepEqual(
    skillsForNodeKind(skills, "parse").map((s) => s.id),
    ["document_parse"],
  );
  assert.deepEqual(skillsForNodeKind(skills, "report"), []);
});

test("skillToolSummary counts granted tools and takes the highest egress level", () => {
  const tools = [
    { id: "local.a", data_egress: "none" },
    { id: "local.b", data_egress: "derived" },
    { id: "model.c", data_egress: "raw" },
  ];
  const summary = skillToolSummary(
    { id: "multimodal", allowed_tools: ["local.b", "model.c", "missing"] },
    tools,
  );
  assert.equal(summary.count, 3);
  assert.equal(summary.egress, "raw");
  assert.equal(summary.egressText, "原始数据");
  assert.equal(skillToolSummary({ id: "x" }, tools).egressText, "不外发");
});

test("toolHealth treats local tools as ready and MCP tools follow their server", () => {
  const servers = [{ id: "docling-local", enabled: false }];
  assert.equal(
    toolHealth({ id: "local.a", provider: "local" }, servers).status,
    "ready",
  );
  assert.equal(
    toolHealth({ id: "http.a", provider: "http" }, servers).status,
    "ready",
  );
  const mcpTool = toolHealth(
    { id: "mcp.docling-local.convert_document", provider: "mcp" },
    servers,
  );
  assert.equal(mcpTool.status, "disabled");
  assert.equal(mcpTool.label, "所属 MCP 服务已禁用");
  assert.equal(
    toolHealth({ id: "mcp.missing.tool", provider: "mcp" }, servers).status,
    "unavailable",
  );
  assert.equal(
    toolHealth({ id: "mcp.docling-local.convert_document", provider: "mcp" }, [
      { id: "docling-local", enabled: true },
    ]).status,
    "ready",
  );
});

test("flattenTrace renders skill tool chains and dedupes direct tool traces", () => {
  const skillTrace = {
    tool_id: "",
    provider: "",
    duration_ms: 42,
    tool_calls: [
      {
        tool_id: "local.knowledge.keyword_search",
        provider: "local",
        status: "completed",
        duration_ms: 40,
      },
      {
        tool_id: "local.graph.query",
        provider: "local",
        status: "blocked",
        duration_ms: 2,
      },
    ],
  };
  const rows = flattenTrace(skillTrace);
  assert.equal(rows.length, 2);
  assert.ok(rows.every((r) => r.depth === 0 && r.tool_id.startsWith("local.")));

  const toolTrace = {
    tool_id: "local.ocr.rapidocr",
    tool_version: "1.0.0",
    provider: "local",
    duration_ms: 15,
    tool_calls: [
      {
        tool_id: "local.ocr.rapidocr",
        provider: "local",
        status: "completed",
        duration_ms: 15,
      },
    ],
  };
  const deduped = flattenTrace(toolTrace);
  assert.equal(deduped.length, 1);
  assert.equal(deduped[0].status, "completed");
  assert.equal(deduped[0].duration_ms, 15);

  assert.deepEqual(flattenTrace(null), []);
  assert.deepEqual(flattenTrace({}), []);
});

test("mcpHealthText presents disabled as configuration, not failure", () => {
  assert.deepEqual(mcpHealthText({ status: "disabled", tools: 0 }), {
    status: "disabled",
    label: "已禁用（配置未启用，非故障）",
  });
  assert.equal(
    mcpHealthText({ status: "ready", tools: 3 }).label,
    "连接正常 · 发现 3 个工具",
  );
  assert.equal(
    mcpHealthText({ status: "unavailable", error: "mcp_connection_failed" })
      .label,
    "不可用 · MCP 服务连接失败",
  );
  assert.equal(mcpHealthText(null).status, "unavailable");
});
