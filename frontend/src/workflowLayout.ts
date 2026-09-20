type LayoutNode = {
  id: string;
  position: { x: number; y: number };
  measured?: { width?: number; height?: number };
};
type LayoutEdge = { source: string; target: string };

// Match .task-node in styles.css; leave room for branch labels and connector paths.
export const WORKFLOW_NODE_SIZE = { width: 268, height: 156 };
const HORIZONTAL_GAP = 100;
const VERTICAL_GAP = 64;
const nodeSize = (node: LayoutNode) => ({
  width: Math.max(WORKFLOW_NODE_SIZE.width, node.measured?.width || 0),
  height: Math.max(WORKFLOW_NODE_SIZE.height, node.measured?.height || 0),
});

/** Check card bounds plus the small space occupied by handles and branch labels. */
export function hasOverlappingWorkflowNodes(nodes: LayoutNode[]): boolean {
  return nodes.some((node, index) =>
    nodes.slice(index + 1).some((other) => {
      const a = nodeSize(node),
        b = nodeSize(other);
      return (
        node.position.x < other.position.x + b.width + 48 &&
        other.position.x < node.position.x + a.width + 48 &&
        node.position.y < other.position.y + b.height + 16 &&
        other.position.y < node.position.y + a.height + 16
      );
    }),
  );
}

/** Upgrade only overlapping legacy layouts in memory; callers choose when to save. */
export function prepareWorkflowLayout<T extends LayoutNode>(
  nodes: T[],
  edges: LayoutEdge[],
) {
  if (!hasOverlappingWorkflowNodes(nodes)) return { nodes, adjusted: false };
  try {
    return { nodes: layoutWorkflow(nodes, edges), adjusted: true };
  } catch {
    // Invalid graphs remain available for correction and validation in the editor.
    return { nodes, adjusted: false };
  }
}

/** Position a DAG from left to right without changing node data or edge meaning. */
export function layoutWorkflow<T extends LayoutNode>(
  nodes: T[],
  edges: LayoutEdge[],
): T[] {
  if (!nodes.length) return [];
  const columnWidth =
    Math.max(...nodes.map((node) => nodeSize(node).width)) + HORIZONTAL_GAP;
  const rowHeight =
    Math.max(...nodes.map((node) => nodeSize(node).height)) + VERTICAL_GAP;
  const byId = new Map(nodes.map((node) => [node.id, node]));
  if (byId.size !== nodes.length)
    throw new Error("节点 ID 重复，请先修复后再整理。");
  const children = new Map(nodes.map((node) => [node.id, new Set<string>()]));
  const parents = new Map(nodes.map((node) => [node.id, new Set<string>()]));
  const depth = new Map(nodes.map((node) => [node.id, 0]));
  for (const edge of edges) {
    if (!byId.has(edge.source) || !byId.has(edge.target))
      throw new Error("存在连接到已删除节点的连线，请先校验流程。");
    children.get(edge.source)!.add(edge.target);
    parents.get(edge.target)!.add(edge.source);
  }
  const remaining = new Map(
    [...parents].map(([id, entries]) => [id, entries.size]),
  );
  const stableOrder = (a: string, b: string) =>
    byId.get(a)!.position.y - byId.get(b)!.position.y ||
    byId.get(a)!.position.x - byId.get(b)!.position.x ||
    a.localeCompare(b);
  const queue = nodes
    .filter((node) => remaining.get(node.id) === 0)
    .map((node) => node.id)
    .sort(stableOrder);
  let visited = 0;
  while (queue.length) {
    const id = queue.shift()!;
    visited++;
    for (const child of children.get(id)!) {
      depth.set(child, Math.max(depth.get(child)!, depth.get(id)! + 1));
      remaining.set(child, remaining.get(child)! - 1);
      if (remaining.get(child) === 0) queue.push(child);
    }
  }
  if (visited !== nodes.length)
    throw new Error("流程包含循环，无法按层整理。请先检查循环连线。");
  const layers = new Map<number, string[]>();
  for (const node of nodes) {
    const level = depth.get(node.id)!;
    layers.set(level, [...(layers.get(level) || []), node.id]);
  }
  const maxLayerSize = Math.max(
    ...[...layers.values()].map((layer) => layer.length),
  );
  const order = new Map<string, number>();
  const positions = new Map<string, { x: number; y: number }>();
  for (const [level, ids] of [...layers].sort(([a], [b]) => a - b)) {
    const parentCenter = (id: string) => {
      const values = [...parents.get(id)!].map(
        (parent) => order.get(parent) || 0,
      );
      return values.length
        ? values.reduce((a, b) => a + b, 0) / values.length
        : 0;
    };
    ids.sort((a, b) => parentCenter(a) - parentCenter(b) || stableOrder(a, b));
    ids.forEach((id, index) => {
      order.set(id, index);
      positions.set(id, {
        x: 100 + level * columnWidth,
        y: 100 + ((maxLayerSize - ids.length) / 2 + index) * rowHeight,
      });
    });
  }
  return nodes.map((node) => ({ ...node, position: positions.get(node.id)! }));
}
