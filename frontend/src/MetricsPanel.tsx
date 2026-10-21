import { useEffect, useState } from "react";
import { Activity, Clock3, ListOrdered, ShieldAlert } from "lucide-react";
import { api } from "./api";
import { Panel, ErrorLine } from "./ui";
import { reasonNames, time } from "./domain";
import { durationLabel } from "./resultModel";
import type { components } from "./generated/api";

type Metrics = components["schemas"]["OperationalMetrics"];
export default function MetricsPanel({ projectId }: { projectId: string }) {
  const [hours, setHours] = useState(24),
    [metrics, setMetrics] = useState<Metrics | null>(null),
    [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    let busy = false;
    setMetrics(null);
    setError("");
    async function refresh() {
      if (busy) return;
      busy = true;
      try {
        const data = await api<Metrics>(
          `/v2/metrics?project_id=${encodeURIComponent(projectId)}&window_hours=${hours}`,
          { signal: controller.signal },
        );
        if (!controller.signal.aborted) {
          setMetrics(data);
          setError("");
        }
      } catch (err) {
        if (!controller.signal.aborted) setError((err as Error).message);
      } finally {
        busy = false;
      }
    }
    void refresh();
    const timer = setInterval(refresh, 10000);
    return () => {
      controller.abort();
      clearInterval(timer);
    };
  }, [projectId, hours]);
  return (
    <Panel
      title="运行观测"
      className="operations-summary"
      actions={
        <select
          aria-label="观测统计窗口"
          value={hours}
          onChange={(e) => setHours(Number(e.target.value))}
        >
          <option value={24}>最近 24 小时</option>
          <option value={168}>最近 7 天</option>
          <option value={720}>最近 30 天</option>
        </select>
      }
    >
      {error && (
        <ErrorLine>
          观测同步失败：{error}。
          {metrics ? "以下保留上次观测结果。" : "暂无可用观测结果。"}
        </ErrorLine>
      )}
      {!metrics ? (
        <p className="muted">
          {error ? "等待服务恢复" : "正在读取持久运行记录…"}
        </p>
      ) : (
        <>
          <div className="monitor-grid">
            <div>
              <ListOrdered size={18} />
              <span>等待执行</span>
              <strong>{metrics.queue.waiting_count}</strong>
              <small>
                最长等待{" "}
                {metrics.queue.oldest_wait_seconds == null
                  ? "—"
                  : durationLabel(metrics.queue.oldest_wait_seconds)}
              </small>
            </div>
            <div>
              <Activity size={18} />
              <span>正在执行</span>
              <strong>{metrics.counts.active}</strong>
              <small>
                交互队列 {metrics.queue.by_service_class.interactive || 0} ·
                批量队列 {metrics.queue.by_service_class.batch || 0}
              </small>
            </div>
            <div>
              <Clock3 size={18} />
              <span>端到端 p50 / p95</span>
              <strong className="latency-value">
                {durationLabel(metrics.latency.p50_seconds)} /{" "}
                {durationLabel(metrics.latency.p95_seconds)}
              </strong>
              <small>
                窗口内 {metrics.latency.sample_count} 个终态样本 · 含排队
              </small>
            </div>
            <div>
              <ShieldAlert size={18} />
              <span>关联执行器</span>
              <strong>{metrics.workers.online} 在线</strong>
              <small>
                {metrics.workers.stale} 心跳超时 · {metrics.workers.stopped}{" "}
                已停止
              </small>
            </div>
          </div>
          <div className="monitor-foot">
            <span>
              排队与运行显示当前状态；耗时和失败统计使用所选时间窗。已排除{" "}
              {metrics.excluded_rehearsal_count} 个演练记录。
            </span>
            <small>观测于 {time(metrics.generated_at)}</small>
          </div>
          {metrics.latency.excluded_count > 0 && (
            <p className="note">
              另有 {metrics.latency.excluded_count}{" "}
              条终态记录缺少有效时间，未计入耗时统计。
            </p>
          )}
          {metrics.failure_reasons.length > 0 && (
            <div className="failure-chips" aria-label="窗口内失败原因">
              {metrics.failure_reasons.map((item) => (
                <span key={item.reason_code}>
                  {reasonNames[item.reason_code] || item.reason_code}
                  <strong>{item.count}</strong>
                </span>
              ))}
            </div>
          )}
          <p className="muted small">
            交互任务优先；批量任务等待 {metrics.scheduling.aging_seconds}{" "}
            秒后提升顺序。执行中的任务不被抢占。关联执行器仅统计当前项目可见任务使用的工作进程。
          </p>
        </>
      )}
    </Panel>
  );
}
