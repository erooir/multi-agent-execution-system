import { createElement, useState } from "react";
import type { Item } from "./types";
import { callRound, findFindingCall } from "./taskTraceModel";
import { callError } from "./TaskToolTrace";

const labels: Record<string, string> = {
  observations: "观察记录",
  report_window: "报告时间窗",
  context: "上下文",
  monthly: "月度统计",
  trend: "趋势",
  reconciliation: "数值复核",
  clause_map: "条款对照",
  matrix: "证据矩阵",
  timeline: "时间线",
  annual_series: "逐年序列",
  anomaly: "距平及排名",
  trend_svg: "趋势图",
  od_comparison: "路径对照",
  route_layer: "路线图层",
  exclusions: "排除项",
  notice_audit: "限制依据",
  limitations: "适用限制",
};
const svgTags = new Set([
  "svg",
  "g",
  "title",
  "path",
  "rect",
  "line",
  "polyline",
  "polygon",
  "circle",
  "text",
  "image",
]);
function plot(value: string) {
  if (value.length > 2_000_000) return null;
  const root = new DOMParser().parseFromString(
    value,
    "image/svg+xml",
  ).documentElement;
  if (root.localName !== "svg" || root.querySelectorAll("*").length > 4000)
    return null;
  function shape(el: Element, i: number): any {
    if (!svgTags.has(el.localName)) return null;
    const props: Record<string, any> = { key: i };
    for (const { name, value: v } of Array.from(el.attributes)) {
      if (
        [
          "x",
          "y",
          "x1",
          "x2",
          "y1",
          "y2",
          "cx",
          "cy",
          "r",
          "width",
          "height",
          "viewBox",
          "points",
          "stroke-width",
          "font-size",
        ].includes(name) &&
        /^[-+\d.eE, %]+$/.test(v)
      )
        props[
          (
            {
              "stroke-width": "strokeWidth",
              "font-size": "fontSize",
            } as Record<string, string>
          )[name] || name
        ] = v;
      if (name === "d" && /^[MmZzLlHhVvCcSsQqTtAaEe0-9+.,\s-]+$/.test(v))
        props.d = v;
      if (
        ["fill", "stroke"].includes(name) &&
        /^(#[A-Fa-f\d]{3,8}|none|black|white|red|gray)$/.test(v)
      )
        props[name] = v;
      if (
        el.localName === "image" &&
        name === "href" &&
        /^data:image\/png;base64,[A-Za-z0-9+/=]+$/.test(v)
      )
        props.href = v;
    }
    return createElement(
      el.localName,
      props,
      ...Array.from(el.childNodes).map((c, j) =>
        c.nodeType === 1
          ? shape(c as Element, j)
          : c.nodeType === 3
            ? c.textContent
            : null,
      ),
    );
  }
  return shape(root, 0);
}

function Product({ value }: { value: any }) {
  const [page, setPage] = useState(0);
  if (typeof value === "string")
    return value.startsWith("<svg") ? (
      <figure>{plot(value)}</figure>
    ) : (
      <p>{value}</p>
    );
  if (
    Array.isArray(value) &&
    value.length &&
    value.every((v) => v && typeof v === "object" && !Array.isArray(v))
  ) {
    const keys = Array.from(new Set(value.flatMap(Object.keys)));
    return (
      <>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                {keys.map((k) => (
                  <th key={k}>{k}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {value.slice(page * 10, page * 10 + 10).map((r, i) => (
                <tr key={i}>
                  {keys.map((k) => (
                    <td key={k}>
                      {typeof r[k] === "object"
                        ? JSON.stringify(r[k])?.slice(0, 500)
                        : String(r[k] ?? "—")}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="toolbar">
          <small>
            共 {value.length} 行 · 第 {page + 1} 页
          </small>
          <button disabled={!page} onClick={() => setPage(page - 1)}>
            上页
          </button>
          <button
            disabled={(page + 1) * 10 >= value.length}
            onClick={() => setPage(page + 1)}
          >
            下页
          </button>
        </div>
      </>
    );
  }
  if (value?.type === "FeatureCollection")
    return <Product value={value.features} />;
  return <pre>{JSON.stringify(value, null, 2)?.slice(0, 15000)}</pre>;
}

export default function CaseReport({
  content,
  calls = [],
  onCall,
}: {
  content: Item;
  calls?: Item[];
  onCall?: (id: string) => void;
}) {
  return (
    <section className="case-products">
      <small>
        协作分析产物 · {content.case_id} ·{" "}
        {content.scope === "sample"
          ? "缩小样本验证"
          : content.scope === "project"
            ? "本次问题与项目资料"
          : content.scope === "reference"
            ? "参考批次"
            : "历史产物，未记录验收范围"}
      </small>
      <h3>{content.title}</h3>
      {content.scope === "reference" && (
        <p className="muted">
          {content.reference_batch?.registered
            ? "冻结来源清单版本 " + content.reference_batch.version
            : "未核实参考批次清单"}
        </p>
      )}
      {content.gaps?.length > 0 && (
        <div className="warning">
          <strong>材料或判定缺口</strong>
          <ul>
            {content.gaps.map((g: string, i: number) => (
              <li key={i}>{g}</li>
            ))}
          </ul>
        </div>
      )}
      {Object.entries(content.products || {}).map(([k, v], i) => (
        <details key={k} open={i === 0}>
          <summary>{labels[k] || k}</summary>
          <Product value={v} />
        </details>
      ))}
      <details>
        <summary>来源证据 · {content.evidence?.length || 0} 条</summary>
        <Product value={content.evidence} />
      </details>
      {content.review?.length > 0 && (
        <details>
          <summary>
            独立复核 · {content.review.filter((r: Item) => r.passed).length}/
            {content.review.length} 项通过
          </summary>
          <ul>
            {content.review.map((r: Item, i: number) => (
              <li key={i}>
                {r.passed ? "通过" : r.hard ? "硬性不一致" : "待补证"} ·{" "}
                {r.name}
                {!r.passed && r.message ? " · " + r.message : ""}
              </li>
            ))}
          </ul>
        </details>
      )}
      {content.reading_aids?.length > 0 && (
        <details>
          <summary>机译阅读索引（以原件为准）</summary>
          <Product value={content.reading_aids} />
        </details>
      )}
      {content.reading_notes?.length > 0 && (
        <details className="warning">
          <summary>未采用的译注 · {content.reading_notes.length} 处</summary>
          <ul>
            {content.reading_notes.map((note: string, i: number) => (
              <li key={i}>{note}</li>
            ))}
          </ul>
        </details>
      )}
      {content.tool_findings?.length > 0 && (
        <details>
          <summary>
            注册工具的实际观测 · {content.tool_findings.length} 次
          </summary>
          <p className="muted">
            按任务实际读取的来源、页面和区间展示。未使用的授权工具不列入此处。
          </p>
          {content.tool_findings.map((item: Item, i: number) => {
            const call = findFindingCall(item, calls);
            return (
              <details
                key={item.tool_call_id || i}
                className="trace-observation"
              >
                <summary>
                  {item.tool_id} · v{item.version} ·{" "}
                  {item.status === "completed"
                    ? "已完成"
                    : item.status === "unavailable"
                      ? "未取得结果"
                      : "未记录状态"}
                  {call && " · " + callRound(call.revision)}
                </summary>
                {item.error_code && (
                  <p className="warning">{callError(item.error_code)}</p>
                )}
                {item.result != null && <Product value={item.result} />}
                {call && onCall ? (
                  <button onClick={() => onCall(call.id)}>追溯实际调用</button>
                ) : (
                  <p className="muted">
                    {item.tool_call_id
                      ? "当前任务中未找到与本条工具观测一致的调用记录。"
                      : "这条观测没有记录调用 ID。"}
                  </p>
                )}
              </details>
            );
          })}
        </details>
      )}
      <p className="muted">完整字段、图层与全部记录随 JSON 产物下载。</p>
    </section>
  );
}
