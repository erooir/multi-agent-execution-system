import { useEffect, useState } from "react";
import { api } from "./api";
import { reasonNames, statusNames, time } from "./domain";
import type { Item } from "./types";
import { Badge, Empty, ErrorLine, Panel } from "./ui";

const filters = {
  all: "全部智能体",
  active: "运行中",
  issues: "有失败或审核未过",
  used: "有执行记录",
};
type Filter = keyof typeof filters;
export default function AgentActivity({
  projectId,
  onAgent,
  onTask,
}: {
  projectId: string;
  onAgent: (id: string) => void;
  onTask: (id: string) => void;
}) {
  const [activity, setActivity] = useState<Item[] | null>(null);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function refresh() {
      try {
        const result = await api<Item>(
          "/v2/agents/activity/summary?project_id=" +
            encodeURIComponent(projectId),
          { signal: controller.signal },
        );
        if (!controller.signal.aborted) {
          setActivity(result.agents);
          setError("");
        }
      } catch (e) {
        if (!controller.signal.aborted) {
          setActivity(null);
          setError((e as Error).message);
        }
      } finally {
        if (!controller.signal.aborted) timer = setTimeout(refresh, 10000);
      }
    }
    void refresh();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [projectId]);
  const visible = activity?.filter(
    (a) =>
      filter === "all" ||
      (filter === "active"
        ? a.running > 0
        : filter === "issues"
          ? a.failed > 0 || a.review_rejections > 0
          : a.attempts > 0),
  );
  return (
    <Panel title="近7天节点尝试及当前运行节点">
      <p className="muted">
        每 10
        秒更新。尝试按实际执行的智能体版本计数，平均耗时仅统计有完整起止时间的节点。
      </p>
      {error && <ErrorLine>{error}</ErrorLine>}
      {activity && (
        <div className="monitor-totals">
          {[
            ["节点尝试", "attempts"],
            ["当前运行", "running"],
            ["修订尝试", "revisions"],
            ["审核未过", "review_rejections"],
          ].map(([label, field]) => (
            <div key={field}>
              <span>{label}</span>
              <strong>
                {activity.reduce((sum, item) => sum + item[field], 0)}
              </strong>
            </div>
          ))}
        </div>
      )}
      <div className="toolbar" aria-label="智能体运行筛选">
        {Object.entries(filters).map(([id, label]) => (
          <button
            key={id}
            aria-pressed={filter === id}
            className={filter === id ? "primary" : ""}
            onClick={() => setFilter(id as Filter)}
          >
            {label}
          </button>
        ))}
      </div>
      {!activity && !error && <p role="status">正在读取智能体节点记录…</p>}
      {visible &&
        (visible.length === 0 ? (
          <Empty
            title="当前筛选没有智能体"
            description="可以切换筛选条件，或在执行任务后查看节点记录。"
          />
        ) : (
          <div className="table-scroll">
            <table className="result-table monitor-table">
              <thead>
                <tr>
                  <th>智能体与版本</th>
                  <th>尝试 / 运行</th>
                  <th>失败 / 修订</th>
                  <th>审核未过</th>
                  <th>平均耗时</th>
                  <th>最近任务</th>
                </tr>
              </thead>
              <tbody>
                {visible.map((a) => (
                  <tr key={a.agent_id}>
                    <td>
                      <button onClick={() => onAgent(a.agent_id)}>
                        {a.name}
                      </button>{" "}
                      <Badge status={a.status} />
                      <small>
                        当前{" "}
                        {a.current_version ? "v" + a.current_version : "未发布"}
                        {a.attempts > 0 &&
                          " · 运行 " +
                            Object.entries(a.versions)
                              .map(([v, n]) => "v" + v + " × " + n)
                              .join("、")}
                      </small>
                    </td>
                    <td>
                      {a.attempts} / {a.running}
                    </td>
                    <td>
                      {a.failed} / {a.revisions}
                    </td>
                    <td>{a.review_rejections}</td>
                    <td>
                      {a.mean_seconds === null ? "—" : a.mean_seconds + " 秒"}
                    </td>
                    <td>
                      {a.last ? (
                        <>
                          <button onClick={() => onTask(a.last.task_id)}>
                            {a.last.node_id} ·{" "}
                            {statusNames[a.last.status] || a.last.status}
                          </button>
                          <small>
                            {a.last.error
                              ? reasonNames[a.last.error] || a.last.error
                              : time(a.last.finished_at || a.last.started_at)}
                          </small>
                        </>
                      ) : (
                        "—"
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ))}
    </Panel>
  );
}
