import { useEffect, useState } from "react";
import { api } from "./api";
import { ErrorLine, Panel, SectionTitle } from "./ui";

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
  n === null ? "暂无样本" : n.toLocaleString("zh-CN");
function timings(data: Latency) {
  return data.samples
    ? `${val(data.p50_ms)} / ${val(data.p95_ms)} 毫秒（${data.samples} 次）`
    : "暂无耗时样本";
}
function Metrics({ rows }: { rows: [string, string | number][] }) {
  return (
    <dl className="facts">
      {rows.map(([name, v]) => (
        <div key={name}>
          <dt>{name}</dt>
          <dd>{v}</dd>
        </div>
      ))}
    </dl>
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
    <section aria-label="资源计量">
      <SectionTitle
        title="资源计量"
        actions={
          <button onClick={() => setTick((i) => i + 1)}>刷新用量</button>
        }
      />
      {error && <ErrorLine>{error}</ErrorLine>}
      {!current && !error && <p role="status">正在读取项目资源记录…</p>}
      {current && (
        <>
          <Panel title="任务与部署容量">
            <Metrics
              rows={[
                ["当前项目执行中", current.active_tasks],
                ["全平台任务并发上限", current.policy.task_slots],
                ["网关模型", current.policy.model],
                [
                  "模型来源",
                  current.policy.provider === "intranet"
                    ? "内网服务"
                    : "DeepSeek 云端",
                ],
                ["同机模型请求并发上限", current.policy.model_slots],
                [
                  "单次上下文窗口（部署值）",
                  val(current.policy.context_tokens),
                ],
                ["单次输出硬上限", val(current.policy.output_tokens)],
                ["本地模式任务数", current.local.tasks],
                ["本地任务耗时 p50 / p95", timings(current.local.elapsed)],
              ]}
            />
          </Panel>
          <Panel title="项目模型调用">
            {g && !g.resource_schema_available && (
              <p>
                历史账本尚无资源记录，当前可查看既有 Token
                和费用；当次窗口与请求耗时不可用。
              </p>
            )}
            {g ? (
              <Metrics
                rows={[
                  ["累计请求", g.requests],
                  ["其中内网服务请求", g.intranet_requests],
                  ["用量已确认 / 待确认", `${g.reported} / ${g.unconfirmed}`],
                  [
                    "已确认输入 / 输出 Token",
                    `${val(g.input_tokens)} / ${val(g.output_tokens)}`,
                  ],
                  ["单次实报输入与输出合计峰值", val(g.peak_context_tokens)],
                  [
                    "实报峰值占当次窗口",
                    g.peak_context_ratio === null
                      ? "暂无样本"
                      : (g.peak_context_ratio * 100).toFixed(2) + "%",
                  ],
                  [
                    "提交前上下文估算上界峰值",
                    g.estimated_samples
                      ? `${val(g.estimated_peak_upper)}（${g.estimated_samples} 次）`
                      : "暂无估算样本",
                  ],
                  ["模型请求耗时 p50 / p95", timings(g.latency)],
                  [
                    "已计费用 / 待确认预留（元）",
                    `${g.settled_cny.toFixed(6)} / ${g.reserved_cny.toFixed(6)}`,
                  ],
                ]}
              />
            ) : (
              <p>{current.gateway_error || "当前部署未连接历史模型账本。"}</p>
            )}
          </Panel>
          <Panel title="内网模型容量估算">
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
          </Panel>
        </>
      )}
    </section>
  );
}
