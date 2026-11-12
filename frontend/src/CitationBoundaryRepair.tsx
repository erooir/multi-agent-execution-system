function span(value: Record<string, unknown> | undefined) {
  return `${Number.isInteger(value?.start) ? value!.start : "未记录"}–${Number.isInteger(value?.end) ? value!.end : "未记录"}`;
}

export default function CitationBoundaryRepair({
  repair,
}: {
  repair: Record<string, any>;
}) {
  const corrected =
    repair.outcome?.passed === true &&
    repair.outcome?.scope === "source_span_only";
  return (
    <article className="taskcase-repair taskcase-citation-repair">
      <h4>{corrected ? "引用边界已校正" : "引用边界校正记录"}</h4>
      <p>
        本项仅校正引文在原文中的字符位置，不表示业务结论通过审核。任务结果仍以独立自动审核为准。
      </p>
      {Array.isArray(repair.repairs) && repair.repairs.length > 0 && (
        <>
          <div className="taskcase-table-scroll">
            <table>
              <thead>
                <tr>
                  <th>引用</th>
                  <th>原字符区间</th>
                  <th>校正后字符区间</th>
                </tr>
              </thead>
              <tbody>
                {repair.repairs.map((item: Record<string, any>, i: number) => (
                  <tr key={i}>
                    <td>
                      第{" "}
                      {Number.isInteger(item.citation_index)
                        ? item.citation_index + 1
                        : i + 1}{" "}
                      条
                    </td>
                    <td>{span(item.original)}</td>
                    <td>{span(item.corrected)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="taskcase-muted">
            区间以字符位置记录，起始位置计入，结束位置不计入。
          </p>
        </>
      )}
    </article>
  );
}
