import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import {
  ArrowRight,
  Bot,
  Boxes,
  ChevronRight,
  Database,
  Layers3,
  Plug,
  Plus,
  Sparkles,
  Workflow,
  Wrench,
  X,
  type LucideIcon,
} from "lucide-react";
import { api, post } from "./api";
import { statusNames, taskTypes } from "./domain";
import type { Item, Mutation } from "./types";
import type { HubKind } from "./WbShell";
import { AgentsPage, WorkflowsPage } from "./AssetPages";
import ExpertInterview, { businessLabel } from "./ExpertInterview";
import ToolCatalog from "./ToolCatalog";
import { JsonView } from "./ui";
import { AgentCreateFields, WorkflowCreateFields } from "./Forms";
import HubIcon, {
  iconHue,
  kindHue,
  type HubIconKind,
} from "./HubIcons";

type HubItem = {
  id: string;
  name: string;
  techId?: string;
  desc: string;
  tags: string[];
  meta: string;
  group: string;
  weight: number;
  createdAt?: string;
  bucket: "primary" | "secondary";
  extra?: Item;
};
type HubLink = { id: string; name: string };
type HubCategory = {
  title: string;
  tone: string;
  icon: LucideIcon;
  count: number;
  links: HubLink[];
};

const TAB_META: Record<
  HubKind,
  { primary: string; secondary: string; bucket: string }
> = {
  expert: { primary: "智能体", secondary: "工作流", bucket: "工作流" },
  skill: { primary: "工具", secondary: "文档技能包", bucket: "技能包" },
  connector: { primary: "MCP 服务", secondary: "", bucket: "" },
};

const FIELD_LABELS: Record<string, string> = {
  general: "通用",
  specialized: "场景专用",
  atomic: "单智能体",
  composite: "协作编排",
  tool: "工具",
  tools: "工具",
  mcp: "MCP",
  skill: "技能包",
  text: "文本",
  structured: "结构化",
  image: "图像",
  audio: "音频",
  video: "视频",
  stdio: "本地进程",
  builtin: "内置",
  http: "HTTP",
  local: "本地",
  cloud: "云端",
};
const MCP_LABELS: Record<string, string> = {
  "project-tables": "项目表格服务",
};
/* The three summary cards reuse the same illustration set as the item cards. */
const HERO_ICON_KIND = new Map<LucideIcon, HubIconKind>([
  [Bot, "agent"],
  [Sparkles, "agent"],
  [Workflow, "flow"],
  [Layers3, "flow"],
  [Wrench, "tool"],
  [Boxes, "skill"],
  [Plug, "mcp"],
  [Database, "mcp"],
]);
/* 48 evenly spaced hue slots: one per item, chosen from a stable hash and
   de-duplicated within the list, so colours stay distinct and well spread. */
const HUE_SLOTS = 48;
const MIN_SLOT_GAP = 14; /* >=105 degrees between neighbours */
const slotHue = (id: string) =>
  Math.round(((iconHue(id) % HUE_SLOTS) * 360) / HUE_SLOTS);
function itemIconKind(
  item: HubItem,
  kind: HubKind,
  category: string,
): HubIconKind {
  if (kind === "expert") return category === "workflow" ? "flow" : "agent";
  if (kind === "skill") return item.bucket === "secondary" ? "skill" : "tool";
  return "mcp";
}
const pretty = (value: string) =>
  taskTypes[value] || FIELD_LABELS[value] || MCP_LABELS[value] || value;

function asLinks(rows: Item[]): HubLink[] {
  return rows.slice(0, 3).map((row) => ({
    id: String(row.id || row.name || ""),
    name: String(row.label || row.name || row.id || "未命名"),
  }));
}

export default function HubCenter({
  kind,
  projectId,
  agents,
  capabilities,
  workflows = [],
  assets = [],
  isAdmin = false,
  canEdit = false,
  mutate,
  onTask,
  onAgent,
  offline = false,
  query = "",
  onOpenFullPage,
  onCreated,
  notify,
}: {
  kind: HubKind;
  projectId: string;
  agents: Item[];
  capabilities: Item[];
  workflows?: Item[];
  assets?: Item[];
  isAdmin?: boolean;
  canEdit?: boolean;
  mutate: Mutation;
  onTask: (id: string) => void;
  onAgent: (id: string, toolId?: string) => void;
  offline?: boolean;
  connectors?: Item[];
  query?: string;
  onOpenFullPage?: (page: string) => void;
  onCreated?: () => void;
  notify?: (message: string, error?: boolean) => void;
}) {
  const [catalog, setCatalog] = useState<{
    tools: Item[];
    skills: Item[];
    mcp_servers: Item[];
  }>({ tools: [], skills: [], mcp_servers: [] });
  const [sort, setSort] = useState<"default" | "hot" | "new">("default");
  const [category, setCategory] = useState<
    "all" | "secondary" | "workflow"
  >("all");
  const [filterGroup, setFilterGroup] = useState("全部");
  const [groupBy, setGroupBy] = useState("business");
  const filterScrollRef = useRef<HTMLDivElement>(null);
  const [selected, setSelected] = useState<HubItem | null>(null);
  // Re-mount the native detail dialog when the same card is opened again.
  const [detailSeq, setDetailSeq] = useState(0);
  const [catalogTick, setCatalogTick] = useState(0);
  const [creating, setCreating] = useState(false);
  const [interviewOpen, setInterviewOpen] = useState(false);
  const [createBusy, setCreateBusy] = useState(false);
  const [createError, setCreateError] = useState("");
  const [createForm, setCreateForm] = useState<Item>({
    name: "",
    description: "",
    capability: capabilities[0]?.id || "structured_transform",
    task_type: capabilities[0]?.id || "structured_transform",
    agent_id: agents[0]?.id || "",
    transport: "stdio",
    url: "",
    launcher_id: "",
    secret_ref: "",
    file: null,
  });

  useEffect(() => {
    if (!projectId) return;
    const controller = new AbortController();
    void api<Item>(
      "/v2/capabilities?project_id=" + encodeURIComponent(projectId),
      { signal: controller.signal },
    )
      .then((value) =>
        setCatalog({
          tools: Array.isArray(value.tools) ? value.tools : [],
          skills: Array.isArray(value.skills) ? value.skills : [],
          mcp_servers: Array.isArray(value.mcp_servers)
            ? value.mcp_servers
            : [],
        }),
      )
      .catch(() => {});
    return () => controller.abort();
  }, [projectId, catalogTick]);

  useEffect(() => {
    setCategory("all");
    setFilterGroup("全部");
    setSort("default");
    setSelected(null);
    setCreating(false);
    setCreateError("");
  }, [kind]);

  function switchCategory(next: "all" | "secondary" | "workflow") {
    // The detail view mounts the original page component; leaving it mounted
    // while switching tabs would re-open the last card's dialog on return.
    setSelected(null);
    setCategory(next);
    setFilterGroup("全部");
  }

  const { items, categories } = useMemo(() => {
    if (kind === "expert" && category === "workflow") {
      const published = workflows.filter((w) => w.status === "published");
      const single = workflows.filter((w) => (w.nodes || []).length <= 1);
      const multi = workflows.filter((w) => (w.nodes || []).length > 1);
      return {
        items: workflows.map(
          (w): HubItem => ({
            id: String(w.id),
            name: String(w.name || w.id),
            desc: String(w.description || ""),
            tags: [
              pretty(String(w.task_type || "")),
              `${(w.nodes || []).length} 个节点`,
            ],
            meta: statusNames[String(w.status)] || String(w.status || ""),
            group: "工作流",
            weight: w.status === "published" ? 1 : 0,
            createdAt: w.created_at,
            bucket: "primary" as const,
            extra: w,
          }),
        ),
        categories: [
          {
            title: "已发布流程",
            tone: "violet",
            icon: Workflow,
            count: published.length,
            links: asLinks(published),
          },
          {
            title: "单节点流程",
            tone: "green",
            icon: Layers3,
            count: single.length,
            links: asLinks(single),
          },
          {
            title: "多节点流程",
            tone: "blue",
            icon: Boxes,
            count: multi.length,
            links: asLinks(multi),
          },
        ] as HubCategory[],
      };
    }
    if (kind === "expert") {
      const published = agents.filter((a) => a.status === "published");
      const builtIn = agents.filter((a) => a.built_in);
      return {
        items: agents.map(
          (a): HubItem => ({
            id: String(a.id),
            name: String(a.name || a.id),
            desc: String(a.description || a.purpose || ""),
            tags: [
              String(a.structure || ""),
              String(a.purpose || ""),
              ...(Array.isArray(a.capabilities)
                ? a.capabilities.slice(0, 1).map(String)
                : []),
            ]
              .filter(Boolean)
              .map(pretty),
            meta: statusNames[String(a.status)] || String(a.status || ""),
            group: pretty(String(a.purpose || a.structure || "其他")),
            weight: a.status === "published" || a.built_in ? 1 : 0,
            createdAt: a.created_at,
            bucket: "primary" as const,
            extra: a,
          }),
        ),
        categories: [
          {
            title: "已发布智能体",
            tone: "violet",
            icon: Bot,
            count: published.length,
            links: asLinks(published),
          },
          {
            title: "内置智能体",
            tone: "green",
            icon: Sparkles,
            count: builtIn.length,
            links: asLinks([...builtIn].reverse()),
          },
          {
            title: "能力覆盖",
            tone: "blue",
            icon: Layers3,
            count: capabilities.length,
            links: asLinks(capabilities),
          },
        ] as HubCategory[],
      };
    }
    if (kind === "skill") {
      const localTools = catalog.tools.filter((t) => !t.external);
      const mcpTools = catalog.tools.filter(
        (t) => t.kind === "mcp" || t.adapter === "mcp",
      );
      return {
        items: [
          ...catalog.tools.map(
            (t): HubItem => ({
              id: String(t.id),
              name: String(t.label || t.name || t.id),
              techId: String(t.name || t.id),
              desc: String(t.description || pretty(String(t.adapter || t.kind || ""))),
              tags: [String(t.kind || ""), t.read_only ? "只读" : "可写"]
                .filter(Boolean)
                .map(pretty),
              meta: t.external ? "外部工具" : "本地工具",
              group: pretty(String(t.kind || "工具")),
              weight: t.read_only ? 1 : 0,
              bucket: "primary" as const,
              extra: t,
            }),
          ),
          ...catalog.skills.map(
            (s): HubItem => ({
              id: String(s.id),
              name: String(s.name || s.id),
              techId: String(s.id),
              desc: String(s.description || ""),
              tags: [
                "文档技能包",
                ...(Array.isArray(s.required_tools)
                  ? s.required_tools.slice(0, 1).map(String).map(pretty)
                  : []),
              ],
              meta: `v${s.version ?? 1}`,
              group: "文档技能包",
              weight: s.external ? 0 : 1,
              bucket: "secondary" as const,
              extra: s,
            }),
          ),
        ],
        categories: [
          {
            title: "工具",
            tone: "violet",
            icon: Wrench,
            count: localTools.length,
            links: asLinks(localTools),
          },
          {
            title: "文档技能包",
            tone: "green",
            icon: Boxes,
            count: catalog.skills.length,
            links: asLinks(catalog.skills),
          },
          {
            title: "MCP 工具",
            tone: "blue",
            icon: Plug,
            count: mcpTools.length,
            links: asLinks(mcpTools),
          },
        ] as HubCategory[],
      };
    }
    // Connector: MCP services only. No duplicated capability / connector cards.
    return {
      items: catalog.mcp_servers.map(
        (m): HubItem => ({
          id: String(m.id),
          name: String(m.label || MCP_LABELS[String(m.id)] || m.id),
          techId: String(m.id),
          desc: String(m.description || `传输方式：${pretty(String(m.transport || "stdio"))}`),
          tags: ["MCP", pretty(String(m.transport || "stdio"))].filter(Boolean),
          meta: m.external ? "外部服务" : "本地服务",
          group: "MCP 服务",
          weight: m.external ? 0 : 1,
          bucket: "primary" as const,
          extra: m,
        }),
      ),
      categories: [] as HubCategory[],
    };
  }, [kind, agents, capabilities, workflows, catalog, category]);

  // Secondary filter row (expert page only), built from real asset fields.
  const isWorkflowView = kind === "expert" && category === "workflow";
  const filterGroups = useMemo(() => {
    if (kind !== "expert") return [];
    const values = isWorkflowView
      ? items.map((item) =>
          (item.extra?.nodes || []).length > 1 ? "多节点流程" : "单节点流程",
        )
      : items.flatMap((item) => [item.group, ...item.tags]);
    return [
      "全部",
      ...Array.from(new Set(values.filter(Boolean).map(String))).slice(0, 12),
    ];
  }, [kind, isWorkflowView, items]);

  function inFilterGroup(item: HubItem) {
    if (filterGroup === "全部") return true;
    if (isWorkflowView) {
      return (
        ((item.extra?.nodes || []).length > 1
          ? "多节点流程"
          : "单节点流程") === filterGroup
      );
    }
    return item.group === filterGroup || item.tags.includes(filterGroup);
  }

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    let rows = items.filter(
      (item) =>
        (category === "workflow" ||
          (category === "secondary"
            ? item.bucket === "secondary"
            : item.bucket === "primary")) &&
        inFilterGroup(item) &&
        (!q ||
          [item.name, item.desc, item.techId || "", ...item.tags].some((v) =>
            v.toLowerCase().includes(q),
          )),
    );
    if (sort === "hot") rows = [...rows].sort((a, b) => b.weight - a.weight);
    if (sort === "new")
      rows = [...rows].sort((a, b) =>
        String(b.createdAt || "").localeCompare(String(a.createdAt || "")),
      );
    return rows;
  }, [items, query, category, sort, filterGroup]);

  /* 48 evenly spaced hue slots, one per item. A stable hash picks the slot;
     neighbours must be at least ~105 degrees apart so the list never reads
     as "all green". Gaps relax only if the palette runs out of room. */
  const hues = useMemo(() => {
    const used = new Set<number>();
    let previous = -1;
    return visible.map((item) => {
      let slot = iconHue(item.id) % HUE_SLOTS;
      let chosen = -1;
      for (const gap of [MIN_SLOT_GAP, 10, 6, 0]) {
        for (let guard = 0; guard < HUE_SLOTS; guard += 1) {
          const distance =
            previous < 0
              ? HUE_SLOTS
              : Math.min(
                  (slot - previous + HUE_SLOTS) % HUE_SLOTS,
                  (previous - slot + HUE_SLOTS) % HUE_SLOTS,
                );
          if (!used.has(slot) && distance >= gap) {
            chosen = slot;
            break;
          }
          slot = (slot + 1) % HUE_SLOTS;
        }
        if (chosen >= 0) break;
      }
      if (chosen < 0) chosen = slot;
      used.add(chosen);
      previous = chosen;
      return Math.round((chosen * 360) / HUE_SLOTS);
    });
  }, [visible]);

  const meta = TAB_META[kind];
  const isWorkflow = kind === "expert" && category === "workflow";
  const isAgentList = kind === "expert" && !isWorkflow;
  const cardGroups = new Map<string, { item: HubItem; hue: number }[]>();
  visible.forEach((item, index) => {
    const agent = item.extra || {};
    const group = !isAgentList
      ? ""
      : groupBy === "business"
        ? businessLabel(agent)
        : pretty(agent.structure || "未分类");
    const cards = cardGroups.get(group) || [];
    cards.push({ item, hue: hues[index] });
    cardGroups.set(group, cards);
  });
  const managePage = isWorkflow
    ? "workflows"
    : kind === "expert"
      ? "agents"
      : kind === "skill"
        ? "tools"
        : "capabilities";
  const addLabel = isWorkflow
    ? "新建流程"
    : kind === "expert"
      ? "新建智能体"
      : kind === "skill"
        ? "导入技能包"
        : "登记 MCP 服务";
  const isCreateFull = Boolean(kind === "expert");
  const closeCreate = () => {
    setCreating(false);
    setCreateError("");
    setCatalogTick((n) => n + 1);
    onCreated?.();
  };
  const openDetail = (item: HubItem) => {
    setSelected(item);
    setDetailSeq((n) => n + 1);
  };
  const openById = (id: string, fallbackName?: string) => {
    const found = items.find((item) => item.id === id);
    openDetail(
      found || {
        id,
        name: fallbackName || pretty(id),
        techId: id,
        desc: "",
        tags: [],
        meta: "",
        group: "",
        weight: 0,
        bucket: "primary",
      },
    );
  };

  const openCreate = () => {
    setCreateForm({
      name: "",
      description: "",
      capability: capabilities[0]?.id || "structured_transform",
      transport: "stdio",
      url: "",
      launcher_id: "",
      secret_ref: "",
      file: null,
    });
    setCreateError("");
    setCreating(true);
  };

  async function submitCreate(e: FormEvent) {
    e.preventDefault();
    if (!projectId || isCreateFull) return;
    setCreateBusy(true);
    setCreateError("");
    try {
      if (kind === "connector") {
        await post("/v2/capability-definitions/mcp", {
          project_id: projectId,
          name: createForm.name,
          description: createForm.description || "",
          transport: createForm.transport,
          url: createForm.transport === "stdio" ? "" : createForm.url,
          launcher_id:
            createForm.transport === "stdio" ? createForm.launcher_id : "",
          secret_ref: createForm.secret_ref || null,
        });
        notify?.("MCP 服务已登记");
      } else {
        if (!createForm.file) throw new Error("请选择文档技能包 ZIP 文件");
        const body = new FormData();
        body.append("project_id", projectId);
        body.append("file", createForm.file);
        await api("/v2/capability-definitions/skills/import", {
          method: "POST",
          body,
        });
        notify?.("文档技能包已导入");
      }
      setCreating(false);
      setCatalogTick((n) => n + 1);
      onCreated?.();
    } catch (e) {
      setCreateError((e as Error).message);
      notify?.((e as Error).message, true);
    } finally {
      setCreateBusy(false);
    }
  }

  return (
    <div className="hub-center">
      {categories.length > 0 && (
        <div className="hub-hero-row">
          {categories.map((cat) => {
            const heroKind = HERO_ICON_KIND.get(cat.icon) || "agent";
            return (
              <div key={cat.title} className={`hub-hero tone-${cat.tone}`}>
                <div className="hub-hero-top">
                  <div>
                    <strong>{cat.title}</strong>
                    <span>{cat.count} 项</span>
                  </div>
                  <HubIcon kind={heroKind} hue={kindHue(heroKind)} size={36} />
                </div>
                <div className="hub-hero-links">
                  {cat.links.length ? (
                    cat.links.map((link) => (
                      <button
                        key={link.id}
                        onClick={() => openById(link.id, link.name)}
                      >
                        {link.name} <ChevronRight size={14} />
                      </button>
                    ))
                  ) : (
                    <span className="hub-hero-empty">暂无条目</span>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}

      <div className="hub-section-head">
        {meta.secondary ? (
          <div className="hub-section-tabs">
            <button
              className={category === "all" ? "is-active" : ""}
              onClick={() => switchCategory("all")}
            >
              {meta.primary}
            </button>
            <button
              className={
                category === "workflow" || category === "secondary"
                  ? "is-active"
                  : ""
              }
              onClick={() =>
                switchCategory(kind === "expert" ? "workflow" : "secondary")
              }
            >
              {meta.secondary}
            </button>
          </div>
        ) : (
          <div className="hub-section-tabs">
            <button className="is-active">{meta.primary}</button>
          </div>
        )}
        <div className="hub-head-actions">
          <button
            className="hub-manage"
            title="打开完整管理页：编辑、发布、停用、删除等操作"
            onClick={() => onOpenFullPage?.(managePage)}
          >
            管理
          </button>
          {isAgentList && canEdit && (
            <button className="hub-manage" onClick={() => setInterviewOpen(true)}>
              <Sparkles size={14} />
              问答创建
            </button>
          )}
          {(kind === "connector" ? isAdmin : canEdit) && (
            <button className="hub-add" onClick={openCreate}>
              <Plus size={14} />
              {addLabel}
            </button>
          )}
          <div className="hub-sort">
            {(["default", "hot", "new"] as const).map((value) => (
              <button
                key={value}
                className={sort === value ? "is-active" : ""}
                onClick={() => setSort(value)}
              >
                {value === "default"
                  ? "综合"
                  : value === "hot"
                    ? "推荐顺序"
                    : "最新"}
              </button>
            ))}
          </div>
        </div>
      </div>

      {kind === "expert" && filterGroups.length > 1 && (
        <div className="hub-filter-row">
          <div className="hub-filter-scroll" ref={filterScrollRef}>
            {filterGroups.map((name) => (
              <button
                key={name}
                className={filterGroup === name ? "is-active" : ""}
                aria-pressed={filterGroup === name}
                onClick={() => setFilterGroup(name)}
              >
                {name}
              </button>
            ))}
          </div>
          <button
            className="hub-filter-more"
            aria-label="查看更多分类"
            onClick={() =>
              filterScrollRef.current?.scrollBy({
                left: 240,
                behavior: "smooth",
              })
            }
          >
            <ChevronRight size={16} />
          </button>
        </div>
      )}

      <div className="hub-list-rule" />

      {isAgentList && (
        <label className="expert-group-select">
          智能体视图
          <select value={groupBy} onChange={(e) => setGroupBy(e.target.value)}>
            <option value="business">按业务方向</option>
            <option value="structure">按智能体类型</option>
          </select>
        </label>
      )}
      {cardGroups.size ? (
        [...cardGroups].map(([group, cards]) => (
          <section key={group} className={isAgentList ? "expert-group" : undefined}>
            {isAgentList && <h3>{group} <small>({cards.length})</small></h3>}
            <div className="hub-card-grid">
          {cards.map(({ item, hue }) => (
            <button
              key={item.id}
              className="hub-card"
              onClick={() => openDetail(item)}
            >
              <HubIcon
                kind={itemIconKind(item, kind, category)}
                hue={hue}
                size={48}
              />
              <span className="hub-card-body">
                <strong>{item.name}</strong>
                <span className="hub-card-desc">{item.desc || "—"}</span>
                <span className="hub-card-tags">
                  {item.tags.slice(0, 3).map((tag) => (
                    <small key={tag}>{tag}</small>
                  ))}
                </span>
                <span className="hub-card-foot">
                  <span>{item.meta || "—"}</span>
                  <span className="hub-card-open">
                    查看 <ArrowRight size={12} />
                  </span>
                </span>
              </span>
            </button>
          ))}
            </div>
          </section>
        ))
      ) : (
        <div className="hub-empty">没有匹配的条目，换个关键词或分类试试。</div>
      )}

      {selected && (
        <>
          {kind === "expert" && category !== "workflow" && (
            <div className="hub-native-dialog">
              <AgentsPage
                key={`agent-${selected.id}-${detailSeq}`}
                projectId={projectId}
                agents={agents}
                capabilities={capabilities}
                canEdit={canEdit}
                mutate={mutate}
                notify={notify || (() => {})}
                onTask={onTask}
                workflows={workflows}
                assets={assets}
                initialId={selected.id}
                offline={offline}
              />
            </div>
          )}
          {kind === "expert" && category === "workflow" && (
            <div className="hub-native-dialog">
              <WorkflowsPage
                key={`flow-${selected.id}-${detailSeq}`}
                projectId={projectId}
                workflows={workflows}
                agents={agents}
                canEdit={canEdit}
                mutate={mutate}
                notify={notify || (() => {})}
                initialId={selected.id}
              />
            </div>
          )}
          {kind === "skill" && (
            <div className="hub-native-tool">
              <div
                className="hub-native-backdrop"
                onClick={() => setSelected(null)}
                role="presentation"
              />
              <button
                className="hub-native-close"
                aria-label="关闭详情"
                onClick={() => setSelected(null)}
              >
                <X size={15} />
                关闭详情
              </button>
              <ToolCatalog
                key={`tool-${selected.id}-${detailSeq}`}
                projectId={projectId}
                agents={agents}
                canEdit={canEdit}
                onAgent={onAgent}
                initialId={selected.id}
                initialKind={
                  selected.bucket === "secondary" ? "skills" : "tools"
                }
              />
            </div>
          )}
          {kind === "connector" && (
            <McpDetail item={selected} onClose={() => setSelected(null)} />
          )}
        </>
      )}

      {interviewOpen && isAgentList && canEdit && (
        <ExpertInterview
          projectId={projectId}
          onClose={() => setInterviewOpen(false)}
          onSaved={() => {
            setInterviewOpen(false);
            onCreated?.();
            notify?.("智能体草稿已保存");
          }}
        />
      )}

      {creating && (
        <div
          className="hub-modal-backdrop"
          onClick={() => setCreating(false)}
          role="presentation"
        >
          <div
            className="hub-modal hub-create-modal"
            role="dialog"
            aria-modal="true"
            aria-label={addLabel}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="hub-modal-head">
              <span className="hub-card-avatar tone-1">
                <Plus size={20} />
              </span>
              <div>
                <h2>{addLabel}</h2>
                <small>调用平台已有的创建接口，不会发起模型调用</small>
              </div>
              <button
                className="hub-modal-close"
                aria-label="关闭添加"
                onClick={() => setCreating(false)}
              >
                <X size={18} />
              </button>
            </div>
            {isCreateFull ? (
              <>
                <div className="hub-modal-body hub-create-body">
                  {isWorkflow ? (
                    <WorkflowCreateFields
                      projectId={projectId}
                      agents={agents}
                      mutate={mutate}
                      onClose={closeCreate}
                    />
                  ) : (
                    <AgentCreateFields
                      projectId={projectId}
                      agents={agents}
                      capabilities={capabilities}
                      offline={offline}
                      mutate={mutate}
                      onClose={closeCreate}
                    />
                  )}
                </div>
                <div className="hub-modal-foot">
                  <button type="button" onClick={() => setCreating(false)}>
                    取消
                  </button>
                  <button
                    type="submit"
                    form={
                      isWorkflow
                        ? "hub-workflow-create-form"
                        : "hub-agent-create-form"
                    }
                    className="is-primary"
                  >
                    确认添加
                  </button>
                </div>
              </>
            ) : (
            <form onSubmit={submitCreate}>
              <div className="hub-modal-body">
                {kind === "skill" ? (
                  <label className="hub-form-field">
                    <span>文档技能包 ZIP</span>
                    <input
                      required
                      type="file"
                      accept=".zip"
                      onChange={(e) =>
                        setCreateForm({
                          ...createForm,
                          file: e.target.files?.[0] || null,
                        })
                      }
                    />
                  </label>
                ) : (
                  <>
                    <label className="hub-form-field">
                      <span>名称</span>
                      <input
                        required
                        autoFocus
                        value={createForm.name}
                        onChange={(e) =>
                          setCreateForm({ ...createForm, name: e.target.value })
                        }
                        placeholder={
                          isWorkflow
                            ? "例如：月度数据核查流程"
                            : kind === "expert"
                              ? "例如：月度数据核查智能体"
                              : "例如：项目表格 MCP 服务"
                        }
                      />
                    </label>
                    <label className="hub-form-field">
                      <span>说明（可选）</span>
                      <input
                        value={createForm.description}
                        onChange={(e) =>
                          setCreateForm({
                            ...createForm,
                            description: e.target.value,
                          })
                        }
                      />
                    </label>
                    {isWorkflow ? (
                      <>
                        <label className="hub-form-field">
                          <span>任务类型</span>
                          <select
                            value={createForm.task_type}
                            onChange={(e) =>
                              setCreateForm({
                                ...createForm,
                                task_type: e.target.value,
                              })
                            }
                          >
                            {capabilities.map((c) => (
                              <option key={c.id} value={c.id}>
                                {c.name || c.id}
                              </option>
                            ))}
                          </select>
                        </label>
                        <label className="hub-form-field">
                          <span>首节点智能体</span>
                          <select
                            value={createForm.agent_id}
                            onChange={(e) =>
                              setCreateForm({
                                ...createForm,
                                agent_id: e.target.value,
                              })
                            }
                          >
                            {agents.map((a) => (
                              <option key={a.id} value={a.id}>
                                {a.name || a.id}
                              </option>
                            ))}
                          </select>
                        </label>
                      </>
                    ) : kind === "expert" ? (
                      <label className="hub-form-field">
                        <span>能力</span>
                        <select
                          value={createForm.capability}
                          onChange={(e) =>
                            setCreateForm({
                              ...createForm,
                              capability: e.target.value,
                            })
                          }
                        >
                          {capabilities.map((c) => (
                            <option key={c.id} value={c.id}>
                              {c.name || c.id}
                            </option>
                          ))}
                        </select>
                      </label>
                    ) : (
                      <>
                        <label className="hub-form-field">
                          <span>传输方式</span>
                          <select
                            value={createForm.transport}
                            onChange={(e) =>
                              setCreateForm({
                                ...createForm,
                                transport: e.target.value,
                              })
                            }
                          >
                            <option value="stdio">
                              stdio · 管理员预装 launcher
                            </option>
                            <option value="streamable_http">
                              Streamable HTTP
                            </option>
                          </select>
                        </label>
                        {createForm.transport === "stdio" ? (
                          <label className="hub-form-field">
                            <span>Launcher ID</span>
                            <input
                              required
                              value={createForm.launcher_id}
                              onChange={(e) =>
                                setCreateForm({
                                  ...createForm,
                                  launcher_id: e.target.value,
                                })
                              }
                            />
                          </label>
                        ) : (
                          <label className="hub-form-field">
                            <span>MCP URL</span>
                            <input
                              required
                              type="url"
                              value={createForm.url}
                              onChange={(e) =>
                                setCreateForm({
                                  ...createForm,
                                  url: e.target.value,
                                })
                              }
                            />
                          </label>
                        )}
                        <label className="hub-form-field">
                          <span>密钥引用（可选）</span>
                          <input
                            value={createForm.secret_ref}
                            onChange={(e) =>
                              setCreateForm({
                                ...createForm,
                                secret_ref: e.target.value,
                              })
                            }
                          />
                        </label>
                      </>
                    )}
                  </>
                )}
                {createError && <p className="hub-form-error">{createError}</p>}
              </div>
              <div className="hub-modal-foot">
                <button type="button" onClick={() => setCreating(false)}>
                  取消
                </button>
                <button
                  type="submit"
                  className="is-primary"
                  disabled={createBusy}
                >
                  {createBusy ? "正在保存…" : "确认添加"}
                </button>
              </div>
            </form>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

/* Connector cards show the real MCP server catalog entry. There is no separate
   definition-version dialog for these rows, so the full raw record is shown. */
function McpDetail({
  item,
  onClose,
}: {
  item: HubItem;
  onClose: () => void;
}) {
  const raw = item.extra || {};
  const fields: [string, string][] = [
    ["服务标识", String(raw.id || item.techId || item.id)],
    ["中文名称", item.name],
    [
      "传输方式",
      raw.transport === "stdio"
        ? "本地进程（stdio）"
        : String(raw.transport || "—"),
    ],
    ["目录版本", raw.version != null ? `v${raw.version}` : "—"],
    [
      "运行时契约版本",
      raw.runtime_contract_version != null
        ? `v${raw.runtime_contract_version}`
        : "—",
    ],
    ["部署位置", raw.external ? "外部服务" : "本项目本地"],
    ["版本校验值", String(raw.checksum || "—")],
  ];
  return (
    <div
      className="hub-modal-backdrop"
      onClick={onClose}
      role="presentation"
    >
      <div
        className="hub-modal hub-detail-modal"
        role="dialog"
        aria-modal="true"
        aria-label={item.name}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="hub-modal-head">
          <HubIcon kind="mcp" hue={slotHue(item.id)} size={38} />
          <div>
            <h2>{item.name}</h2>
            <small>MCP 服务 · 完整目录条目</small>
          </div>
          <button
            className="hub-modal-close"
            aria-label="关闭详情"
            onClick={onClose}
          >
            <X size={18} />
          </button>
        </div>
        <div className="hub-modal-body hub-detail-body">
          <p className="hub-detail-lead">
            {item.desc || "本项目已登记的 MCP 服务，工具调用受版本与授权约束。"}
          </p>
          <div className="hub-detail-grid">
            {fields.map(([label, value]) => (
              <div key={label}>
                <span>{label}</span>
                <strong>{value}</strong>
              </div>
            ))}
          </div>
          <JsonView value={raw} label="完整目录条目（接口原始数据）" />
        </div>
        <div className="hub-modal-foot">
          <button onClick={onClose}>关闭</button>
        </div>
      </div>
    </div>
  );
}
