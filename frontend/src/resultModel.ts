import type { Item } from "./types";
import { terminal } from "./domain.ts";

const tokenCount = new Intl.NumberFormat("zh-CN");

export function taskTokenUsage(task: Item) {
  const usage = task.result?.usage;
  if (!usage) {
    return terminal.has(task.status)
      ? { value: "未记录", detail: "任务结果中缺少 Token 用量" }
      : { value: "待汇总", detail: "任务结束后显示输入和输出用量" };
  }
  const { input_tokens: input, output_tokens: output } = usage;
  const mode = task.request?.mode ?? task.mode;
  const localZero = (mode === "local" || mode === "rehearsal") && input === 0 && output === 0;
  if (
    (usage.accounting_available === false && !localZero) ||
    !Number.isSafeInteger(input) || input < 0 ||
    !Number.isSafeInteger(output) || output < 0
  ) {
    return { value: "待确认", detail: "网关用量记录不完整" };
  }
  const detail = `输入 ${tokenCount.format(input)} · 输出 ${tokenCount.format(output)}`;
  if (usage.accounting_complete !== true) {
    return { value: "待确认", detail: "已确认" + detail };
  }
  return { value: tokenCount.format(input + output), detail };
}

export function displayValue(value: unknown): string {
  if (value === null) return "null";
  if (value === undefined) return "—";
  return typeof value === "object" ? JSON.stringify(value) : String(value);
}

export function resultRows(content: unknown): Item[] | null {
  const rows = Array.isArray(content) ? content : (content as Item)?.records;
  return Array.isArray(rows) &&
    rows.every((row) => row && typeof row === "object" && !Array.isArray(row))
    ? rows
    : null;
}

export function tableColumns(rows: Item[]): string[] {
  return [...new Set(rows.flatMap((row) => Object.keys(row)))];
}

export function qualityOutputs(quality: Item | null | undefined): Item[] {
  if (!quality || quality.verdict === "not_evaluated") return [];
  return Array.isArray(quality.outputs) ? quality.outputs : [quality];
}

export function elapsedSeconds(start?: string, end?: string): number | null {
  if (!start || !end) return null;
  const seconds = (Date.parse(end) - Date.parse(start)) / 1000;
  return Number.isFinite(seconds) && seconds >= 0 ? seconds : null;
}

export function durationLabel(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds)) return "暂无样本";
  if (seconds < 60) return `${seconds.toFixed(2)} 秒`;
  return `${Math.floor(seconds / 60)} 分 ${(seconds % 60).toFixed(0)} 秒`;
}
