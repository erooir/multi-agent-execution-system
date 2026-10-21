import type { Item } from "./types";

export function workflowIssues(
  nodes: Item[],
  agents: Item[],
  taskType: string,
): string[] {
  const issues: string[] = [];
  if (!nodes.length) return ["请从已发布智能体添加至少一个节点"];
  const ids = new Set(nodes.map((node) => node.id));
  if (ids.size !== nodes.length) issues.push("节点标识不能重复");
  for (const node of nodes) {
    const agent = agents.find((item) => item.id === node.agent_id);
    if (!node.label.trim()) issues.push("请为每个节点填写名称");
    if (!agent || agent.status !== "published")
      issues.push(`「${node.label}」需要绑定已发布智能体`);
    else if (!(agent.capabilities || []).includes(taskType))
      issues.push(`「${node.label}」的智能体未声明此任务能力`);
    for (const dependency of node.depends_on || []) {
      if (!ids.has(dependency))
        issues.push(`「${node.label}」引用了不存在的前置节点`);
    }
  }
  const visiting = new Set<string>(),
    visited = new Set<string>();
  const byId = new Map(nodes.map((node) => [node.id, node]));
  function cyclic(id: string): boolean {
    if (visiting.has(id)) return true;
    if (visited.has(id)) return false;
    visiting.add(id);
    if (
      (byId.get(id)?.depends_on || []).some(
        (dep: string) => ids.has(dep) && cyclic(dep),
      )
    )
      return true;
    visiting.delete(id);
    visited.add(id);
    return false;
  }
  if (nodes.some((node) => cyclic(node.id)))
    issues.push("流程存在循环依赖，请移除形成回路的连线");
  return [...new Set(issues)];
}

export function connectWorkflow(
  nodes: Item[],
  source: string,
  target: string,
): Item[] {
  if (source === target) throw new Error("节点不能依赖自身");
  if (
    !nodes.some((node) => node.id === source) ||
    !nodes.some((node) => node.id === target)
  )
    throw new Error("连线两端必须是现有节点");
  // Adding source → target is invalid when source already depends on target.
  const seen = new Set<string>();
  const reaches = (id: string): boolean => {
    if (id === target) return true;
    if (seen.has(id)) return false;
    seen.add(id);
    return (nodes.find((node) => node.id === id)?.depends_on || []).some(
      reaches,
    );
  };
  if (reaches(source)) throw new Error("这条连线会形成循环，已保留原有流程");
  return nodes.map((node) =>
    node.id === target
      ? {
          ...node,
          depends_on: [...new Set([...(node.depends_on || []), source])],
        }
      : node,
  );
}

export function removeWorkflowNode(nodes: Item[], id: string): Item[] {
  return nodes
    .filter((node) => node.id !== id)
    .map((node) => ({
      ...node,
      depends_on: (node.depends_on || []).filter((dep: string) => dep !== id),
    }));
}

export const versionFields: Record<string, string> = {
  name: "名称",
  description: "职责说明",
  purpose: "用途",
  structure: "结构",
  capabilities: "任务能力",
  modalities: "输入模态",
  instructions: "执行指令",
  model_policy: "模型策略",
  execution_strategy: "运行方式",
  tool_ids: "固定工具",
  tool_refs: "工具版本授权",
  skill_refs: "技能版本授权",
  members: "成员版本",
  parameters: "执行参数",
};
function normalized(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(normalized).join(",")}]`;
  if (value && typeof value === "object")
    return `{${Object.entries(value)
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([key, item]) => `${key}:${normalized(item)}`)
      .join(",")}}`;
  return JSON.stringify(value) ?? "";
}
export function changedVersionFields(before: Item, after: Item): string[] {
  return Object.keys(versionFields).filter(
    (key) => normalized(before[key]) !== normalized(after[key]),
  );
}
