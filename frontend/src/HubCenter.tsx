import { useEffect, useMemo, useState, type FormEvent } from "react";
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
import type { Item } from "./types";
import type { HubKind } from "./WbShell";

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
  const [selected, setSelected] = useState<HubItem | null>(null);
  const [catalogTick, setCatalogTick] = useState(0);
  const [creating, setCreating] = useState(false);
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
    setSort("default");
    setSelected(null);
    setCreating(false);
    setCreateError("");
  }, [kind]);

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

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    let rows = items.filter(
      (item) =>
        (category === "all" ||
          category === "workflow" ||
          item.bucket === "secondary") &&
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
  }, [items, query, category, sort]);

  const meta = TAB_META[kind];
  const isWorkflow = kind === "expert" && category === "workflow";
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
  const openById = (id: string, fallbackName?: string) => {
    const found = items.find((item) => item.id === id);
    setSelected(
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
    if (!projectId) return;
    setCreateBusy(true);
    setCreateError("");
    try {
      if (isWorkflow) {
        await post("/v2/workflows", {
          project_id: projectId,
          name: createForm.name,
          description: createForm.description || "",
          task_type: createForm.task_type,
          nodes: [
            {
              id: "process",
              label: createForm.name,
              agent_id: createForm.agent_id,
              depends_on: [],
              config: {},
            },
          ],
        });
        notify?.("流程已创建");
      } else if (kind === "expert") {
        const cap = capabilities.find(
          (c) => c.id === createForm.capability,
        );
        await post("/v2/agents", {
          project_id: projectId,
          name: createForm.name,
          description: createForm.description || "",
          purpose: "general",
          structure: "atomic",
          capabilities: [createForm.capability],
          modalities: Array.isArray(cap?.modalities)
            ? cap.modalities
            : ["structured"],
          tool_ids: [],
          instructions: "",
          model_policy: "local",
          execution_strategy: "fixed",
          tool_refs: [],
          skill_refs: [],
          members: [],
          parameters: {},
        });
        notify?.("智能体已创建");
      } else if (kind === "connector") {
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
            const Icon = cat.icon;
            return (
              <div key={cat.title} className={`hub-hero tone-${cat.tone}`}>
                <div className="hub-hero-top">
                  <div>
                    <strong>{cat.title}</strong>
                    <span>{cat.count} 项</span>
                  </div>
                  <Icon size={36} />
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
              onClick={() => setCategory("all")}
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
                setCategory(kind === "expert" ? "workflow" : "secondary")
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
          <button className="hub-add" onClick={openCreate}>
            <Plus size={14} />
            {addLabel}
          </button>
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
                    ? "最热"
                    : "最新"}
              </button>
            ))}
          </div>
        </div>
      </div>

      <div className="hub-list-rule" />

      {visible.length ? (
        <div className="hub-card-grid">
          {visible.map((item, index) => (
            <button
              key={item.id + "-" + index}
              className="hub-card"
              onClick={() => setSelected(item)}
            >
              <span className={`hub-card-avatar tone-${index % 4}`}>
                {item.name.slice(0, 1)}
              </span>
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
      ) : (
        <div className="hub-empty">没有匹配的条目，换个关键词或分类试试。</div>
      )}

      {selected && (
        <div
          className="hub-modal-backdrop"
          onClick={() => setSelected(null)}
          role="presentation"
        >
          <div
            className="hub-modal"
            role="dialog"
            aria-modal="true"
            aria-label={selected.name}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="hub-modal-head">
              <span className={`hub-card-avatar tone-${selected.bucket === "secondary" ? 1 : 0}`}>
                {selected.name.slice(0, 1)}
              </span>
              <div>
                <h2>{selected.name}</h2>
                {selected.techId && selected.techId !== selected.name && (
                  <small>技术标识：{selected.techId}</small>
                )}
              </div>
              <button
                className="hub-modal-close"
                aria-label="关闭详情"
                onClick={() => setSelected(null)}
              >
                <X size={18} />
              </button>
            </div>
            <div className="hub-modal-body">
              <p>{selected.desc || "暂无说明"}</p>
              <div className="hub-modal-rows">
                <div>
                  <span>分类</span>
                  <strong>{selected.group || "—"}</strong>
                </div>
                <div>
                  <span>状态</span>
                  <strong>{selected.meta || "—"}</strong>
                </div>
                <div>
                  <span>标签</span>
                  <strong>
                    {selected.tags.length ? selected.tags.join(" · ") : "—"}
                  </strong>
                </div>
              </div>
            </div>
            <div className="hub-modal-foot">
              <button onClick={() => setSelected(null)}>关闭</button>
              {onOpenFullPage && (
                <button
                  className="is-primary"
                  onClick={() => onOpenFullPage(managePage)}
                >
                  在管理页查看 <ArrowRight size={14} />
                </button>
              )}
            </div>
          </div>
        </div>
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
          </div>
        </div>
      )}
    </div>
  );
}
