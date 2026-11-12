import type { Item } from "./types";
import { Panel } from "./ui";

export default function AgentCollaboration({
  task,
  nodes,
}: {
  task: Item;
  nodes: Item[];
}) {
  const agents: Item[] = task.snapshot?.agents || [];
  const name = (id: string) =>
    agents.find((a) => (a.agent_id || a.id) === id)?.name || id;
  return (
    <Panel title="节点上下文与结果交接">
      <p className="muted">
        后端按流程依赖执行节点。这里记录本次执行可访问的资料范围和收到的上游结果来源，实际读取内容可在工具调用中查看。复用步骤沿用原执行记录。
      </p>
      {nodes.length === 0 ? (
        <p>任务开始执行后显示信息交接记录。</p>
      ) : (
        nodes.map((node) => {
          const context = node.context_manifest;
          return (
            <section key={node.id} className="panel">
              <strong>
                {node.label || node.node_id} · {name(node.agent_id)} · v
                {node.agent_version}
              </strong>
              {!context ? (
                <p>此历史节点尚未记录结果交接清单。</p>
              ) : (
                <>
                  <p>
                    上下文：{context.context_id} · 第 {context.revision + 1} 轮
                    · {context.input_refs.length} 份任务授权资料
                  </p>
                  <p>
                    {context.upstream.length
                      ? "实际收到的上游结果："
                      : "本次执行未接收上游节点结果。"}
                  </p>
                  <ul>
                    {context.upstream.map((source: Item, index: number) => (
                      <li key={source.node_record_id + ":" + index}>
                        {name(source.agent_id)} v{source.agent_version} →{" "}
                        {node.label || node.node_id}
                        <small>
                          来源步骤 {source.node_id} · 执行记录{" "}
                          {source.node_record_id} · 结果指纹{" "}
                          {source.checksum.slice(0, 12)}
                        </small>
                      </li>
                    ))}
                  </ul>
                  {node.reused_from && <p>本轮复用记录：{node.reused_from}</p>}
                </>
              )}
            </section>
          );
        })
      )}
    </Panel>
  );
}
