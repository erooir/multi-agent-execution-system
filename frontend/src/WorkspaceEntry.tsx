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
        <span className="eyebrow">项目工作入口</span>
        <h2>智能体、工具与任务</h2>
        <p>
          当前项目已发布 <strong>{fixed}</strong> 个固定流程执行智能体、{" "}
          <strong>{autonomous}</strong> 个自主工具型智能体和{" "}
          <strong>{dual}</strong> 个本地 / 云端双模式用例智能体。
        </p>
        <button className="text-button" onClick={() => go("agents")}>
          查看智能体资产 <ArrowUpRight size={15} />
        </button>
      </div>
      <div className="workspace-actions">
        <button onClick={() => openCasework("catalog")}>
          <Layers3 />
          <span>
            <strong>用例任务</strong>
            <small>查看所需材料，提交分析</small>
          </span>
        </button>
        <button onClick={() => openCasework("roles")}>
          <GitCompareArrows />
          <span>
            <strong>用例岗位对照</strong>
            <small>比较算法、验收条件与许可</small>
          </span>
        </button>
        <button onClick={() => go("tools")}>
          <Wrench />
          <span>
            <strong>工具目录</strong>
            <small>
              {toolCount !== null
                ? `${toolCount} 项登记工具 · 查看智能体授权`
                : error
                  ? "目录数量暂不可用"
                  : "正在读取目录数量…"}
            </small>
          </span>
        </button>
        <button onClick={() => openCasework("monitor")}>
          <Activity />
          <span>
            <strong>智能体运行监测</strong>
            <small>查看节点尝试、修订与审核</small>
          </span>
        </button>
      </div>
    </section>
  );
}
