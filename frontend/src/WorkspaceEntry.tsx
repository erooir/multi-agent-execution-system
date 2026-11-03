import { useEffect, useState } from "react";
import {
  Activity,
  ArrowUpRight,
  GitCompareArrows,
  Layers3,
  Wrench,
} from "lucide-react";
import { api } from "./api";
import type { CaseTab } from "./Casework";
import type { Item, Page } from "./types";
import "./workspace.css";

export default function WorkspaceEntry({
  projectId,
  agents,
  go,
  openCasework,
}: {
  projectId: string;
  agents: Item[];
  go: (page: Page) => void;
  openCasework: (tab: CaseTab) => void;
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
    <section className="workspace-entry" aria-label="项目工作入口">
      <div className="workspace-intro">
        <span className="workspace-kicker">YOUR AI WORKSPACE</span>
        <h2>从资料出发，找到答案。</h2>
        <p>选择资料和分析方式，让助手协作完成。</p>
        <div className="workspace-stats">
          <span>{fixed} 个流程助手</span>
          <span>{autonomous} 个工具助手</span>
          <span>{dual} 个场景助手</span>
        </div>
        <button className="text-button" onClick={() => go("agents")}>
          查看全部助手 <ArrowUpRight size={15} />
        </button>
      </div>
      <div className="workspace-actions">
        <button onClick={() => openCasework("catalog")}>
          <Layers3 />
          <span>
            <strong>开始场景分析</strong>
            <small>选择场景，创建任务</small>
          </span>
        </button>
        <button onClick={() => openCasework("roles")}>
          <GitCompareArrows />
          <span>
            <strong>比较分析助手</strong>
            <small>找到适合的分工</small>
          </span>
        </button>
        <button onClick={() => go("tools")}>
          <Wrench />
          <span>
            <strong>工具箱</strong>
            <small>
              {toolCount !== null
                ? `${toolCount} 项可查工具`
                : error
                  ? "目录数量暂不可用"
                  : "探索可用工具"}
            </small>
          </span>
        </button>
        <button onClick={() => openCasework("monitor")}>
          <Activity />
          <span>
            <strong>查看运行状态</strong>
            <small>了解每一步进展</small>
          </span>
        </button>
      </div>
    </section>
  );
}
