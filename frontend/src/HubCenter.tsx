import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import {
  ArrowRight,
  Bot,
  Boxes,
  ChevronRight,
  Database,
  Layers3,
  Plus,
  Sparkles,
  Search,
  Workflow,
  Wrench,
  X,
  type LucideIcon,
} from "lucide-react";
import { api, post } from "./api";
import { statusNames, taskTypes } from "./domain";
import type { Item, Mutation, Notify } from "./types";
import type { HubKind } from "./WbShell";
import { AgentDetailsDialog, WorkflowDetailsDialog } from "./AssetPages";
import CapabilityPages from "./CapabilityPages";
import { agentScopeLabel, candidateStatusLabels, isRestrictedAgent, isTaskScoped } from "./agentScopeModel";
import { stageNames } from "./agentPreviewModel";
import { businessLabel, businessLabels, caseBusiness } from "./agentBusiness";
import { mcpConnectorId } from "./toolCatalogModel";
import ExpertInterview from "./ExpertInterview";
import ToolCatalog from "./ToolCatalog";
import { Badge, Empty, ErrorLine, Field, FormModal, Modal } from "./ui";
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
type HubLink = { id: string; name: string; target: "asset" | "capability" };
type HubCategory = {
  title: string;
  tone: string;
  icon: LucideIcon;
  count: number;
  links: HubLink[];
  countUnit?: string;
};

const TAB_META: Record<
  HubKind,
  { primary: string; secondary: string; bucket: string }
> = {
  expert: { primary: "智能体", secondary: "工作流", bucket: "工作流" },
  skill: { primary: "工具", secondary: "文档技能包", bucket: "技能包" },
  connector: { primary: "连接器", secondary: "", bucket: "" },
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
/* The three summary cards reuse the same illustration set as the item cards. */
const HERO_ICON_KIND = new Map<LucideIcon, HubIconKind>([
  [Bot, "agent"],
  [Sparkles, "agent"],
  [Workflow, "flow"],
  [Layers3, "flow"],
  [Wrench, "tool"],
  [Boxes, "skill"],
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
  taskTypes[value] || FIELD_LABELS[value] || value;
const businessGroups: readonly string[] = Object.values(businessLabels);

function asLinks(rows: Item[], target: HubLink["target"] = "asset"): HubLink[] {
  return rows.slice(0, 3).map((row) => ({
    id: String(row.id || row.name || ""),
    target,
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
  onWorkflow,
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
  query?: string;
  onOpenFullPage?: (page: "tools" | "capabilities") => void;
  onWorkflow: (id: string) => void;
  onCreated?: () => void;
  notify: Notify;
}) {
  const [catalog, setCatalog] = useState<{
    tools: Item[];
    skills: Item[];
  }>({ tools: [], skills: [] });
  const [connectors, setConnectors] = useState<Item[]>([]);
  const [connectorError, setConnectorError] = useState("");
  const [sort, setSort] = useState<"default" | "hot" | "new">("default");
  const [category, setCategory] = useState<
    "all" | "secondary" | "workflow"
  >("all");
  const [filterGroup, setFilterGroup] = useState("全部");
  const [groupBy, setGroupBy] = useState("business");
  const [search, setSearch] = useState("");
  const [purpose, setPurpose] = useState("all");
  const [structure, setStructure] = useState("all");
  const [status, setStatus] = useState("all");
  const [stage, setStage] = useState("all");
  const [strategy, setStrategy] = useState("all");
  const [scope, setScope] = useState("all");
  const filterScrollRef = useRef<HTMLDivElement>(null);
  const [selected, setSelected] = useState<HubItem | null>(null);
  const [capabilityId, setCapabilityId] = useState<string | null>(null);
  const [managingConnector, setManagingConnector] = useState(false);
  const [connectorTool, setConnectorTool] = useState<Item | null>(null);
  const [catalogTick, setCatalogTick] = useState(0);
  const [creating, setCreating] = useState(false);
  const [interviewOpen, setInterviewOpen] = useState(false);
  const [dataServiceOpen, setDataServiceOpen] = useState(false);
  const [dataService, setDataService] = useState({ name: "", base_url: "", description: "", automatic_intake: false, allow_public_results: false });
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
        }),
      )
      .catch(() => {});
    return () => controller.abort();
  }, [projectId, catalogTick]);

  useEffect(() => {
    if (kind !== "connector") return;
    const controller = new AbortController();
    setConnectorError("");
    api<Item[]>("/v2/project-connectors?project_id=" + encodeURIComponent(projectId), { signal: controller.signal })
      .then((rows) => { if (!controller.signal.aborted) setConnectors(rows); })
      .catch((error) => { if (!controller.signal.aborted) setConnectorError(error.message); });
    return () => controller.abort();
  }, [kind, projectId, catalogTick]);

  useEffect(() => {
    setCategory("all");
    setFilterGroup("全部");
    setSort("default");
    setSelected(null);
    setCreating(false);
    setCreateError("");
  }, [kind]);

  function switchCategory(next: "all" | "secondary" | "workflow") {
    setSelected(null);
    setCapabilityId(null);
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
            techId: caseBusiness(a)?.caseId,
            desc: String(a.description || a.purpose || ""),
            tags: [
              agentScopeLabel(a),
              candidateStatusLabels[a.candidate_status] || a.candidate_status || "",
              String(a.structure || ""),
              String(a.purpose || ""),
              ...(Array.isArray(a.capabilities)
                ? a.capabilities.slice(0, 1).map(String)
                : []),
            ]
              .filter(Boolean)
              .map(pretty),
            meta: statusNames[String(a.status)] || String(a.status || ""),
            group: businessLabel(a),
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
            countUnit: "类平台能力",
            links: asLinks(capabilities, "capability"),
          },
        ] as HubCategory[],
      };
    }
    if (kind === "skill") {
      const tools = catalog.tools.filter((tool) => !mcpConnectorId(tool));
      return {
        items: [
          ...tools.map(
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
            count: tools.length,
            links: asLinks(tools),
          },
          {
            title: "文档技能包",
            tone: "green",
            icon: Boxes,
            count: catalog.skills.length,
            links: asLinks(catalog.skills),
          },
        ] as HubCategory[],
      };
    }
    return {
      items: connectors.map(
        (m): HubItem => ({
          id: String(m.id),
          name: String(m.label || m.name || m.id),
          techId: String(m.id),
          desc: String(m.description || (m.builtin ? "对项目授权的表格执行筛选、去重和统计。" : "")),
          tags: [m.kind === "mcp" ? "MCP" : "上游数据", pretty(String(m.transport || ""))].filter(Boolean),
          meta: statusNames[m.status] || m.status,
          group: "连接器",
          weight: m.builtin ? 1 : 0,
          bucket: "primary" as const,
          extra: m,
        }),
      ),
      categories: [] as HubCategory[],
    };
  }, [kind, agents, capabilities, workflows, catalog, connectors, category]);

  const connectorLink = useRef(new URLSearchParams(window.location.search).get("connector"));
  useEffect(() => {
    if (kind !== "connector" || !connectorLink.current) return;
    const found = items.find((item) => item.id === connectorLink.current);
    if (found) {
      setSelected(found);
      connectorLink.current = null;
    }
  }, [kind, items]);

  const isWorkflow = kind === "expert" && category === "workflow";
  const isAgentList = kind === "expert" && !isWorkflow;
  const filterGroups = useMemo(() => {
    if (kind !== "expert") return [];
    if (isAgentList) return ["全部", ...businessGroups];
    const values = items.map((item) =>
      (item.extra?.nodes || []).length > 1 ? "多节点流程" : "单节点流程",
    );
    return ["全部", ...new Set(values)];
  }, [kind, isAgentList, items]);

  function inFilterGroup(item: HubItem) {
    if (filterGroup === "全部") return true;
    if (isWorkflow) {
      return (
        ((item.extra?.nodes || []).length > 1
          ? "多节点流程"
          : "单节点流程") === filterGroup
      );
    }
    return item.group === filterGroup;
  }

  const visible = useMemo(() => {
    const agentQuery = search.trim().toLowerCase();
    const q = query.trim().toLowerCase();
    let rows = items.filter(
      (item) => {
        const a = item.extra || {};
        if (isAgentList) {
          if (a.status === "destroyed") return false;
          const matchesAgent =
            (purpose === "all" || a.purpose === purpose) &&
            (structure === "all" || a.structure === structure) &&
            (status === "all" || a.status === status) &&
            (scope === "all" || (scope === "reusable" ? !isRestrictedAgent(a) : a.scope === scope)) &&
            (stage === "all" || (a.case_role || "other") === stage) &&
            (strategy === "all" ||
              (strategy === "dual"
                ? a.executor_bindings?.local && a.executor_bindings?.cloud
                : (a.execution_strategy || "fixed") === strategy)) &&
            (!agentQuery || [item.name, item.desc, item.group, item.techId, a.role_contract?.operation, ...item.tags]
              .join(" ").toLowerCase().includes(agentQuery));
          if (!matchesAgent) return false;
        }
        return (
          (category === "workflow" ||
            item.bucket === (category === "secondary" ? "secondary" : "primary")) &&
          inFilterGroup(item) &&
          (!q || [item.name, item.desc, item.techId || "", isAgentList ? item.group : "", ...item.tags].some((v) =>
            v.toLowerCase().includes(q),
          ))
        );
      },
    );
    if (sort === "hot") rows = [...rows].sort((a, b) => b.weight - a.weight);
    if (sort === "new")
      rows = [...rows].sort((a, b) =>
        String(b.createdAt || "").localeCompare(String(a.createdAt || "")),
      );
    return rows;
  }, [items, query, isAgentList, category, sort, filterGroup, search, purpose, structure, status, stage, strategy, scope]);

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
  const scoped = agents.filter((a) => a.status !== "destroyed");
  const published = scoped.filter((a) => a.status === "published" && !isRestrictedAgent(a));
  const hasFilters =
    !!search || filterGroup !== "全部" ||
    [purpose, structure, status, stage, strategy, scope].some(
      (v) => v !== "all",
    );
  function clearFilters() {
    setSearch("");
    setFilterGroup("全部");
    setPurpose("all");
    setStructure("all");
    setStatus("all");
    setStage("all");
    setStrategy("all");
    setScope("all");
  }
  const cardGroups = new Map<string, { item: HubItem; hue: number }[]>();
  visible.forEach((item, index) => {
    const agent = item.extra || {};
    const group = !isAgentList
      ? ""
      : groupBy === "business"
        ? item.group
        : pretty(agent.structure || "未分类");
    const cards = cardGroups.get(group) || [];
    cards.push({ item, hue: hues[index] });
    cardGroups.set(group, cards);
  });
  const orderedGroups = [...cardGroups];
  if (isAgentList && groupBy === "business") {
    orderedGroups.sort(([a], [b]) => businessGroups.indexOf(a) - businessGroups.indexOf(b));
  }
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
  function closeConnectorManager() {
    setManagingConnector(false);
    setCatalogTick((tick) => tick + 1);
  }
  const openDetail = (item: HubItem) => {
    setSelected(item);
  };
  function openHeroLink(link: HubLink) {
    if (link.target === "capability") {
      setCapabilityId(link.id);
      return;
    }
    const item = items.find((item) => item.id === link.id);
    if (item) openDetail(item);
  }

  const capability = capabilities.find((item) => item.id === capabilityId);
  const matchingAgents = capability
    ? scoped.filter((agent) => agent.capabilities?.includes(capability.id))
    : [];

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
      {connectorError && <ErrorLine>{connectorError}</ErrorLine>}
      {categories.length > 0 && (
        <div className="hub-hero-row">
          {categories.map((cat) => {
            const heroKind = HERO_ICON_KIND.get(cat.icon) || "agent";
            return (
              <div key={cat.title} className={`hub-hero tone-${cat.tone}`}>
                <div className="hub-hero-top">
                  <div>
                    <strong>{cat.title}</strong>
                    <span>{cat.count} {cat.countUnit || "项"}</span>
                  </div>
                  <HubIcon kind={heroKind} hue={kindHue(heroKind)} size={36} />
                </div>
                <div className="hub-hero-links">
                  {cat.links.length ? (
                    cat.links.map((link) => (
                      <button
                        key={link.id}
                        onClick={() => openHeroLink(link)}
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
          {kind === "skill" && (
            <button className="hub-manage" onClick={() => onOpenFullPage?.("tools")}>工具箱</button>
          )}
          {kind === "skill" && canEdit && (
            <button className="hub-interview" onClick={() => onOpenFullPage?.("capabilities")}>扩展管理</button>
          )}
          {isAgentList && canEdit && (
            <button className="hub-interview" onClick={() => setInterviewOpen(true)}>
              <Sparkles size={14} />
              对话创建
            </button>
          )}
          {kind === "connector" && isAdmin && (
            <button className="hub-add" onClick={() => setDataServiceOpen(true)}>接入数据服务</button>
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
      {isAgentList && (
        <details className="hub-agent-filters">
          <summary>智能体筛选与统计</summary>
            <div className="agent-registry-counts" aria-label="当前项目资产概况">
              <div>
                <span>项目内定义</span>
                <strong>{scoped.length}</strong>
              </div>
              <div>
                <span>可复用 · 已发布</span>
                <strong>{published.length}</strong>
              </div>
              <div>
                <span>仅当前任务</span>
                <strong>{scoped.filter(isTaskScoped).length}</strong>
              </div>
              <div>
                <span>已发布 · 固定流程执行</span>
                <strong>
                  {
                    published.filter(
                      (a) =>
                        (a.execution_strategy || "fixed") === "fixed" &&
                        !(a.executor_bindings?.local && a.executor_bindings?.cloud),
                    ).length
                  }
                </strong>
              </div>
              <div>
                <span>已发布 · 自主选工具</span>
                <strong>
                  {
                    published.filter(
                      (a) =>
                        a.execution_strategy === "tool_agent" &&
                        !(a.executor_bindings?.local && a.executor_bindings?.cloud),
                    ).length
                  }
                </strong>
              </div>
              <div>
                <span>已发布 · 本地 / 云端双模式</span>
                <strong>
                  {
                    published.filter(
                      (a) => a.executor_bindings?.local && a.executor_bindings?.cloud,
                    ).length
                  }
                </strong>
              </div>
            </div>
            <div className="toolbar registry-filters">
              <div className="search">
                <Search size={17} />
                <input
                  aria-label="搜索智能体"
                  placeholder="搜索名称、职责、业务方向、用例编号或算法"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                />
              </div>
              <select
                aria-label="智能体使用范围"
                value={scope}
                onChange={(e) => setScope(e.target.value)}
              >
                <option value="all">全部使用范围</option>
                <option value="reusable">可复用资产</option>
                <option value="task">仅当前任务</option>
                <option value="evaluation">仅独立评测</option>
                <option value="candidate">候选未入库</option>
              </select>
              <select
                aria-label="发布状态"
                value={status}
                onChange={(e) => setStatus(e.target.value)}
              >
                <option value="all">全部状态</option>
                <option value="published">已发布</option>
                <option value="draft">草稿</option>
                <option value="disabled">已停用</option>
                <option value="retired">已退役</option>
              </select>
              <select
                aria-label="执行方式筛选"
                value={strategy}
                onChange={(e) => setStrategy(e.target.value)}
              >
                <option value="all">全部执行方式</option>
                <option value="fixed">固定流程执行</option>
                <option value="tool_agent">云端工具循环（含双模式）</option>
                <option value="dual">本地 / 云端双模式用例</option>
              </select>
              <select
                aria-label="用例岗位分工"
                value={stage}
                onChange={(e) => setStage(e.target.value)}
              >
                <option value="all">全部用例岗位</option>
                {Object.entries(stageNames).map(([id, label]) => (
                  <option key={id} value={id}>
                    {label}
                  </option>
                ))}
              </select>
              <select
                aria-label="用途分类"
                value={purpose}
                onChange={(e) => setPurpose(e.target.value)}
              >
                <option value="all">全部用途</option>
                <option value="general">通用智能体</option>
                <option value="specialized">任务专用智能体</option>
              </select>
              <select
                aria-label="结构分类"
                value={structure}
                onChange={(e) => setStructure(e.target.value)}
              >
                <option value="all">全部结构</option>
                <option value="atomic">元智能体</option>
                <option value="composite">复合智能体</option>
              </select>
              <span className="muted" aria-live="polite">
                显示 {visible.length} / {scoped.length}
              </span>
              {hasFilters && (
                <button className="text-button" onClick={clearFilters}>
                  清除筛选
                </button>
              )}
            </div>
        </details>
      )}
      {cardGroups.size ? (
        orderedGroups.map(([group, cards]) => (
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
                  {isAgentList && <small>{item.techId ? `${item.techId} · ${item.group}` : item.group}</small>}
                  {item.tags.slice(0, isAgentList ? 2 : 3).map((tag) => (
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
      ) : kind !== "connector" || query ? (
        <div className="hub-empty">没有匹配的条目，换个关键词或分类试试。</div>
      ) : null}

      {capability && (
        <Modal
          title={`能力详情 · ${capability.name || capability.id}`}
          onClose={() => setCapabilityId(null)}
        >
          <div className="modal-body">
            {capability.description && <p>{capability.description}</p>}
            <h3>声明此能力的智能体 · {matchingAgents.length}</h3>
            {matchingAgents.length ? matchingAgents.map((agent) => (
              <article className="record row-between" key={agent.id}>
                <div>
                  <strong>{agent.name}</strong>
                  <p className="muted">{agentScopeLabel(agent)}</p>
                </div>
                <div className="actions">
                  <Badge status={agent.status} />
                  <button onClick={() => onAgent(agent.id)}>查看智能体</button>
                </div>
              </article>
            )) : (
              <Empty title="当前工作空间暂无声明此能力的智能体" />
            )}
          </div>
        </Modal>
      )}

      {selected && (
        <>
          {kind === "expert" && category !== "workflow" && (
            <div className="hub-native-dialog">
              <AgentDetailsDialog
                key={selected.id}
                projectId={projectId}
                agents={agents}
                capabilities={capabilities}
                canEdit={canEdit}
                mutate={mutate}
                notify={notify}
                onTask={onTask}
                workflows={workflows}
                assets={assets}
                initialId={selected.id}
                onClose={() => setSelected(null)}
                onWorkflow={onWorkflow}
                offline={offline}
              />
            </div>
          )}
          {kind === "expert" && category === "workflow" && (
            <div className="hub-native-dialog">
              <WorkflowDetailsDialog
                key={selected.id}
                projectId={projectId}
                workflows={workflows}
                agents={agents}
                canEdit={canEdit}
                mutate={mutate}
                notify={notify}
                initialId={selected.id}
                onClose={() => setSelected(null)}
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
                key={selected.id}
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
            <ConnectorDetail
              item={items.find((item) => item.id === selected.id) || selected}
              tools={catalog.tools.filter((tool) => mcpConnectorId(tool) === selected.id)}
              onManage={isAdmin && selected.extra?.kind === "mcp" && !selected.extra.builtin
                ? () => setManagingConnector(true)
                : undefined}
              onTool={setConnectorTool}
              onClose={() => setSelected(null)}
            />
          )}
        </>
      )}

      {selected && connectorTool && (
        <Modal wide title={connectorTool.remote_name || connectorTool.label || connectorTool.name}
          onClose={() => setConnectorTool(null)}>
          <div className="modal-body">
            <ToolCatalog
              projectId={projectId}
              connectorId={selected.id}
              initialId={connectorTool.id}
              agents={agents}
              canEdit={canEdit}
              onAgent={onAgent}
            />
          </div>
          <div className="modal-foot">
            <button onClick={() => setConnectorTool(null)}>返回连接器详情</button>
          </div>
        </Modal>
      )}

      {selected && managingConnector && (
        <Modal wide title={`管理连接器 · ${selected.name}`} onClose={closeConnectorManager}>
          <div className="modal-body">
            <CapabilityPages
              projectId={projectId}
              connectorId={selected.id}
              canEdit={canEdit}
              isAdmin={isAdmin}
              mutate={mutate}
              notify={notify}
            />
          </div>
          <div className="modal-foot">
            <button onClick={closeConnectorManager}>返回连接器详情</button>
          </div>
        </Modal>
      )}

      {interviewOpen && isAgentList && canEdit && (
        <ExpertInterview
          projectId={projectId}
          onClose={() => setInterviewOpen(false)}
          onSaved={(agent) => {
            setInterviewOpen(false);
            onCreated?.();
            notify?.(`已创建 ${agent.name}`);
          }}
        />
      )}

      {dataServiceOpen && (
        <FormModal title="接入上游数据服务" onClose={() => setDataServiceOpen(false)} onSubmit={async () => {
          await post("/v2/connectors", { ...dataService, project_id: projectId, kind: "http_knowledge" });
          setCatalogTick((tick) => tick + 1);
          onCreated?.();
          notify?.("数据服务已登记");
          setDataService({ name: "", base_url: "", description: "", automatic_intake: false, allow_public_results: false });
        }}>
          <Field label="服务名称"><input required value={dataService.name} onChange={(e) => setDataService({ ...dataService, name: e.target.value })}/></Field>
          <Field label="服务地址"><input required type="url" value={dataService.base_url} onChange={(e) => setDataService({ ...dataService, base_url: e.target.value })}/></Field>
          <Field label="说明"><input value={dataService.description} onChange={(e) => setDataService({ ...dataService, description: e.target.value })}/></Field>
          <label><input type="checkbox" checked={dataService.automatic_intake} onChange={(e) => setDataService({ ...dataService, automatic_intake: e.target.checked })}/>允许任务自动检索</label>
          <label><input type="checkbox" checked={dataService.allow_public_results} onChange={(e) => setDataService({ ...dataService, allow_public_results: e.target.checked })}/>接受服务声明的公开分类</label>
        </FormModal>
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

function ConnectorDetail({
  item,
  tools,
  onClose,
  onManage,
  onTool,
}: {
  item: HubItem;
  tools: Item[];
  onClose: () => void;
  onManage?: () => void;
  onTool: (tool: Item) => void;
}) {
  const raw = item.extra || {};
  const fields = [
    ["连接器标识", item.id],
    ["状态", statusNames[raw.status] || raw.status],
    ["传输方式", raw.transport],
    ["版本", raw.current_version ? `v${raw.current_version}` : null],
    ["来源", raw.builtin ? "平台内置" : "项目接入"],
    ["自动接入", raw.kind === "http_knowledge" ? (raw.automatic_intake ? "已启用" : "未启用") : null],
  ].filter(([, value]) => value);
  return (
    <Modal wide title={item.name} onClose={onClose}>
      <div className="modal-body hub-detail-body">
        {item.desc && <p className="hub-detail-lead">{item.desc}</p>}
        <div className="hub-detail-grid">
          {fields.map(([label, value]) => (
            <div key={label}>
              <span>{label}</span>
              <strong>{value}</strong>
            </div>
          ))}
        </div>
        {tools.length > 0 && (
          <section className="hub-connector-tools">
            <h3>连接器提供的工具</h3>
            {tools.map((tool) => (
              <article className="record" key={tool.id}>
                <div className="row-between">
                  <strong>{tool.remote_name || tool.label || tool.name}</strong>
                  <button className="link-button" onClick={() => onTool(tool)}>查看工具详情</button>
                </div>
                {tool.description && <p>{tool.description}</p>}
              </article>
            ))}
          </section>
        )}
      </div>
      <div className="modal-foot">
        <button onClick={onClose}>关闭</button>
        {onManage && <button className="primary" onClick={onManage}>管理连接器</button>}
      </div>
    </Modal>
  );
}
