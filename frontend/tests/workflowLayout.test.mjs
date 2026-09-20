import { test } from "node:test";
import assert from "node:assert/strict";
import { layoutWorkflow } from "../src/workflowLayout.ts";

const makeNode = (id, y = 0) => ({
  id,
  position: { x: 0, y },
  data: { label: id },
});

test("a long research workflow remains left-to-right without wrapping rows", () => {
  const nodes = Array.from({ length: 12 }, (_, i) => makeNode(String(i)));
  const edges = nodes
    .slice(1)
    .map((n, i) => ({ source: String(i), target: n.id }));
  const result = layoutWorkflow(nodes, edges);
  for (let i = 1; i < result.length; i++)
    assert.ok(result[i].position.x > result[i - 1].position.x);
  assert.equal(new Set(result.map((n) => n.position.y)).size, 1);
  assert.deepEqual(nodes[0].position, { x: 0, y: 0 });
  assert.equal(result[0].data, nodes[0].data);
});

test("parallel branches stay apart and merge only after all predecessor layers", () => {
  const nodes = [
    makeNode("start"),
    makeNode("left"),
    makeNode("right", 10),
    makeNode("extra"),
    makeNode("end"),
  ];
  const edges = [
    { source: "start", target: "left" },
    { source: "start", target: "right" },
    { source: "right", target: "extra" },
    { source: "extra", target: "end" },
    { source: "left", target: "end" },
  ];
  const result = new Map(layoutWorkflow(nodes, edges).map((n) => [n.id, n]));
  assert.equal(result.get("left").position.x, result.get("right").position.x);
  assert.notEqual(
    result.get("left").position.y,
    result.get("right").position.y,
  );
  for (const edge of edges)
    assert.ok(
      result.get(edge.target).position.x > result.get(edge.source).position.x,
    );
});

test("cycles and dangling edges fail without altering a manually positioned canvas", () => {
  const nodes = [makeNode("a", 20), makeNode("b", 120)];
  const snapshot = structuredClone(nodes);
  assert.throws(
    () =>
      layoutWorkflow(nodes, [
        { source: "a", target: "b" },
        { source: "b", target: "a" },
      ]),
    /循环/,
  );
  assert.throws(
    () => layoutWorkflow(nodes, [{ source: "a", target: "missing" }]),
    /已删除节点/,
  );
  assert.deepEqual(nodes, snapshot);
});

test("two condition edges with the same destination do not create a false cycle", () => {
  const nodes = [makeNode("condition"), makeNode("end")];
  const result = layoutWorkflow(nodes, [
    { source: "condition", target: "end" },
    { source: "condition", target: "end" },
  ]);
  assert.ok(result[1].position.x > result[0].position.x);
});
