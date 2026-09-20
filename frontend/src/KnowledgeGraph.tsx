import { useMemo } from "react";
import { Background, Controls, MarkerType, ReactFlow } from "@xyflow/react";
import type { RecordData } from "./api";

export default function KnowledgeGraph({
  graph,
  onSource,
}: {
  graph: RecordData;
  onSource: (edge: RecordData) => void;
}) {
  const nodes = useMemo(
    () =>
      (graph.nodes || []).map((node: RecordData, index: number) => ({
        id: node.id,
        data: { label: node.label || node.name || node.id },
        position: {
          x:
            320 +
            Math.cos((index * 2 * Math.PI) / Math.max(graph.nodes.length, 1)) *
              290,
          y:
            220 +
            Math.sin((index * 2 * Math.PI) / Math.max(graph.nodes.length, 1)) *
              170,
        },
        style: {
          border: "1px solid var(--border-strong)",
          borderRadius: 8,
          color: "var(--text-primary)",
          background: "var(--surface-2)",
          fontSize: 13,
          padding: 15,
          width: 155,
        },
      })),
    [graph],
  );
  const edges = useMemo(
    () =>
      (graph.edges || []).map((edge: RecordData, index: number) => ({
        id: edge.id || `relationship-${index}`,
        source: edge.source,
        target: edge.target,
        label: edge.label || edge.relation || "关联",
        data: edge,
        markerEnd: {
          type: MarkerType.ArrowClosed,
          color: "var(--canvas-edge)",
        },
      })),
    [graph],
  );
  return (
    <div className="knowledge-graph-canvas">
      <ReactFlow
        colorMode="dark"
        nodes={nodes}
        edges={edges}
        onEdgeClick={(_, edge) => onSource(edge.data || {})}
        nodesDraggable={false}
        nodesConnectable={false}
        fitView
        minZoom={0.2}
        maxZoom={1.8}
        attributionPosition="bottom-right"
      >
        <Background gap={24} size={1} color="var(--canvas-dot)" />
        <Controls showInteractive={false} />
      </ReactFlow>
      <div className="graph-source-hint">点击关系连线查看原始来源</div>
    </div>
  );
}
