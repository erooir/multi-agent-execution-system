import assert from "node:assert/strict";
import test from "node:test";
import {
  graphLayout,
  money,
  parseObject,
  projectItems,
  splitList,
  statusNames,
  taskSummary,
  terminal,
} from "../src/domain.ts";

test("empty task history never creates a success metric", () => {
  assert.deepEqual(taskSummary([]), {
    total: 0,
    active: 0,
    succeeded: 0,
    failed: 0,
    terminal: 0,
  });
  assert.equal(money(null), "—");
  assert.equal(money(undefined), "—");
  assert.equal(money(0), "0.0000");
});
test("partial and cancelled results remain separate from success", () => {
  const values = [
    "accepted",
    "running",
    "succeeded",
    "partial",
    "failed",
    "cancelled",
  ].map((status) => ({ status }));
  assert.deepEqual(taskSummary(values), {
    total: 6,
    active: 2,
    succeeded: 1,
    failed: 1,
    terminal: 4,
  });
  assert.equal(terminal.has("waiting_review"), false);
  assert.equal(statusNames.waiting_review, undefined);
});
test("project view scopes all visible entities by exact project id", () => {
  assert.deepEqual(
    projectItems(
      [
        { id: "a", project_id: "one" },
        { id: "b", project_id: "two" },
        { id: "c" },
      ],
      "one",
    ).map((x) => x.id),
    ["a"],
  );
  assert.deepEqual(projectItems(undefined, "one"), []);
});
test("graph layout follows dependencies, never fabricates sequential edges", () => {
  const nodes = graphLayout([
    { id: "c", depends_on: ["a", "b"] },
    { id: "a", depends_on: [] },
    { id: "b", depends_on: [] },
  ]);
  assert.equal(nodes.find((n) => n.id === "c").position.x, 368);
  assert.equal(nodes.find((n) => n.id === "a").position.x, 0);
  assert.equal(nodes.find((n) => n.id === "b").position.x, 0);
  assert.notEqual(
    nodes.find((n) => n.id === "a").position.y,
    nodes.find((n) => n.id === "b").position.y,
  );
});
test("runtime node ids are used instead of database record ids", () => {
  const nodes = graphLayout([
    { id: "record1", node_id: "parse" },
    { id: "record2", node_id: "review", depends_on: ["parse"] },
  ]);
  assert.equal(nodes[1].position.x, 368);
  assert.equal(nodes[1].graphId, "review");
});
test("malformed dependency cycles cannot hang the rendering process", () => {
  assert.equal(
    graphLayout([
      { id: "a", depends_on: ["b"] },
      { id: "b", depends_on: ["a"] },
    ]).length,
    2,
  );
});
test("configuration JSON rejects arrays/null, member lists normalize whitespace", () => {
  assert.throws(() => parseObject("[]"));
  assert.throws(() => parseObject("null"));
  assert.deepEqual(parseObject('{"max_chars":100}'), { max_chars: 100 });
  assert.deepEqual(splitList("one， two\none,,two"), ["one", "two"]);
});
