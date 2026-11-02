import { useEffect, useState } from "react";
import { api, idPath } from "./api";
import { reasonNames, time } from "./domain";
import type { Item } from "./types";
import { Badge, Empty, ErrorLine, Field, JsonView, Modal } from "./ui";
import { callContext, callRound } from "./taskTraceModel";
import "./task-trace.css";

const kinds: Record<string, string> = {
  tool: "内置工具",
  mcp: "MCP 服务",
  skill: "技能读取",
};
export function callError(code: string) {
  return (
    (
      {
        unsupported_tool_input: "输入未满足工具的格式或范围要求",
        tool_interrupted: "工具执行中断",
        lease_recovered: "工作进程接管了未完成的调用",
      } as Record<string, string>
    )[code] ||
    reasonNames[code] ||
    code
  );
}

export function ToolCalls({
  task,
  calls,
  onSelect,
}: {
  task: Item;
  calls: Item[];
  onSelect: (id: string) => void;
}) {
  const [status, setStatus] = useState("all");
  if (!calls.length)
    return (
      <Empty
        title="暂无工具调用"
        description="此范围内尚无实际调用记录。岗位授权可在智能体资产中查看。"
      />
    );
  const rows = calls.filter(
    (call) => status === "all" || call.status === status,
  );
  return (
    <>
      <div className="trace-filters">
        <p>
          共 {calls.length} 次实际调用 · 已完成{" "}
          {calls.filter((c) => c.status === "completed").length} 次 · 失败{" "}
          {calls.filter((c) => c.status === "failed").length} 次
        </p>
        <Field label="调用状态">
          <select value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="all">全部状态</option>
            <option value="running">运行中</option>
            <option value="completed">已完成</option>
            <option value="failed">失败</option>
          </select>
        </Field>
      </div>
      {task.content_withheld && (
        <p className="warning">来源已撤回或不可用，调用输入与输出已屏蔽。</p>
      )}
      {rows.length ? (
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>工具 / 技能</th>
                <th>执行节点</th>
                <th>状态</th>
                <th>耗时</th>
                <th>调用记录</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((call) => {
                const { label, node, agent } = callContext(task, call);
                return (
                  <tr key={call.id}>
                    <td>
                      <strong>{label}</strong>
                      {label !== call.capability_id && (
                        <small>{call.capability_id}</small>
                      )}
                      <small>
                        {kinds[call.kind] || call.kind} · v{call.version}
                      </small>
                    </td>
                    <td>
                      {node?.label || call.node_id || "未记录节点"}
                      <small>
                        {callRound(call.revision)}
                        {agent && " · " + agent.name + " v" + agent.version}
                      </small>
                    </td>
                    <td>
                      <Badge status={call.status} />
                      {call.error_code && (
                        <small>{callError(call.error_code)}</small>
                      )}
                    </td>
                    <td>
                      {call.status === "running"
                        ? "进行中"
                        : call.duration_ms != null
                          ? call.duration_ms + " ms"
                          : "未记录"}
                    </td>
                    <td>
                      <button
                        disabled={task.content_withheld}
                        onClick={() => onSelect(call.id)}
                      >
                        查看输入与输出
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : (
        <Empty title="没有符合该状态的调用" />
      )}
    </>
  );
}

export function ToolCallDialog({
  task,
  call,
  onClose,
}: {
  task: Item;
  call: Item;
  onClose: () => void;
}) {
  const [detail, setDetail] = useState<Item | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    setDetail(null);
    setError("");
    api(
      idPath("tasks", task.id) + "/tool-calls/" + encodeURIComponent(call.id),
      { signal: controller.signal },
    )
      .then((value) => {
        if (!controller.signal.aborted) setDetail(value);
      })
      .catch((e) => {
        if (!controller.signal.aborted) setError(e.message);
      });
    return () => controller.abort();
  }, [task.id, call.id, call.status]);
  const { label, node, agent } = callContext(task, call);
  return (
    <Modal wide title="工具调用详情" onClose={onClose}>
      <div className="modal-body trace-call">
        <div className="row-between">
          <h3>{label}</h3>
          <Badge status={call.status} />
        </div>
        <p>
          {callRound(call.revision)} ·{" "}
          {node?.label || call.node_id || "未记录节点"}
          {agent && " · " + agent.name + " v" + agent.version}
        </p>
        <dl className="trace-facts">
          <dt>调用 ID</dt>
          <dd>
            <code>{call.id}</code>
          </dd>
          <dt>能力版本</dt>
          <dd>
            {call.capability_id} · v{call.version}
          </dd>
          <dt>开始时间</dt>
          <dd>{time(call.started_at)}</dd>
          <dt>读取来源</dt>
          <dd>
            {(call.source_refs || [])
              .map(
                (ref: string) =>
                  task.snapshot?.inputs?.find((a: Item) => a.id === ref)
                    ?.name || ref,
              )
              .join("、") || "未记录来源"}
          </dd>
        </dl>
        {call.error_code && (
          <p className="warning">
            {callError(call.error_code)} · {call.error_code}
          </p>
        )}
        {error ? (
          <ErrorLine>{error}</ErrorLine>
        ) : detail ? (
          <>
            <JsonView value={detail.call} label="调用元数据" />
            <h3>实际参数</h3>
            {detail.arguments == null ? (
              <p className="muted">没有保存参数内容。</p>
            ) : (
              <pre>{JSON.stringify(detail.arguments, null, 2)}</pre>
            )}
            <h3>实际输出</h3>
            {detail.result == null ? (
              <p className="muted">没有保存输出内容。</p>
            ) : (
              <pre>{JSON.stringify(detail.result, null, 2)}</pre>
            )}
          </>
        ) : (
          <p role="status">正在读取调用记录…</p>
        )}
      </div>
    </Modal>
  );
}
