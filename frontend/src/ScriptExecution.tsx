import { Download } from "lucide-react";
import type { Item } from "./types";
import { bytesLabel } from "./taskCases";

export default function ScriptExecution({
  scripts = [],
  runs = [],
}: {
  scripts?: Item[];
  runs?: Item[];
}) {
  if (!scripts.length && !runs.length) return null;
  return (
    <div className="taskcase-execution-section">
      <h3>隔离脚本与执行产物</h3>
      <p className="taskcase-muted">
        脚本版本、实际输出和运行限制分别保存。脚本退出正常只代表执行完成，交付质量仍以自动审核为准。
      </p>
      {scripts.map((script) => (
        <details className="taskcase-script" key={script.id}>
          <summary>
            {script.purpose || "数据处理脚本"} · 第 {(script.revision || 0) + 1}{" "}
            轮
          </summary>
          <p className="taskcase-muted">
            不可变版本：{script.id}
            <br />
            校验值：{script.checksum}
          </p>
          <pre>
            <code>{script.code}</code>
          </pre>
        </details>
      ))}
      {runs.map((run) => (
        <article className="taskcase-script-run" key={run.id}>
          <div className="taskcase-row">
            <strong>第 {(run.revision || 0) + 1} 轮脚本执行</strong>
            <span
              className={
                run.status === "succeeded"
                  ? "taskcase-review-pass"
                  : run.status === "failed"
                    ? "taskcase-review-fail"
                    : "taskcase-muted"
              }
            >
              {run.status === "succeeded"
                ? "执行完成"
                : run.status === "failed"
                  ? "执行失败"
                  : "正在执行"}
            </span>
          </div>
          <p className="taskcase-muted">
            隔离方式：{run.isolation || "尚未报告"}
            {run.returncode != null ? ` · 退出码 ${run.returncode}` : ""}
          </p>
          {run.limits && (
            <p className="taskcase-muted">
              执行限额：{run.limits.cpu != null ? `${run.limits.cpu} CPU` : ""}
              {run.limits.memory_mb != null
                ? ` · 内存 ${run.limits.memory_mb} MB`
                : ""}
              {run.limits.timeout_seconds != null
                ? ` · ${run.limits.timeout_seconds} 秒`
                : ""}
              {run.limits.output_bytes != null
                ? ` · 输出 ${bytesLabel(run.limits.output_bytes)}`
                : ""}
            </p>
          )}
          {run.error_code && (
            <p className="taskcase-review-fail">{run.error_code}</p>
          )}
          {!!run.stdout && (
            <details>
              <summary>标准输出</summary>
              <pre>{run.stdout}</pre>
              {run.stdout_truncated && (
                <p className="taskcase-muted">
                  标准输出超出保存上限，当前记录已截断。
                </p>
              )}
            </details>
          )}
          {!!run.stderr && (
            <details>
              <summary>错误输出</summary>
              <pre>{run.stderr}</pre>
              {run.stderr_truncated && (
                <p className="taskcase-muted">
                  错误输出超出保存上限，当前记录已截断。
                </p>
              )}
            </details>
          )}
          {!!run.outputs?.length && (
            <div className="taskcase-script-outputs">
              {run.outputs.map((output: Item, i: number) => (
                <div key={output.checksum || i}>
                  <strong>{output.name}</strong>
                  <small>
                    {bytesLabel(output.bytes)} · {output.media_type}
                  </small>
                  {typeof output.download_url === "string" &&
                    output.download_url.startsWith("/api/v2/task-cases/") && (
                      <a href={output.download_url}>
                        <Download size={14} />
                        下载脚本输出
                      </a>
                    )}
                  {output.preview != null && (
                    <details>
                      <summary>查看输出预览</summary>
                      <pre>
                        {typeof output.preview === "string"
                          ? output.preview
                          : JSON.stringify(output.preview, null, 2)}
                      </pre>
                      {output.preview_truncated && (
                        <p className="taskcase-muted">
                          仅显示预览，下载查看完整文件。
                        </p>
                      )}
                    </details>
                  )}
                </div>
              ))}
            </div>
          )}
        </article>
      ))}
    </div>
  );
}
