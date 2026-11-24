import { useEffect, useState, type ReactNode } from "react";
import {
  Activity,
  ChartNoAxesColumn,
  Clock,
  Cpu,
  Database,
  Gauge,
  RefreshCw,
  Wallet,
} from "lucide-react";
import { api } from "./api";
import { ErrorLine, SectionTitle } from "./ui";
import "./resource-management.css";

type Latency = {
  samples: number;
  p50_ms: number | null;
  p95_ms: number | null;
};
type Resources = {
  project_id: string;
  policy: {
    task_slots: number;
    model_slots: number;
    model: string;
    provider: string;
    context_tokens: number;
    output_tokens: number;
  };
  host: { logical_cpu: number | null; host_ram_mib: number | null } | null;
  active_tasks: number;
  local: { tasks: number; elapsed: Latency };
  gateway_error?: string;
  gateway: null | {
    resource_schema_available: boolean;
    requests: number;
    intranet_requests: number;
    reported: number;
    unconfirmed: number;
    input_tokens: number;
    output_tokens: number;
    peak_context_tokens: number | null;
    peak_context_ratio: number | null;
    estimated_peak_upper: number | null;
    estimated_samples: number;
    latency: Latency;
    settled_cny: number;
    reserved_cny: number;
  };
};

const val = (n: number | null) =>
  n === null ? "—" : n.toLocaleString("zh-CN", { maximumFractionDigits: 2 });
const percent = (part: number, total: number) =>
  total > 0 ? Math.max(0, Math.min(100, (part / total) * 100)) : 0;
const money = (n: number) =>
  n.toLocaleString("zh-CN", {
    style: "currency",
    currency: "CNY",
    minimumFractionDigits: 6,
    maximumFractionDigits: 6,
  });

function Track({ value, total }: { value: number; total: number }) {
  return (
    <div className="resource-track" aria-hidden="true">
      <i style={{ width: percent(value, total) + "%" }} />
    </div>
  );
}

function MeterBreakdown({
  parts,
  format = val,
}: {
  parts: [string, number][];
  format?: (value: number) => string;
}) {
  const total = parts.reduce((sum, [, value]) => sum + value, 0);
  return (
    <>
      <div className="resource-breakdown" aria-hidden="true">
        {parts.map(([label, value]) => (
          <i key={label} style={{ width: percent(value, total) + "%" }} />
        ))}
      </div>
      <dl className="resource-legend">
        {parts.map(([label, value]) => (
          <div key={label}>
            <dt>{label}</dt>
            <dd>{format(value)}</dd>
          </div>
        ))}
      </dl>
    </>
  );
}

function DetailCard({
  title,
  icon: Icon,
  children,
}: {
  title: string;
  icon: typeof Cpu;
  children: ReactNode;
}) {
  return (
    <section className="resource-card">
      <h3>
        <Icon size={18} aria-hidden="true" />
        {title}
      </h3>
      {children}
    </section>
  );
}

function LatencyRows({ title, data }: { title: string; data: Latency }) {
  const max = Math.max(data.p50_ms ?? 0, data.p95_ms ?? 0);
  return (
    <div className="resource-latency">
      <div className="resource-latency-title">
        <h4>{title}</h4>
        <span>{data.samples ? val(data.samples) + " 次样本" : "暂无样本"}</span>
      </div>
      {data.samples > 0 && (
        <dl>
          {(
            [
              ["p50", data.p50_ms],
              ["p95", data.p95_ms],
            ] as const
          ).map(([label, value]) => (
            <div key={label}>
              <dt>{label}</dt>
              <dd>
                <Track value={value ?? 0} total={max} />
                <strong>
                  {val(value)}
                  {value !== null && " ms"}
                </strong>
              </dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  );
}

export default function ResourceManagement({
  projectId,
}: {
  projectId: string;
}) {
  const [data, setData] = useState<Resources | null>(null);
  const [error, setError] = useState("");
  const [tick, setTick] = useState(0);
  const [memory, setMemory] = useState(["", "", ""]);
  useEffect(() => {
    const ctrl = new AbortController();
    setError("");
    setData(null);
    api<Resources>(
      "/v2/resources?project_id=" + encodeURIComponent(projectId),
      { signal: ctrl.signal },
    )
      .then((r) => {
        if (!ctrl.signal.aborted) setData(r);
      })
      .catch((e) => {
        if (!ctrl.signal.aborted) setError(e.message);
      });
    return () => ctrl.abort();
  }, [projectId, tick]);
  const usable = memory.every(
    (v) => v.trim() !== "" && Number.isSafeInteger(Number(v)) && Number(v) > 0,
  );
  const [device, weights, increment] = memory.map(Number);
  const fit = usable
    ? Math.max(0, Math.floor((device - weights) / increment))
    : null;
  const current = data?.project_id === projectId ? data : null;
  const g = current?.gateway;
  return (
    <section className="wb-resource-page" aria-label="资源计量">
      <SectionTitle
        title="资源计量"
        actions={
          <button onClick={() => setTick((i) => i + 1)}>
            <RefreshCw size={15} /> 刷新用量
          </button>
        }
      />
      {error && <ErrorLine>{error}</ErrorLine>}
      {!current && !error && <p role="status">正在读取项目资源记录…</p>}
      {current && (
        <>
          <div className="resource-summary" aria-label="项目资源概览">
            <section className="resource-card resource-stat">
              <h3>
                <Activity size={18} aria-hidden="true" />
                当前项目运行任务
              </h3>
              <strong>{val(current.active_tasks)}</strong>
              <Track
                value={current.active_tasks}
                total={current.policy.task_slots}
              />
              <small>全平台任务并发上限 {val(current.policy.task_slots)}</small>
            </section>
            <section className="resource-card resource-stat">
              <h3>
                <Cpu size={18} aria-hidden="true" />
                累计模型请求
              </h3>
              <strong>{val(g?.requests ?? null)}</strong>
              {g && <small>其中内网服务 {val(g.intranet_requests)} 次</small>}
            </section>
            <section className="resource-card resource-stat">
              <h3>
                <Database size={18} aria-hidden="true" />
                已确认 Token
              </h3>
              <strong>
                {val(g ? g.input_tokens + g.output_tokens : null)}
              </strong>
              {g && (
                <MeterBreakdown
                  parts={[
                    ["输入", g.input_tokens],
                    ["输出", g.output_tokens],
                  ]}
                />
              )}
            </section>
          </div>

          {g && !g.resource_schema_available && (
            <p className="resource-note">
              历史账本可查看 Token 和费用。当次上下文窗口和请求耗时暂无记录。
            </p>
          )}
          {!g && (
            <p className="resource-note">
              {current.gateway_error || "当前部署未连接历史模型账本。"}
            </p>
          )}

          <div className="resource-detail-grid">
            {g && (
              <>
                <DetailCard title="请求用量确认" icon={ChartNoAxesColumn}>
                  <MeterBreakdown
                    parts={[
                      ["已确认", g.reported],
                      ["待确认", g.unconfirmed],
                    ]}
                  />
                </DetailCard>
                <DetailCard title="上下文用量" icon={Gauge}>
                  <div className="resource-context">
                    <strong>{val(g.peak_context_tokens)}</strong>
                    <span>单次实报输入与输出合计峰值</span>
                  </div>
                  {g.peak_context_ratio !== null && (
                    <>
                      <Track value={g.peak_context_ratio} total={1} />
                      <small>
                        占当次窗口 {val(g.peak_context_ratio * 100)}%
                      </small>
                    </>
                  )}
                  <small>
                    提交前估算上界{" "}
                    {g.estimated_samples
                      ? val(g.estimated_peak_upper) +
                        "（" +
                        val(g.estimated_samples) +
                        " 次）"
                      : "暂无样本"}
                  </small>
                </DetailCard>
                <DetailCard title="模型调用费用" icon={Wallet}>
                  <MeterBreakdown
                    parts={[
                      ["已计费用", g.settled_cny],
                      ["待确认预留", g.reserved_cny],
                    ]}
                    format={money}
                  />
                </DetailCard>
              </>
            )}
            <DetailCard title="耗时分布" icon={Clock}>
              {g && <LatencyRows title="模型请求" data={g.latency} />}
              <LatencyRows title="本地演练" data={current.local.elapsed} />
            </DetailCard>
            <section className="resource-card resource-deployment">
              <div className="resource-deployment-head">
                <h3>
                  <Cpu size={18} aria-hidden="true" />
                  部署容量
                </h3>
                <div>
                  <strong>{current.policy.model}</strong>
                  <span>
                    {current.policy.provider === "intranet"
                      ? "内网服务"
                      : "DeepSeek 云端"}
                  </span>
                </div>
              </div>
              <dl>
                {(
                  [
                    ["同机模型请求并发上限", current.policy.model_slots],
                    ["单次上下文窗口", current.policy.context_tokens],
                    ["单次输出硬上限", current.policy.output_tokens],
                    ["本地演练任务数", current.local.tasks],
                  ] as const
                ).map(([label, value]) => (
                  <div key={label}>
                    <dt>{label}</dt>
                    <dd>{val(value)}</dd>
                  </div>
                ))}
              </dl>
            </section>
          </div>

          <details className="resource-card wb-resource-capacity">
            <summary>内网模型容量估算</summary>
            {current.host && (
              <p>
                平台主机 · {val(current.host.logical_cpu)} 个逻辑处理器 ·
                物理内存 {val(current.host.host_ram_mib)} MiB
              </p>
            )}
            <p>
              填写推理设备同一内存池的实测值。按目标模型、上下文长度和精度完成预热后测量。
            </p>
            <div className="form-grid">
              {[
                "扣除安全余量后的可用容量（MiB）",
                "模型及服务常驻占用（MiB）",
                "每路并发峰值增量（MiB）",
              ].map((label, i) => (
                <label className="field" key={label}>
                  {label}
                  <input
                    type="number"
                    min="1"
                    step="1"
                    value={memory[i]}
                    onChange={(e) =>
                      setMemory(
                        memory.map((v, k) => (k === i ? e.target.value : v)),
                      )
                    }
                  />
                </label>
              ))}
            </div>
            <p aria-live="polite">
              {fit !== null
                ? `按给定内存估算可容纳 ${fit} 路并发；吞吐量需用代表性任务压测。`
                : "等待推理设备实测参数"}
            </p>
          </details>
        </>
      )}
    </section>
  );
}
