import test from "node:test";
import assert from "node:assert/strict";
import { resultRows, tableColumns, qualityOutputs, elapsedSeconds, displayValue } from "../src/resultModel.ts";

test("heterogeneous result tables keep later fields, false and zero", () => {
  const rows = resultRows({ records: [{ a: 0 }, { b: false }] });
  assert.deepEqual(tableColumns(rows), ["a", "b"]);
  assert.equal(displayValue(false), "false");
  assert.equal(displayValue(0), "0");
  assert.equal(resultRows({ records: [null] }), null);
  assert.equal(resultRows({ text: "ordinary text" }), null);
});
test("absence and not-evaluated status cannot become a passed review", () => {
  assert.deepEqual(qualityOutputs(null), []);
  assert.deepEqual(qualityOutputs({ verdict: "not_evaluated" }), []);
  assert.deepEqual(qualityOutputs({ outputs: [{ passed: false }] }), [{ passed: false }]);
});
test("elapsed duration rejects absent, malformed or reversed timestamps", () => {
  assert.equal(elapsedSeconds("2026-10-21T01:00:00Z", "2026-10-21T01:00:05Z"), 5);
  assert.equal(elapsedSeconds("invalid", "2026-10-21T01:00:05Z"), null);
  assert.equal(elapsedSeconds("2026-10-21T01:00:06Z", "2026-10-21T01:00:05Z"), null);
  assert.equal(elapsedSeconds("2026-10-21T01:00:00Z"), null);
});
