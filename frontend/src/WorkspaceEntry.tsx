import { useEffect, useState } from "react";
import { api } from "./api";
import type { Item } from "./types";
import "./workspace.css";

export default function WorkspaceEntry({
  projectId,
  agents,
}: {
  projectId: string;
  agents: Item[];
}) {
  const [toolCount, setToolCount] = useState<number | null>(null);
  const [error, setError] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    api<Item>("/v2/capabilities?project_id=" + encodeURIComponent(projectId), {
      signal: controller.signal,
    })
      .then((catalog) => {
        if (!controller.signal.aborted) setToolCount(catalog.tools.length);
      })
      .catch(() => {
        if (!controller.signal.aborted) setError(true);
      });
    return () => controller.abort();
  }, [projectId]);
  const published = agents.filter((a) => a.status === "published");
  const dual = published.filter(
    (a) => a.executor_bindings?.local && a.executor_bindings?.cloud,
  ).length;
  const fixed = published.filter(
    (a) =>
      (a.execution_strategy || "fixed") === "fixed" &&
      !(a.executor_bindings?.local && a.executor_bindings?.cloud),
  ).length;
  const autonomous = published.filter(
    (a) =>
      a.execution_strategy === "tool_agent" &&
      !(a.executor_bindings?.local && a.executor_bindings?.cloud),
  ).length;
  return (
    <section className="workspace-entry" aria-label="项目资源汇总">
      <h2>智能体与工具概况</h2>
      <dl className="workspace-summary-stats">
        <div>
          <dt>固定流程智能体</dt>
          <dd>{fixed}</dd>
        </div>
        <div>
          <dt>工具型智能体</dt>
          <dd>{autonomous}</dd>
        </div>
        <div>
          <dt>场景智能体</dt>
          <dd>{dual}</dd>
        </div>
        <div>
          <dt>已登记工具</dt>
          <dd>{toolCount ?? "—"}</dd>
        </div>
      </dl>
      {error && <p role="status">工具数量暂不可用</p>}
    </section>
  );
}
