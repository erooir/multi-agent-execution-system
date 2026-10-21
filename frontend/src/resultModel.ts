import type { Item } from "./types";

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
