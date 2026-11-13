import type { Item } from "./types";

export const toolGroups = {
  sources: "通用资料",
  mcp: "MCP 表格",
  evidence: "文档与媒体证据",
  analysis: "统计与空间分析",
  media: "多模态读取",
  scripts: "隔离脚本处理",
  other: "其他工具",
};

export function toolGroup(tool: Item): keyof typeof toolGroups {
  if (tool.kind === "mcp") return "mcp";
  const namespace = tool.id.split(".")[0];
  if (namespace === "data") return "sources";
  if (namespace === "evidence") return "evidence";
  if (
    ["document", "image", "pdf", "audio", "video", "media"].includes(namespace)
  )
    return "media";
  if (namespace === "script") return "scripts";
  if (["table", "stats", "series", "geo", "raster"].includes(namespace))
    return "analysis";
  return "other";
}

export type CatalogKind = "tools" | "skills";

export function eligibleAgents(
  agents: Item[],
  projectId: string,
  spec: Item,
  kind: CatalogKind,
): Item[] {
  const key = kind === "tools" ? "tool_id" : "skill_id";
  const refs = kind === "tools" ? "tool_refs" : "skill_refs";
  return agents.filter((agent) => {
    if (
      agent.project_id !== projectId ||
      agent.status !== "published" ||
      !agent.current_version ||
      !spec.checksum
    )
      return false;
    if (
      kind === "tools" &&
      agent.role_contract &&
      !agent.role_contract.grants?.includes(spec.id)
    )
      return false;
    const matchesVersion = (value: unknown) =>
      String(value) === String(spec.version);
    return (
      agent[refs]?.some(
        (ref: Item) => ref[key] === spec.id && matchesVersion(ref.version),
      ) &&
      agent.runtime_bindings?.[kind]?.some(
        (frozen: Item) =>
          frozen.id === spec.id &&
          matchesVersion(frozen.version) &&
          frozen.checksum === spec.checksum,
      )
    );
  });
}

export function catalogMatches(item: Item, query: string, group?: string) {
  const words = query.trim().toLowerCase().split(/\s+/);
  const text = [item.label, item.name, item.id, item.description, group]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
  return words.every((word) => text.includes(word));
}
