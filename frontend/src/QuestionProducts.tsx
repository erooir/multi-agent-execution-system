import { useMemo } from "react";
import { Download } from "lucide-react";
import { mapDrawing } from "./questionMap";

export type AnswerTable = {
  title: string;
  columns: string[];
  rows: (string | number | boolean | null)[][];
  total_rows: number;
};
export type AnswerMap = {
  title: string;
  geojson: Parameters<typeof mapDrawing>[0];
};

function MapResult({ map }: { map: AnswerMap }) {
  const { marks, exceeded } = useMemo(
    () => mapDrawing(map.geojson),
    [map.geojson],
  );
  function download() {
    const link = document.createElement("a");
    const url = URL.createObjectURL(
      new Blob([JSON.stringify(map.geojson)], { type: "application/geo+json" }),
    );
    link.href = url;
    link.download =
      (map.title
        .replace(/[^\p{L}\p{N} _-]/gu, "")
        .slice(0, 70)
        .trim() || "空间结果") + ".geojson";
    link.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  return (
    <details className="answer-product">
      <summary>{map.title} · 地图</summary>
      {marks.length ? (
        <svg
          viewBox="0 0 560 212"
          role="img"
          aria-label={map.title + "，按资料原坐标绘制"}
          className="answer-map"
        >
          {marks.map((mark, i) => (
            <g key={i}>
              {mark.name && <title>{mark.name}</title>}
              {mark.kind === "point" ? (
                mark.parts
                  .flat()
                  .map(([x, y], j) => <circle key={j} cx={x} cy={y} r={3} />)
              ) : (
                <path
                  fill={mark.kind === "polygon" ? "var(--accent-bg)" : "none"}
                  fillRule="evenodd"
                  d={mark.parts
                    .map(
                      (p) =>
                        "M" +
                        p.map(([x, y]) => x + "," + y).join(" L") +
                        (mark.kind === "polygon" ? " Z" : ""),
                    )
                    .join(" ")}
                />
              )}
            </g>
          ))}
        </svg>
      ) : (
        <p>
          {exceeded
            ? "此图超出浏览器预览范围，可下载完整图层。"
            : "图层暂无可预览的坐标。"}
        </p>
      )}
      {map.geojson && (
        <button type="button" onClick={download}>
          <Download size={15} aria-hidden="true" />
          下载 GeoJSON
        </button>
      )}
    </details>
  );
}

export function QuestionProducts({
  tables = [],
  maps = [],
}: {
  tables?: AnswerTable[];
  maps?: AnswerMap[];
}) {
  if (!tables.length && !maps.length) return null;
  return (
    <section className="answer-products" aria-label="答复附表和地图">
      {tables.map((t, i) => (
        <details className="answer-product" key={i}>
          <summary>
            {t.title} · {t.total_rows} 行
          </summary>
          {t.rows.length ? (
            <>
              <div
                className="answer-table-scroll"
                role="region"
                aria-label={t.title}
                tabIndex={0}
              >
                <table>
                  <caption>{t.title}</caption>
                  <thead>
                    <tr>
                      {t.columns.map((c, j) => (
                        <th scope="col" key={j}>
                          {c}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {t.rows.map((row, j) => (
                      <tr key={j}>
                        {t.columns.map((_, k) => (
                          <td key={k}>
                            {row[k] === true
                              ? "是"
                              : row[k] === false
                                ? "否"
                                : (row[k] ?? "—")}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {t.rows.length < t.total_rows && (
                <small>
                  当前显示 {t.rows.length} 行，共 {t.total_rows} 行。
                </small>
              )}
            </>
          ) : (
            <p>没有可显示的数据行。</p>
          )}
        </details>
      ))}
      {maps.map((map, i) => (
        <MapResult map={map} key={i} />
      ))}
    </section>
  );
}
