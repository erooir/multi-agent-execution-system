import assert from "node:assert/strict";
import test from "node:test";
import {
  changedVersionFields,
  connectWorkflow,
  removeWorkflowNode,
  workflowIssues,
} from "../src/workflowEditorModel.ts";

const agents = [
  { id: "table", status: "published", capabilities: ["structured_transform"] },
];
const node = (id, depends_on = []) => ({
  id,
  label: id,
  agent_id: "table",
  depends_on,
  config: {},
});

test("workflow authoring connects parallel branches without fabricating order", () => {
  const original = [node("a"), node("b"), node("c")];
  const connected = connectWorkflow(
    connectWorkflow(original, "a", "c"),
    "b",
    "c",
  );
  assert.deepEqual(connected[0].depends_on, []);
  assert.deepEqual(connected[1].depends_on, []);
  assert.deepEqual(connected[2].depends_on, ["a", "b"]);
  assert.deepEqual(original[2].depends_on, []);
  assert.deepEqual(
    workflowIssues(connected, agents, "structured_transform"),
    [],
  );
});

test("long cycle, self-link and missing endpoint are rejected before saving", () => {
  const graph = [node("a"), node("b", ["a"]), node("c", ["b"])];
  assert.throws(() => connectWorkflow(graph, "c", "a"), /循环/);
  assert.throws(() => connectWorkflow(graph, "a", "a"), /自身/);
  assert.throws(() => connectWorkflow(graph, "missing", "a"), /现有节点/);
  assert.deepEqual(connectWorkflow(graph, "a", "b")[1].depends_on, ["a"]);
});

test("removing a node removes every incoming dependency and preserves other configs", () => {
  const graph = [
    node("a"),
    node("b", ["a"]),
    { ...node("c", ["a", "b"]), config: { max_chars: 900 } },
  ];
  const removed = removeWorkflowNode(graph, "a");
  assert.deepEqual(
    removed.map((item) => item.depends_on),
    [[], ["b"]],
  );
  assert.deepEqual(removed[1].config, { max_chars: 900 });
  assert.deepEqual(graph[2].depends_on, ["a", "b"]);
});

test("published availability, task capability and broken imports surface actionable issues", () => {
  assert.match(
    workflowIssues([], agents, "structured_transform")[0],
    /至少一个/,
  );
  assert.match(
    workflowIssues([node("a")], [], "structured_transform").join(),
    /已发布/,
  );
  assert.match(
    workflowIssues([node("a")], agents, "source_summary").join(),
    /未声明/,
  );
  assert.match(
    workflowIssues(
      [node("a", ["missing"])],
      agents,
      "structured_transform",
    ).join(),
    /不存在/,
  );
  assert.match(
    workflowIssues(
      [node("a", ["b"]), node("b", ["a"])],
      agents,
      "structured_transform",
    ).join(),
    /循环/,
  );
});

test("version comparison ignores bookkeeping but retains tool and member version differences", () => {
  const before = {
    id: "a:1",
    version: 1,
    parameters: { sort_by: "name", descending: false },
    tool_refs: [{ tool_id: "lookup", version: 1 }],
    members: [{ agent_id: "child", version: 1 }],
  };
  assert.deepEqual(
    changedVersionFields(before, {
      ...before,
      id: "a:2",
      version: 2,
      parameters: { descending: false, sort_by: "name" },
    }),
    [],
  );
  assert.deepEqual(
    changedVersionFields(before, {
      ...before,
      tool_refs: [{ tool_id: "lookup", version: 2 }],
      members: [{ agent_id: "child", version: 2 }],
    }),
    ["tool_refs", "members"],
  );
});
