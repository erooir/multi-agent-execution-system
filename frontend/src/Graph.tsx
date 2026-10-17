import { useMemo } from "react";
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  Handle,
  Position,
} from "@xyflow/react";
import { Bot, Workflow } from "lucide-react";
import "@xyflow/react/dist/style.css";
import type { Item } from "./types";
import { graphLayout } from "./domain";
import { Badge, Empty } from "./ui";
function AgentNode({ data }: any) {
  return (
    <div className="agent-node">
      <Handle type="target" position={Position.Left} />
      <div className="node-head">
        <span className="node-icon">
          <Bot size={20} />
        </span>
        <div>
          <small>{data.agentName || "任务节点"}</small>
          <strong>{data.label}</strong>
        </div>
      </div>
      <p>
        {data.description ||
          (data.agent_id
            ? `版本 ${data.agent_version || "发布时冻结"}`
            : "任务级配置")}
      </p>
      <div className="node-foot">
        <Badge status={data.status || "pending"} />
        <span>{data.node_id || data.id}</span>
      </div>
      <Handle type="source" position={Position.Right} />
    </div>
  );
}
const nodeTypes = { agent: AgentNode };
export default function Graph({
  nodes,
  agents = [],
  onNode,
}: {
  nodes: Item[];
  agents?: Item[];
  onNode?: (item: Item) => void;
}) {
  const prepared = useMemo(() => {
    const layout = graphLayout(nodes),
      ids = new Set(layout.map((n) => n.graphId));
    return {
      nodes: layout.map((n) => ({
        id: n.graphId,
        type: "agent",
        position: n.position,
        data: {
          ...n,
          agentName: agents.find((a) => a.id === n.agent_id)?.name,
        },
      })),
      edges: layout.flatMap((n) =>
        (n.depends_on || [])
          .filter((id: string) => ids.has(id))
          .map((id: string) => ({
            id: `${id}-${n.graphId}`,
            source: id,
            target: n.graphId,
            type: "smoothstep",
            animated: n.status === "running",
          })),
      ),
    };
  }, [nodes, agents]);
  if (!nodes.length)
    return (
      <Empty
        title="尚未生成执行图"
        description="任务进入规划后，这里显示已持久化的节点、依赖与绑定版本。"
      />
    );
  return (
    <div className="graph">
      <div className="graph-caption">
        <Workflow size={15} /> 按依赖排列 · 点击节点查看记录
      </div>
      <ReactFlow
        nodes={prepared.nodes}
        edges={prepared.edges}
        nodeTypes={nodeTypes}
        colorMode="dark"
        fitView
        fitViewOptions={{ padding: 0.15 }}
        nodesDraggable={false}
        nodesConnectable={false}
        onNodeClick={(_, node) => onNode?.(node.data)}
        minZoom={0.2}
        maxZoom={1.5}
        proOptions={{ hideAttribution: false }}
      >
        <Background gap={24} size={1} />
        <Controls showInteractive={false} />
        <MiniMap pannable zoomable nodeColor="#74669d" maskColor="#151619b8" />
      </ReactFlow>
    </div>
  );
}
