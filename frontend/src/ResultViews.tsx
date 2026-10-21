import { useState } from "react";
import {
  CheckCircle2,
  CircleAlert,
  FileText,
  Search,
  ShieldCheck,
} from "lucide-react";
import type { Item } from "./types";
import { Badge, Empty, JsonView } from "./ui";
import {
  displayValue,
  qualityOutputs,
  resultRows,
  tableColumns,
} from "./resultModel";

const checkNames: Record<string, string> = {
  nonempty_output: "产物非空",
  source_refs: "来源属于冻结输入",
  record_schema: "记录格式",
  min_records: "最少记录数",
  required_fields: "必需字段",
  exact_source_spans: "原文位置一致",
  extractive_fragments: "来源片段完整",
  grounded_citations: "引用可核验",
  min_length: "文本长度",
  image_metadata: "图片真实解码",
  model_output_invalid: "模型输出格式",
  supported_output: "支持的产物类型",
};

export function QualityView({
  quality,
  withheld = false,
}: {
  quality?: Item | null;
  withheld?: boolean;
}) {
  const outputs = qualityOutputs(quality);
  if (withheld)
    return <p className="warning">来源已撤回或过期，派生评审内容不再提供。</p>;
  if (!outputs.length)
    return (
      <Empty
        title="尚无质量判定"
        description="任务完成校验后显示逐项检查和自动评审依据。"
      />
    );
  return (
    <div className="quality-results">
      <div className="result-heading">
        <ShieldCheck size={20} />
        <strong>质量检查</strong>
        <Badge status={quality?.verdict || "unknown"} />
      </div>
      {outputs.map((output, index) => (
        <section className="quality-output" key={index}>
          {outputs.length > 1 && <h3>产物 {index + 1}</h3>}
          <div className="check-grid">
            {(output.checks || []).map((check: Item, i: number) => (
              <div
                className={check.passed ? "check-pass" : "check-fail"}
                key={i}
              >
                {check.passed ? (
                  <CheckCircle2 size={16} />
                ) : (
                  <CircleAlert size={16} />
                )}
                <span>{checkNames[check.name] || check.name}</span>
                <small>{check.passed ? "通过" : "未通过"}</small>
              </div>
            ))}
          </div>
          {(output.issues || []).map((issue: Item | string, i: number) => (
            <p className="warning" key={i}>
              {typeof issue === "string"
                ? issue
                : `${issue.hard ? "硬性问题：" : "问题："}${issue.message || issue.code}`}
            </p>
          ))}
          {output.model_review && (
            <div className="model-review">
              <div className="row-between">
                <strong>独立上下文自动评审</strong>
                <Badge status={output.model_review.verdict || "unknown"} />
              </div>
              {(output.model_review.issues || []).map(
                (issue: string, i: number) => (
                  <p key={i}>{issue}</p>
                ),
              )}
              {output.model_review.suggested_changes?.length > 0 && (
                <>
                  <h4>修订建议</h4>
                  <ul>
                    {output.model_review.suggested_changes.map(
                      (s: string, i: number) => (
                        <li key={i}>{s}</li>
                      ),
                    )}
                  </ul>
                </>
              )}
              <p className="muted small">
                独立上下文减少相互干扰；同一模型仍可能存在共同偏差，结论同时受确定性检查约束。
              </p>
            </div>
          )}
        </section>
      ))}
      <JsonView value={quality} label="完整评审记录" />
    </div>
  );
}

export function ArtifactContent({ artifact }: { artifact: Item }) {
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(0);
  if (artifact.content_withheld)
    return <p className="warning">来源已撤回或过期，该产物内容不再提供。</p>;
  const content = artifact.content,
    rows = resultRows(content);
  const columns = rows ? tableColumns(rows) : [];
  const filtered =
    rows?.filter(
      (row) =>
        !query ||
        Object.values(row).some((v) =>
          displayValue(v).toLowerCase().includes(query.toLowerCase()),
        ),
    ) || [];
  const pageCount = Math.max(1, Math.ceil(filtered.length / 50)),
    currentPage = Math.min(page, pageCount - 1);
  return (
    <div className="artifact-content">
      {artifact.rehearsal && (
        <p className="warning">配置演练产物，不代表模型推理或业务质量。</p>
      )}
      {rows ? (
        <>
          <div className="toolbar">
            <strong>
              {rows.length} 条记录 · {columns.length} 个字段
            </strong>
            <div className="search">
              <Search size={16} />
              <input
                aria-label="筛选产物记录"
                value={query}
                placeholder="搜索结果中的内容"
                onChange={(e) => {
                  setQuery(e.target.value);
                  setPage(0);
                }}
              />
            </div>
          </div>
          <div className="table-scroll result-table">
            <table>
              <thead>
                <tr>
                  <th scope="col">序号</th>
                  {columns.map((key) => (
                    <th scope="col" key={key}>
                      {key}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {filtered
                  .slice(currentPage * 50, (currentPage + 1) * 50)
                  .map((row, i) => (
                    <tr key={currentPage * 50 + i}>
                      <td>{currentPage * 50 + i + 1}</td>
                      {columns.map((key) => (
                        <td key={key}>{displayValue(row[key])}</td>
                      ))}
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
          {!filtered.length && (
            <p className="muted">没有符合筛选条件的记录。</p>
          )}
          {pageCount > 1 && (
            <div className="row-between">
              <span>
                筛选后 {filtered.length} 条 · 第 {currentPage + 1} / {pageCount}{" "}
                页
              </span>
              <div className="actions">
                <button
                  disabled={currentPage === 0}
                  onClick={() => setPage(currentPage - 1)}
                >
                  上一页
                </button>
                <button
                  disabled={currentPage >= pageCount - 1}
                  onClick={() => setPage(currentPage + 1)}
                >
                  下一页
                </button>
              </div>
            </div>
          )}
        </>
      ) : typeof content === "string" || typeof content?.text === "string" ? (
        <div className="result-prose">
          {typeof content === "string" ? content : content.text}
        </div>
      ) : Array.isArray(content?.fragments) ? (
        <>
          <div className="result-heading">
            <FileText size={18} />
            <strong>来源文本摘录</strong>
          </div>
          {content.fragments.map((fragment: Item, i: number) => (
            <figure className="evidence-card" key={i}>
              <blockquote>{fragment.text}</blockquote>
              <figcaption>
                来源 {fragment.source_id} · 字符 {fragment.start}–{fragment.end}
              </figcaption>
            </figure>
          ))}
        </>
      ) : Array.isArray(content?.images) ? (
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>来源</th>
                <th>尺寸</th>
                <th>格式</th>
                <th>解码</th>
              </tr>
            </thead>
            <tbody>
              {content.images.map((img: Item, i: number) => (
                <tr key={i}>
                  <td>{img.source_id}</td>
                  <td>
                    {img.width} × {img.height}
                  </td>
                  <td>{img.format || "—"}</td>
                  <td>{img.decoded === true ? "通过" : "未通过"}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="note">这是文件解码和元数据检查，未执行图像语义理解。</p>
        </div>
      ) : (
        <pre className="prose">{JSON.stringify(content, null, 2)}</pre>
      )}
      {content?.citations?.length > 0 && (
        <section className="evidence-list">
          <h3>来源与证据</h3>
          {content.citations.map((cite: Item, i: number) => (
            <figure className="evidence-card" key={i}>
              <figcaption>
                证据 {i + 1} ·{" "}
                {cite.kind === "derived_result" ? "工具计算结果" : "原文引用"} ·{" "}
                {cite.source_id}
                {cite.source_version ? ` · 版本 ${cite.source_version}` : ""}
              </figcaption>
              {cite.quote && <blockquote>{cite.quote}</blockquote>}
              {cite.start != null && (
                <small>
                  字符位置 {cite.start}–{cite.end}
                </small>
              )}
              {cite.kind === "derived_result" && (
                <JsonView value={cite} label="计算证据详情" />
              )}
            </figure>
          ))}
        </section>
      )}
      <details className="json">
        <summary>原始产物 JSON</summary>
        <pre className="prose">{JSON.stringify(content, null, 2)}</pre>
      </details>
    </div>
  );
}
