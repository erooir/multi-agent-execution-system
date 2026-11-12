import { useId } from "react";
import type { AnswerTable } from "./QuestionProducts";

// A view of delivered cells, never a separately computed or cached case answer.
export default function MonthlyResultChart({
  tables = [],
}: {
  tables?: AnswerTable[];
}) {
  const pattern = useId().replace(/:/g, "");
  const table = tables.find(
    (t) =>
      ["月份", "死亡人数", "受伤人数"].every((name) =>
        t.columns.includes(name),
      ) &&
      t.rows.length > 1 &&
      t.rows.length <= 36 &&
      t.rows.length === t.total_rows,
  );
  if (!table) return null;
  const indexes = ["月份", "死亡人数", "受伤人数", "覆盖"].map((name) =>
    table.columns.indexOf(name),
  );
  const rows = table.rows.map((row) => ({
    month: String(row[indexes[0]] ?? ""),
    first: row[indexes[1]],
    second: row[indexes[2]],
    partial: indexes[3] >= 0 && String(row[indexes[3]]).includes("部分"),
  }));
  if (
    rows.some(
      (r) =>
        typeof r.first !== "number" ||
        typeof r.second !== "number" ||
        !Number.isFinite(r.first) ||
        !Number.isFinite(r.second) ||
        r.first < 0 ||
        r.second < 0,
    )
  )
    return null;
  const maximum = Math.max(
    ...rows.flatMap((r) => [r.first as number, r.second as number]),
  );
  if (!maximum) return null;
  const width = Math.max(640, rows.length * 43 + 66),
    height = 255,
    left = 48,
    top = 20,
    plot = 170,
    gap = (width - left - 16) / rows.length;
  return (
    <figure className="taskcase-monthly-chart">
      <figcaption>
        逐月记录对照 <small>单位：人</small>
      </figcaption>
      <div className="taskcase-chart-legend">
        <span className="deaths">死亡人数</span>
        <span className="injured">受伤人数</span>
        {rows.some((r) => r.partial) && <span>斜线背景：部分月份</span>}
      </div>
      <div
        className="taskcase-chart-scroll"
        role="region"
        aria-label="逐月记录图，可横向滚动"
        tabIndex={0}
      >
        <svg
          viewBox={`0 0 ${width} ${height}`}
          style={{ minWidth: Math.min(width, 760) }}
          role="img"
          aria-label="按交付表格绘制的逐月死亡与受伤人数，精确数值见下方附表"
        >
          <defs>
            <pattern
              id={pattern}
              width="7"
              height="7"
              patternUnits="userSpaceOnUse"
              patternTransform="rotate(45)"
            >
              <line
                x1="0"
                y1="0"
                x2="0"
                y2="7"
                stroke="#d4d2e8"
                strokeWidth="2"
              />
            </pattern>
          </defs>
          {[0, 0.25, 0.5, 0.75, 1].map((fraction) => {
            const y = top + plot * (1 - fraction);
            return (
              <g key={fraction}>
                <line
                  x1={left}
                  y1={y}
                  x2={width - 16}
                  y2={y}
                  stroke="#e9e8f1"
                />
                <text
                  x={left - 8}
                  y={y + 4}
                  textAnchor="end"
                  fill="#72788d"
                  fontSize="10"
                >
                  {Math.round(maximum * fraction).toLocaleString("zh-CN")}
                </text>
              </g>
            );
          })}
          {rows.map((r, index) => {
            const x = left + gap * index,
              bar = Math.min(13, gap * 0.25);
            return (
              <g key={r.month + index}>
                <title>
                  {r.month}：死亡 {r.first} 人，受伤 {r.second} 人
                  {r.partial ? "（部分月份）" : ""}
                </title>
                {r.partial && (
                  <rect
                    x={x + 2}
                    y={top}
                    width={gap - 4}
                    height={plot}
                    fill={`url(#${pattern})`}
                    opacity=".6"
                  />
                )}
                <rect
                  x={x + gap / 2 - bar - 1}
                  y={top + plot * (1 - (r.first as number) / maximum)}
                  width={bar}
                  height={(plot * (r.first as number)) / maximum}
                  rx="2"
                  fill="#625bd7"
                />
                <rect
                  x={x + gap / 2 + 1}
                  y={top + plot * (1 - (r.second as number) / maximum)}
                  width={bar}
                  height={(plot * (r.second as number)) / maximum}
                  rx="2"
                  fill="#57a993"
                />
                <text
                  x={x + gap / 2}
                  y={top + plot + 20}
                  textAnchor="middle"
                  fill="#515a72"
                  fontSize="10"
                >
                  {r.month.replace(/^20/, "")}
                </text>
              </g>
            );
          })}
        </svg>
      </div>
      <p>
        图形直接使用本次交付表中的数值。部分月份不能与完整月份直接比较；记录人数不代表实际发生人数的完整统计。
      </p>
    </figure>
  );
}
