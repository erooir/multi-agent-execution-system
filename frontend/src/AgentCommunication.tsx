import type { Item } from "./types";
import { Empty, Panel } from "./ui";

export default function AgentCommunication({
  task,
  agents,
  onNode,
}: {
  task: Item;
  agents: Item[];
  onNode: (item: Item) => void;
}) {
  const instances: Item[] = task.instances || [];
  const name = (i: Item) =>
    `${agents.find((a) => a.id === i.agent_id)?.name || i.agent_id} · ${i.node_id} · v${i.agent_version}`;
  return (
    <Panel title="智能体协作与上下文">
      <p className="muted">
        每次执行使用独立上下文，只接收授权资料与依赖步骤的产物。委派与结果返回按实例追溯。
      </p>
      {task.content_withheld ? (
        <p className="warning">来源已不可用，协作详情已隐藏。</p>
      ) : instances.some((i) => i.context) ? (
        instances
          .filter((i) => i.context)
          .map((i) => (
            <article className="record" key={i.id}>
              <strong>{name(i)}</strong>
              <p>
                {i.kind === "coordinator" ? "复合协调者" : "执行成员"} · 第
                {(i.context.revision ?? 0) + 1}轮
                {i.parent_instance_id &&
                  ` · 委派方：${instances.find((p) => p.id === i.parent_instance_id)?.node_id || i.parent_instance_id}`}
              </p>
              <p className="mono small wrap-text">上下文 {i.context_id}</p>
              <p className="muted">
                授权来源 {i.context.input_refs?.length || 0} 项 · 收到依赖{" "}
                {i.context.inbox?.length || 0} 项
                {i.kind === "coordinator" &&
                  ` · 收回结果 ${i.result_messages?.length || 0} 项`}
              </p>
              {[...(i.context.inbox || []), ...(i.result_messages || [])].map(
                (m: Item, index) => {
                  const node = (task.nodes || []).find(
                    (n: Item) => n.id === m.source_node_id,
                  );
                  return (
                    <div
                      className="record"
                      key={`${m.source_node_id}:${index}`}
                    >
                      <button
                        className="text-button"
                        disabled={!node}
                        onClick={() => node && onNode(node)}
                      >
                        查看来源步骤 {node?.node_id || m.source_node_id}
                      </button>
                      <small>
                        {" "}
                        · v{m.agent_version ?? "历史未记录"} ·{" "}
                        {m.reused_from ? "复用检查点" : "执行结果"}
                      </small>
                      <p className="mono small wrap-text">
                        SHA-256 {m.checksum}
                      </p>
                    </div>
                  );
                },
              )}
            </article>
          ))
      ) : (
        <Empty
          title="尚无上下文交换记录"
          description="新执行会保存通信记录；历史运行不会补造发送者或委派事件。"
        />
      )}
    </Panel>
  );
}
