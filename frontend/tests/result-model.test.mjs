import test from "node:test";
import assert from "node:assert/strict";
import { resultRows, tableColumns, qualityOutputs, elapsedSeconds, displayValue, taskTokenUsage } from "../src/resultModel.ts";

test("任务卡使用网关确认的输入与输出，缺失记录不会显示为零", () => {
  const task = (usage, mode = "cloud") => ({ status: "succeeded", request: { mode }, result: { usage } });
  const complete = { accounting_available: true, accounting_complete: true, input_tokens: 7587, output_tokens: 1087 };
  assert.deepEqual(taskTokenUsage(task(complete)), { value: "8,674", detail: "输入 7,587 · 输出 1,087" });
  assert.deepEqual(taskTokenUsage(task({ ...complete, accounting_complete: false })), {
    value: "待确认", detail: "已确认输入 7,587 · 输出 1,087",
  });
  const zero = { ...complete, accounting_available: false, input_tokens: 0, output_tokens: 0 };
  assert.equal(taskTokenUsage(task(zero, "local")).value, "0");
  assert.equal(taskTokenUsage(task(zero)).value, "待确认");
  assert.equal(taskTokenUsage(task({ ...complete, output_tokens: null })).value, "待确认");
  assert.equal(taskTokenUsage(task(undefined)).value, "未记录");
  assert.equal(taskTokenUsage({ status: "running" }).value, "待汇总");
});

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
