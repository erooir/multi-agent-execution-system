import { useProject } from "./ProjectContext";
import KnowledgeGraph from "./KnowledgeGraph";
import { useEffect, useState } from "react";
import {
  ArrowRight,
  BookOpen,
  Boxes,
  BrainCircuit,
  Check,
  ChevronRight,
  Copy,
  Database,
  Download,
  Edit3,
  FileText,
  FolderOpen,
  GitBranch,
  Globe,
  LockKeyhole,
  Network,
  Play,
  Plus,
  RefreshCw,
  Search,
  Server,
  ShieldCheck,
  Sparkles,
  Trash2,
  Upload,
  Workflow,
  Wrench,
} from "lucide-react";
import {
  api,
  post,
  put,
  remove,
  testSkill,
  testTool,
  mcpHealth,
  mcpRefresh,
  time,
  type RecordData,
} from "./api";
import {
  type CapabilityErrorInfo,
  type CapabilityTrace,
  type PageProps,
  type SkillSummary,
  type ToolSummary,
  categoryNames,
  roleNames,
} from "./types";
import {
  capabilityErrorText,
  capabilityStatusText,
  collectToolInput,
  egressNames,
  flattenTrace,
  mcpHealthText,
  networkNames,
  nodeKindNames,
  providerNames,
  skillToolSummary,
  toolHealth,
  toolInputFields,
} from "./capabilities";
import {
  ActionButton,
  Badge,
  Empty,
  Field,
  InlineMessage,
  JsonView,
  Modal,
  ModeBadge,
  Panel,
  SectionTitle,
} from "./ui";
function useTask(p: PageProps) {
  return async (fn: () => Promise<any>, message?: string) => {
    try {
      const r = await fn();
      await p.refresh();
      if (message) p.notify(message);
      return r;
    } catch (e) {
      p.notify((e as Error).message, true);
      return undefined;
    }
  };
}
export function ProjectsPage(p: PageProps) {
  const [edit, setEdit] = useState<RecordData | null>(null),
    [search, setSearch] = useState("");
  const task = useTask(p),
    items = (p.data.projects || []).filter((x: any) =>
      `${x.name} ${x.description}`.includes(search),
    );
  return (
    <>
      <SectionTitle
        eyebrow="PROJECTS"
        title="研究项目"
        detail="用项目组织资料、研究流程和报告，保持研究上下文连贯。"
        actions={
          <button
            className="button primary"
            disabled={!p.canEdit}
            onClick={() =>
              setEdit({ name: "", description: "", category: "technology" })
            }
          >
            <Plus size={17} />
            新建项目
          </button>
        }
      />
      <div className="toolbar">
        <div className="search-field">
          <Search size={17} />
          <input
            placeholder="搜索项目名称或描述"
            aria-label="搜索项目"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <span className="muted">{items.length} 个项目</span>
      </div>
      {items.length ? (
        <div className="project-grid">
          {items.map((x: any) => (
            <Panel key={x.id} className="project-card">
              <div className="card-top">
                <div className="project-icon">
                  <FolderOpen size={25} />
                </div>
                <Badge>{categoryNames[x.category] || x.category}</Badge>
              </div>
              <h2>{x.name}</h2>
              <p className="card-description">
                {x.description || "尚未填写项目说明"}
              </p>
              <div className="project-counts">
                <span>
                  <FileText size={15} />
                  {
                    (p.data.documents || []).filter(
                      (d: any) => d.project_id === x.id,
                    ).length
                  }{" "}
                  份资料
                </span>
                <span>
                  <Workflow size={15} />
                  {
                    (p.data.runs || []).filter(
                      (d: any) => d.project_id === x.id,
                    ).length
                  }{" "}
                  次运行
                </span>
              </div>
              <div className="card-footer">
                <small>{time(x.created_at)} 创建</small>
                <div>
                  <button
                    className="icon-button"
                    title="编辑项目"
                    disabled={!p.canEdit}
                    onClick={() => setEdit({ ...x })}
                  >
                    <Edit3 size={16} />
                  </button>
                  <ActionButton
                    className="icon-button danger"
                    disabled={!p.canEdit}
                    onClick={() => {
                      if (window.confirm(`删除项目“${x.name}”？`))
                        return task(
                          () => remove(`/projects/${x.id}`),
                          "项目已删除",
                        );
                    }}
                  >
                    <Trash2 size={16} />
                  </ActionButton>
                  <button
                    className="button small"
                    onClick={() => p.go("knowledge", x.id)}
                  >
                    查看资料
                    <ArrowRight size={14} />
                  </button>
                </div>
              </div>
            </Panel>
          ))}
        </div>
      ) : (
        <Empty title="没有找到项目" />
      )}
      {edit && (
        <Modal
          title={edit.id ? "编辑研究项目" : "新建研究项目"}
          onClose={() => setEdit(null)}
        >
          <Field label="项目名称">
            <input
              autoFocus
              value={edit.name}
              onChange={(e) => setEdit({ ...edit, name: e.target.value })}
              placeholder="例如：民用航空复合材料技术研究"
            />
          </Field>
          <Field label="研究领域">
            <select
              value={edit.category}
              onChange={(e) => setEdit({ ...edit, category: e.target.value })}
            >
              {Object.entries(categoryNames).map(([k, v]) => (
                <option value={k} key={k}>
                  {v}
                </option>
              ))}
            </select>
          </Field>
          <Field label="项目说明">
            <textarea
              rows={4}
              value={edit.description}
              onChange={(e) =>
                setEdit({ ...edit, description: e.target.value })
              }
              placeholder="描述研究目标与范围"
            />
          </Field>
          <div className="modal-actions">
            <button className="button" onClick={() => setEdit(null)}>
              取消
            </button>
            <ActionButton
              className="button primary"
              disabled={!edit.name.trim()}
              onClick={() =>
                task(async () => {
                  await (edit.id
                    ? put(`/projects/${edit.id}`, edit)
                    : post("/projects", edit));
                  setEdit(null);
                }, "项目已保存")
              }
            >
              保存项目
            </ActionButton>
          </div>
        </Modal>
      )}
    </>
  );
}
export function KnowledgePage(p: PageProps) {
  const [project, setProject] = useProject();
  const [tab, setTab] = useState("documents"),
    [query, setQuery] = useState(""),
    [semantic, setSemantic] = useState(false),
    [results, setResults] = useState<any[] | null>(null),
    [graph, setGraph] = useState<any>(null),
    [chunks, setChunks] = useState<{ name: string; items: any[] } | null>(null),
    [upload, setUpload] = useState(false),
    [file, setFile] = useState<File | null>(null),
    [visibility, setVisibility] = useState("local"),
    [selectedDocs, setSelectedDocs] = useState<string[]>([]);
  const task = useTask(p),
    docs = (p.data.documents || []).filter(
      (d: any) => !project || d.project_id === project,
    );
  useEffect(() => {
    setResults(null);
    setGraph(null);
    setSelectedDocs([]);
  }, [project]);
  return (
    <>
      <SectionTitle
        eyebrow="KNOWLEDGE & EVIDENCE"
        title="资料与知识"
        detail="管理可追溯的知识来源，在分块、检索结果和关系图之间探索。"
        actions={
          <button
            className="button primary"
            disabled={!p.canEdit}
            onClick={() => setUpload(true)}
          >
            <Upload size={16} />
            导入资料
          </button>
        }
      />
      <div className="toolbar">
        <div className="tabs">
          {[
            ["documents", "资料库"],
            ["search", "证据检索"],
            ["graph", "知识关系"],
          ].map(([id, n]) => (
            <button
              key={id}
              className={tab === id ? "active" : ""}
              onClick={() => setTab(id)}
            >
              {n}
            </button>
          ))}
        </div>
        <select
          className="project-select"
          aria-label="筛选项目"
          value={project}
          onChange={(e) => setProject(e.target.value)}
        >
          <option value="">全部项目</option>
          {(p.data.projects || []).map((x: any) => (
            <option value={x.id} key={x.id}>
              {x.name}
            </option>
          ))}
        </select>
      </div>
      {tab === "documents" ? (
        <Panel
          title="项目资料"
          detail="本地限定资料参与本地检索，不发送给外部模型。"
        >
          {docs.length ? (
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>资料名称</th>
                    <th>可见范围</th>
                    <th>索引状态</th>
                    <th>分块 / 大小</th>
                    <th>导入时间</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {docs.map((d: any) => (
                    <tr key={d.id}>
                      <td>
                        <div className="document-name">
                          <FileText size={18} />
                          <span>
                            <strong>{d.name}</strong>
                            <small>{d.kind || "文档"}</small>
                          </span>
                        </div>
                      </td>
                      <td>
                        <span className={`visibility ${d.visibility}`}>
                          {d.visibility === "external" ? (
                            <Globe size={14} />
                          ) : (
                            <LockKeyhole size={14} />
                          )}{" "}
                          {d.visibility === "external"
                            ? "允许外部模型"
                            : "仅限本地"}
                        </span>
                      </td>
                      <td>
                        <Badge status={d.status} />
                      </td>
                      <td>
                        {d.chunk_count || 0} 段 /{" "}
                        {((d.size || 0) / 1024).toFixed(1)} KB
                      </td>
                      <td className="muted">{time(d.created_at)}</td>
                      <td>
                        <div className="row-actions">
                          <ActionButton
                            className="text-button"
                            onClick={() =>
                              task(async () => {
                                const result = await api(
                                  `/documents/${d.id}/chunks`,
                                );
                                setChunks({
                                  name: d.name,
                                  items: Array.isArray(result)
                                    ? result
                                    : result.chunks || [],
                                });
                              })
                            }
                          >
                            查看分块
                          </ActionButton>
                          <a
                            className="icon-button"
                            href={`/api/documents/${d.id}/file`}
                            title="下载资料"
                          >
                            <Download size={15} />
                          </a>
                          <ActionButton
                            className="icon-button danger"
                            disabled={!p.canEdit}
                            onClick={() => {
                              if (
                                window.confirm(`删除资料“${d.name}”及其索引？`)
                              )
                                return task(
                                  () => remove(`/documents/${d.id}`),
                                  "资料已删除",
                                );
                            }}
                          >
                            <Trash2 size={15} />
                          </ActionButton>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <Empty
              title="该项目暂无资料"
              detail="导入 TXT、Markdown、CSV、PDF 或 DOCX，建立研究知识库。"
            />
          )}
        </Panel>
      ) : tab === "search" ? (
        <>
          <Panel
            title="检索可引用的证据"
            detail="每条结果都携带来源文档和位置，可在报告中追溯。"
          >
            <div className="search-query">
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="输入研究问题或关键词"
                aria-label="检索查询"
                onKeyDown={(e) => {
                  if (e.key === "Enter" && query.trim())
                    task(async () =>
                      setResults(
                        await post("/knowledge/search", {
                          query,
                          project_id: project || undefined,
                          document_ids: selectedDocs.length
                            ? selectedDocs
                            : undefined,
                          limit: 8,
                          semantic,
                        }),
                      ),
                    );
                }}
              />
              <ActionButton
                className="button primary"
                disabled={!query.trim()}
                onClick={() =>
                  task(async () =>
                    setResults(
                      await post("/knowledge/search", {
                        query,
                        project_id: project || undefined,
                        document_ids: selectedDocs.length
                          ? selectedDocs
                          : undefined,
                        limit: 8,
                        semantic,
                      }),
                    ),
                  )
                }
              >
                <Search size={16} />
                检索证据
              </ActionButton>
            </div>
            <label className="checkbox-label">
              <input
                type="checkbox"
                checked={semantic}
                onChange={(e) => setSemantic(e.target.checked)}
              />
              启用语义检索（以系统实际可用能力为准）
            </label>
            <details className="doc-selection">
              <summary>
                限制检索资料（已选 {selectedDocs.length}
                ，未选择时使用项目全部资料）
              </summary>
              <div className="checkbox-grid">
                {docs.map((d: any) => (
                  <label className="checkbox-label" key={d.id}>
                    <input
                      type="checkbox"
                      checked={selectedDocs.includes(d.id)}
                      onChange={(e) =>
                        setSelectedDocs(
                          e.target.checked
                            ? [...selectedDocs, d.id]
                            : selectedDocs.filter((i) => i !== d.id),
                        )
                      }
                    />
                    {d.name}
                  </label>
                ))}
              </div>
            </details>
          </Panel>
          {results === null ? (
            <Empty
              title="输入一个问题，发现资料中的证据"
              detail="检索不会凭空生成来源；无匹配时将明确返回空结果。"
            />
          ) : results.length ? (
            <div className="evidence-results">
              {results.map((r: any, i) => (
                <EvidenceCard evidence={r} index={i} key={r.id || i} />
              ))}
            </div>
          ) : (
            <Empty
              title="没有找到匹配证据"
              detail="尝试更具体的关键词，或导入相关资料后重试。"
            />
          )}
        </>
      ) : (
        <Panel
          title="项目知识关系"
          detail="展示已索引资料中实际保存的实体与关系。"
          actions={
            <ActionButton
              onClick={() =>
                task(async () =>
                  setGraph(
                    await api(
                      `/knowledge/graph${project ? `?project_id=${encodeURIComponent(project)}` : ""}`,
                    ),
                  ),
                )
              }
            >
              <Network size={16} />
              加载关系
            </ActionButton>
          }
        >
          {!graph ? (
            <Empty
              title="探索知识之间的联系"
              detail="加载当前项目的知识关系。"
            />
          ) : (
            <>
              <div className="graph-stats">
                <span>{graph.nodes?.length || 0} 个实体</span>
                <span>{graph.edges?.length || 0} 条关系</span>
                {graph.nodes?.some((n: any) => n.synthetic) && (
                  <Badge>公开安全的合成示例</Badge>
                )}
              </div>
              {graph.nodes?.length > 0 && (
                <KnowledgeGraph
                  graph={graph}
                  onSource={(edge) => {
                    if (!edge.document_id) {
                      p.notify("该关系尚未绑定文档来源", true);
                      return;
                    }
                    task(async () => {
                      const result = await api(
                        `/documents/${edge.document_id}/chunks`,
                      );
                      const items = Array.isArray(result)
                        ? result
                        : result.chunks || [];
                      setChunks({
                        name:
                          p.data.documents?.find(
                            (d: any) => d.id === edge.document_id,
                          )?.name || "关系来源",
                        items: edge.chunk_id
                          ? items.filter((c: any) => c.id === edge.chunk_id)
                          : items,
                      });
                    });
                  }}
                />
              )}
              <div className="knowledge-nodes">
                {(graph.nodes || []).map((n: any) => (
                  <div className="knowledge-node" key={n.id}>
                    <Network size={17} />
                    <strong>{n.label || n.name || n.id}</strong>
                    <Badge>{n.type || n.kind || "实体"}</Badge>
                  </div>
                ))}
              </div>
              <div className="relationship-list">
                {(graph.edges || []).map((e: any, i: number) => (
                  <div key={e.id || i}>
                    <span>
                      {graph.nodes?.find((n: any) => n.id === e.source)
                        ?.label || e.source}
                    </span>
                    <i>── {e.label || e.relation || e.type || "关联"} →</i>
                    <span>
                      {graph.nodes?.find((n: any) => n.id === e.target)
                        ?.label || e.target}
                    </span>
                  </div>
                ))}
              </div>
              {!graph.nodes?.length && <Empty title="暂无知识关系" />}
            </>
          )}
        </Panel>
      )}
      {upload && (
        <Modal title="导入研究资料" onClose={() => setUpload(false)}>
          <Field label="所属项目">
            <select
              value={project}
              onChange={(e) => setProject(e.target.value)}
            >
              <option value="">选择项目</option>
              {p.data.projects.map((x: any) => (
                <option key={x.id} value={x.id}>
                  {x.name}
                </option>
              ))}
            </select>
          </Field>
          <label className="file-drop">
            <Upload size={28} />
            <strong>{file?.name || "选择需要导入的文件"}</strong>
            <span>TXT、Markdown、CSV、PDF、DOCX、常见图片</span>
            <input
              type="file"
              accept=".txt,.md,.csv,.pdf,.docx,.png,.jpg,.jpeg,.webp"
              onChange={(e) => setFile(e.target.files?.[0] || null)}
            />
          </label>
          <Field
            label="资料使用范围"
            hint="只有选择“允许外部模型”的资料，才能作为上下文发送给 DeepSeek。"
          >
            <select
              value={visibility}
              onChange={(e) => setVisibility(e.target.value)}
            >
              <option value="local">仅限本地检索</option>
              <option value="external">允许外部模型使用</option>
            </select>
          </Field>
          <div className="modal-actions">
            <button className="button" onClick={() => setUpload(false)}>
              取消
            </button>
            <ActionButton
              className="button primary"
              disabled={!file || !project}
              onClick={() =>
                task(async () => {
                  const body = new FormData();
                  body.append("file", file!);
                  body.append("project_id", project);
                  body.append("visibility", visibility);
                  await api("/documents/upload", { method: "POST", body });
                  setUpload(false);
                  setFile(null);
                }, "资料已导入")
              }
            >
              导入并索引
            </ActionButton>
          </div>
        </Modal>
      )}
      {chunks && (
        <Modal
          title={`${chunks.name} · 文档分块`}
          onClose={() => setChunks(null)}
          wide
        >
          <div className="chunk-list">
            {chunks.items.length ? (
              chunks.items.map((x: any, i) => (
                <article className="chunk" key={x.id || i}>
                  <div>
                    <Badge>分块 {i + 1}</Badge>
                    <small>{x.location || x.id}</small>
                  </div>
                  <p>{x.text || x.content}</p>
                </article>
              ))
            ) : (
              <Empty title="暂无可用分块" />
            )}
          </div>
        </Modal>
      )}
    </>
  );
}
export function EvidenceCard({
  evidence: r,
  index,
}: {
  evidence: any;
  index: number;
}) {
  return (
    <article className="evidence-card">
      <div className="evidence-header">
        <span className="evidence-number">
          {String(index + 1).padStart(2, "0")}
        </span>
        <div>
          <strong>{r.document_name || r.source || "来源文档"}</strong>
          <small>{r.location || r.id}</small>
        </div>
        {r.score !== undefined && (
          <Badge>相关度 {Number(r.score).toFixed(3)}</Badge>
        )}
      </div>
      <p>{r.text || r.content}</p>
      <small className="source-id">来源 ID · {r.id || r.document_id}</small>
    </article>
  );
}
export function AgentsPage(p: PageProps) {
  const [edit, setEdit] = useState<RecordData | null>(null),
    [test, setTest] = useState<RecordData | null>(null),
    [message, setMessage] = useState(
      "请介绍你的研究职责，以及处理证据不足时的原则。",
    ),
    [mode, setMode] = useState(p.data.system?.default_mode || "rehearsal"),
    [result, setResult] = useState<any>(null);
  const task = useTask(p);
  return (
    <>
      <SectionTitle
        eyebrow="AGENT REGISTRY"
        title="智能体"
        detail="定义角色职责与技能边界，让不同智能体协同完成研究。"
        actions={
          <button
            className="button primary"
            disabled={!p.canEdit}
            onClick={() =>
              setEdit({
                name: "",
                role: "planner",
                description: "",
                instructions: "",
                skill_ids: [],
                model: "deepseek-flash",
                enabled: true,
              })
            }
          >
            <Plus size={17} />
            创建智能体
          </button>
        }
      />
      <div className="agent-grid">
        {(p.data.agents || []).map((a: any) => (
          <Panel key={a.id} className="agent-card">
            <div className="card-top">
              <div className={`agent-icon ${a.role}`}>
                <BrainCircuit size={26} />
              </div>
              <Badge status={a.enabled === false ? "pending" : "ready"}>
                {a.enabled === false ? "已停用" : "已启用"}
              </Badge>
            </div>
            <div className="eyebrow">{roleNames[a.role] || a.role}</div>
            <h2>{a.name}</h2>
            <p className="card-description">
              {a.description || "尚未填写角色说明"}
            </p>
            <div className="agent-skills">
              {(a.skill_ids || []).map((id: string) => (
                <Badge key={id}>
                  {p.data.skills?.find((s: any) => s.id === id)?.name || id}
                </Badge>
              ))}
            </div>
            <div className="agent-model">
              <span className="status-dot" />
              {a.model || "deepseek-flash"}
              <small>v{a.version || 1}</small>
            </div>
            <div className="card-footer">
              <button
                className="button small"
                onClick={() => {
                  setTest(a);
                  setResult(null);
                }}
              >
                <Play size={14} />
                测试角色
              </button>
              <div>
                <button
                  className="icon-button"
                  title="编辑智能体"
                  disabled={!p.canEdit}
                  onClick={() => setEdit({ ...a })}
                >
                  <Edit3 size={16} />
                </button>
                <ActionButton
                  className="icon-button danger"
                  disabled={!p.canEdit}
                  onClick={() => {
                    if (window.confirm(`删除智能体“${a.name}”？`))
                      return task(
                        () => remove(`/agents/${a.id}`),
                        "智能体已删除",
                      );
                  }}
                >
                  <Trash2 size={16} />
                </ActionButton>
              </div>
            </div>
          </Panel>
        ))}
      </div>
      {edit && (
        <Modal
          title={edit.id ? "配置智能体" : "创建智能体"}
          onClose={() => setEdit(null)}
          wide
        >
          <div className="form-grid">
            <Field label="名称">
              <input
                value={edit.name}
                onChange={(e) => setEdit({ ...edit, name: e.target.value })}
              />
            </Field>
            <Field label="协作角色">
              <select
                value={edit.role}
                onChange={(e) => setEdit({ ...edit, role: e.target.value })}
              >
                {[
                  "planner",
                  "parser",
                  "retriever",
                  "writer",
                  "coordinator",
                ].map((k) => (
                  <option key={k} value={k}>
                    {roleNames[k]}
                  </option>
                ))}
              </select>
            </Field>
          </div>
          <Field label="职责说明">
            <input
              value={edit.description || ""}
              onChange={(e) =>
                setEdit({ ...edit, description: e.target.value })
              }
            />
          </Field>
          <Field label="角色指令">
            <textarea
              rows={6}
              value={edit.instructions || ""}
              onChange={(e) =>
                setEdit({ ...edit, instructions: e.target.value })
              }
              placeholder="定义任务目标、证据要求、输出格式和需要人工确认的条件"
            />
          </Field>
          <Field label="启用技能">
            <div className="checkbox-grid">
              {(p.data.skills || []).map((s: any) => {
                const summary = skillToolSummary(s, p.data.tools || []);
                return (
                  <label className="checkbox-label" key={s.id}>
                    <input
                      type="checkbox"
                      checked={(edit.skill_ids || []).includes(s.id)}
                      onChange={(e) =>
                        setEdit({
                          ...edit,
                          skill_ids: e.target.checked
                            ? [...(edit.skill_ids || []), s.id]
                            : (edit.skill_ids || []).filter(
                                (id: string) => id !== s.id,
                              ),
                        })
                      }
                    />
                    <span>
                      {s.name}
                      <small className="muted">
                        授权 {summary.count} 个工具 · 外发：
                        {summary.egressText}
                      </small>
                    </span>
                  </label>
                );
              })}
            </div>
          </Field>
          <label className="checkbox-label">
            <input
              type="checkbox"
              checked={edit.enabled !== false}
              onChange={(e) => setEdit({ ...edit, enabled: e.target.checked })}
            />
            启用该智能体
          </label>
          <div className="modal-actions">
            <button className="button" onClick={() => setEdit(null)}>
              取消
            </button>
            <ActionButton
              className="button primary"
              disabled={!edit.name.trim()}
              onClick={() =>
                task(async () => {
                  await (edit.id
                    ? put(`/agents/${edit.id}`, edit)
                    : post("/agents", edit));
                  setEdit(null);
                }, "智能体配置已保存")
              }
            >
              保存配置
            </ActionButton>
          </div>
        </Modal>
      )}
      {test && (
        <Modal
          title={`${test.name} · 角色测试`}
          onClose={() => setTest(null)}
          wide
        >
          <Field label="测试输入">
            <textarea
              rows={3}
              value={message}
              onChange={(e) => setMessage(e.target.value)}
            />
          </Field>
          <div className="toolbar">
            <select
              value={mode}
              onChange={(e) => setMode(e.target.value)}
              aria-label="测试模式"
            >
              <option value="rehearsal">本地演练</option>
              <option value="live">真实模型 · 计费</option>
            </select>
            <ActionButton
              className="button primary"
              disabled={!p.canEdit || !message.trim()}
              onClick={() =>
                task(async () =>
                  setResult(
                    await post(`/agents/${test.id}/test`, { message, mode }),
                  ),
                )
              }
            >
              <Play size={15} />
              开始测试
            </ActionButton>
          </div>
          {result && (
            <div className="test-result">
              <ModeBadge mode={result.mode || mode} />
              <div className="result-text">
                {result.text || JSON.stringify(result, null, 2)}
              </div>
              {result.note && <InlineMessage>{result.note}</InlineMessage>}
              {Array.isArray(result.tool_calls) &&
                result.tool_calls.length > 0 && (
                  <p className="muted">
                    本次模型实际调用能力 {result.tool_calls.length} 次
                    {result.skills?.length
                      ? `，涉及技能：${result.skills.join("、")}`
                      : ""}
                  </p>
                )}
              {result.usage && <JsonView value={result.usage} />}
            </div>
          )}
        </Modal>
      )}
    </>
  );
}
export function TraceView({
  trace,
  error,
  indent = 0,
}: {
  trace?: CapabilityTrace | null;
  error?: CapabilityErrorInfo | null;
  indent?: number;
}) {
  const rows = flattenTrace(trace);
  if (!rows.length && !error) return null;
  return (
    <div className="trace-view">
      {rows.map((row, i) => (
        <div
          className="trace-row"
          key={`${row.tool_id}-${i}`}
          style={{ paddingLeft: (row.depth + indent) * 24 }}
        >
          <span className="trace-layer">Tool</span>
          <code>{row.tool_id}</code>
          {row.provider && (
            <Badge>{providerNames[row.provider] || row.provider}</Badge>
          )}
          {row.status && <Badge status={row.status} />}
          {typeof row.duration_ms === "number" && (
            <span className="muted">{row.duration_ms} ms</span>
          )}
        </div>
      ))}
      {error && (
        <div className="trace-row error" style={{ paddingLeft: indent * 24 }}>
          <Badge status="failed">{capabilityErrorText(error.code)}</Badge>
          <span className="muted">{error.message}</span>
        </div>
      )}
    </div>
  );
}
export function SkillTraceHeader({
  skillId,
  status,
  durationMs,
}: {
  skillId: string;
  status?: string;
  durationMs?: number;
}) {
  return (
    <div className="trace-row">
      <span className="trace-layer">Skill</span>
      <code>{skillId}</code>
      {status && <Badge status={status}>{capabilityStatusText(status)}</Badge>}
      {typeof durationMs === "number" && (
        <span className="muted">{durationMs} ms</span>
      )}
    </div>
  );
}
function SkillTestResult({ result }: { result: any }) {
  return (
    <div className="test-result">
      <div className="report-mode">
        <Badge status={result.status}>
          {capabilityStatusText(result.status)}
        </Badge>
        {result.trace?.duration_ms !== undefined && (
          <span className="muted">耗时 {result.trace.duration_ms} ms</span>
        )}
      </div>
      {result.status === "dry_run" && (
        <InlineMessage>
          {result.data?.note || "演练模式仅完成预检，未发起实际调用。"}
        </InlineMessage>
      )}
      {result.error && (
        <InlineMessage error>
          {capabilityErrorText(result.error.code)}：{result.error.message}
        </InlineMessage>
      )}
      <div className="node-trace">
        <SkillTraceHeader
          skillId={result.skill_id}
          status={result.status}
          durationMs={result.trace?.duration_ms}
        />
        <TraceView trace={result.trace} indent={1} />
      </div>
      {result.text && <div className="result-text">{result.text}</div>}
      {result.evidence?.length > 0 && (
        <p className="muted">返回 {result.evidence.length} 条证据</p>
      )}
      <details className="metric-details">
        <summary>完整响应</summary>
        <JsonView value={result} />
      </details>
    </div>
  );
}
function SkillsTab({ p }: { p: PageProps }) {
  const [project, setProject] = useProject();
  const [selected, setSelected] = useState<SkillSummary | null>(null),
    [query, setQuery] = useState("民用航空复合材料"),
    [document, setDocument] = useState(""),
    [mode, setMode] = useState(p.data.system?.default_mode || "rehearsal"),
    [result, setResult] = useState<any>(null);
  const task = useTask(p);
  const toolName = (id: string) =>
    p.data.tools?.find((t) => t.id === id)?.name || id;
  return (
    <>
      <div className="skills-grid">
        {(p.data.skills || []).map((s, i) => (
          <Panel key={s.id} className="skill-card">
            <div className="card-top">
              <div className="skill-icon">
                {i % 3 === 0 ? (
                  <Search size={23} />
                ) : i % 3 === 1 ? (
                  <Network size={23} />
                ) : (
                  <FileText size={23} />
                )}
              </div>
              <Badge status={s.status || "ready"} />
            </div>
            <h2>{s.name}</h2>
            <p className="card-description">
              {s.description || "在流程节点中调用此技能完成指定任务。"}
            </p>
            <div className="skill-meta">
              <span>版本 {s.version || "—"}</span>
              <span>
                {s.execution_mode === "agent" ? "智能体执行" : "固定流程"}
              </span>
              <span>
                适用节点：
                {(s.node_kinds || [])
                  .map((k) => nodeKindNames[k] || k)
                  .join("、") || "未限定"}
              </span>
              <span>{s.evidence_required ? "要求返回证据" : "不强制证据"}</span>
            </div>
            {(s.allowed_tools || []).length > 0 && (
              <div className="agent-skills">
                {s.allowed_tools!.map((id) => (
                  <Badge key={id}>{toolName(id)}</Badge>
                ))}
              </div>
            )}
            {s.note && <p className="muted">{s.note}</p>}
            <div className="skill-id">{s.id}</div>
            <button
              className="button small"
              onClick={() => {
                setSelected(s);
                setResult(null);
              }}
            >
              测试技能
              <ArrowRight size={14} />
            </button>
          </Panel>
        ))}
      </div>
      {selected && (
        <Modal
          title={`${selected.name} · 技能测试`}
          onClose={() => setSelected(null)}
          wide
        >
          <div className="form-grid">
            <Field label="项目范围">
              <select
                value={project}
                onChange={(e) => {
                  setProject(e.target.value);
                  setDocument("");
                }}
              >
                {(p.data.projects || []).map((x: any) => (
                  <option key={x.id} value={x.id}>
                    {x.name}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="模式">
              <select value={mode} onChange={(e) => setMode(e.target.value)}>
                <option value="rehearsal">本地演练</option>
                <option value="live">真实模型 · 计费</option>
              </select>
            </Field>
          </div>
          <Field label="输入问题 / 指令">
            <textarea
              rows={3}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </Field>
          <Field label="资料（解析 / OCR / 多模态需要选择）">
            <select
              value={document}
              onChange={(e) => setDocument(e.target.value)}
            >
              <option value="">使用项目资料范围</option>
              {(p.data.documents || [])
                .filter((d: any) => d.project_id === project)
                .map((d: any) => (
                  <option key={d.id} value={d.id}>
                    {d.name}
                  </option>
                ))}
            </select>
          </Field>
          <div className="modal-actions">
            <ModeBadge mode={mode} />
            <ActionButton
              className="button primary"
              disabled={!p.canEdit}
              onClick={() =>
                task(async () =>
                  setResult(
                    await testSkill(selected.id, {
                      query,
                      prompt: query,
                      project_id: project,
                      document_id: document || undefined,
                      document_ids: document ? [document] : undefined,
                      mode,
                    }),
                  ),
                )
              }
            >
              <Play size={15} />
              运行技能
            </ActionButton>
          </div>
          {result !== null && <SkillTestResult result={result} />}
        </Modal>
      )}
    </>
  );
}
function ToolTestModal({
  tool,
  p,
  onClose,
}: {
  tool: ToolSummary;
  p: PageProps;
  onClose: () => void;
}) {
  const fields = toolInputFields(tool.input_schema);
  const [values, setValues] = useState<Record<string, string | boolean>>({}),
    [mode, setMode] = useState(p.data.system?.default_mode || "rehearsal"),
    [result, setResult] = useState<any>(null);
  const task = useTask(p);
  return (
    <Modal title={`${tool.name} · 工具测试`} onClose={onClose} wide>
      <p className="muted">
        参数按工具的输入约束（input_schema）生成，提交前由后端再次校验。
      </p>
      {fields.length ? (
        fields.map((field) =>
          field.kind === "boolean" ? (
            <label className="checkbox-label" key={field.name}>
              <input
                type="checkbox"
                checked={Boolean(values[field.name])}
                onChange={(e) =>
                  setValues({ ...values, [field.name]: e.target.checked })
                }
              />
              {field.name}
              {field.required && <small>必填</small>}
            </label>
          ) : (
            <Field
              key={field.name}
              label={`${field.name}${field.required ? "（必填）" : ""}`}
              hint={field.array ? "多个取值用逗号分隔" : field.description}
            >
              <input
                type={field.kind === "number" ? "number" : "text"}
                value={String(values[field.name] ?? "")}
                onChange={(e) =>
                  setValues({ ...values, [field.name]: e.target.value })
                }
              />
            </Field>
          ),
        )
      ) : (
        <InlineMessage>该工具无需输入参数。</InlineMessage>
      )}
      <div className="modal-actions">
        <select
          value={mode}
          onChange={(e) => setMode(e.target.value)}
          aria-label="测试模式"
        >
          <option value="rehearsal">本地演练</option>
          <option value="live">真实执行</option>
        </select>
        <ActionButton
          className="button primary"
          disabled={!p.canEdit}
          onClick={() =>
            task(async () =>
              setResult(
                await testTool(tool.id, collectToolInput(fields, values), mode),
              ),
            )
          }
        >
          <Play size={15} />
          运行工具
        </ActionButton>
      </div>
      {result && (
        <div className="test-result">
          <div className="report-mode">
            <Badge status={result.status}>
              {capabilityStatusText(result.status)}
            </Badge>
            {result.trace?.duration_ms !== undefined && (
              <span className="muted">耗时 {result.trace.duration_ms} ms</span>
            )}
          </div>
          {result.status === "dry_run" && (
            <InlineMessage>
              {result.data?.note || "演练模式仅完成预检，未发起网络/模型调用。"}
            </InlineMessage>
          )}
          {result.error && (
            <InlineMessage error>
              {capabilityErrorText(result.error.code)}：{result.error.message}
            </InlineMessage>
          )}
          <TraceView trace={result.trace} />
          {result.text && <div className="result-text">{result.text}</div>}
          <details className="metric-details" open>
            <summary>返回数据</summary>
            <JsonView value={result.data ?? {}} />
          </details>
        </div>
      )}
    </Modal>
  );
}
function ToolsTab({ p }: { p: PageProps }) {
  const [selected, setSelected] = useState<ToolSummary | null>(null);
  return (
    <>
      <div className="skills-grid">
        {(p.data.tools || []).map((t) => {
          const health = toolHealth(t, p.data.mcp_servers || []);
          return (
            <Panel key={t.id} className="skill-card">
              <div className="card-top">
                <div className="skill-icon">
                  <Wrench size={22} />
                </div>
                <Badge>{providerNames[t.provider] || t.provider}</Badge>
              </div>
              <h2>{t.name}</h2>
              <div className="skill-meta">
                <span>{t.read_only === false ? "可写" : "只读"}</span>
                <span>{networkNames[t.network || "none"]}</span>
                <span>外发：{egressNames[t.data_egress || "none"]}</span>
                {t.requires_confirmation && <span>需人工确认</span>}
                <span>超时 {t.timeout_seconds ?? "—"} 秒</span>
              </div>
              <div className="agent-skills">
                <Badge status={health.status}>{health.label}</Badge>
              </div>
              <div className="skill-id">{t.id}</div>
              <button className="button small" onClick={() => setSelected(t)}>
                测试工具
                <ArrowRight size={14} />
              </button>
            </Panel>
          );
        })}
      </div>
      {selected && (
        <ToolTestModal
          tool={selected}
          p={p}
          onClose={() => setSelected(null)}
        />
      )}
    </>
  );
}
function McpServersTab({ p }: { p: PageProps }) {
  const [health, setHealth] = useState<Record<string, any>>({}),
    [refreshed, setRefreshed] = useState<Record<string, any>>({});
  const task = useTask(p);
  const isAdmin = p.data.user?.role === "admin";
  return (
    <div className="skills-grid">
      {(p.data.mcp_servers || []).map((server) => {
        const h = health[server.id];
        const status = mcpHealthText(h);
        const discovered = refreshed[server.id]?.discovered?.length;
        return (
          <Panel key={server.id} className="skill-card">
            <div className="card-top">
              <div className="skill-icon">
                <Server size={22} />
              </div>
              <Badge status={server.enabled ? "ready" : "pending"}>
                {server.enabled ? "已启用" : "已禁用"}
              </Badge>
            </div>
            <h2>{server.id}</h2>
            <div className="skill-meta">
              <span>
                连接方式：
                {server.transport === "stdio" ? "本地进程（stdio）" : "HTTP"}
              </span>
              <span>已允许 {(server.tool_allowlist || []).length} 个工具</span>
              <span>
                已发现 {discovered ?? (h?.status === "ready" ? h.tools : "—")}{" "}
                个工具
              </span>
            </div>
            {!server.enabled && (
              <InlineMessage>
                该服务在配置中登记为禁用，属于正常状态；启用后才能健康检查与发现工具。
              </InlineMessage>
            )}
            {h && server.enabled && (
              <InlineMessage error={status.status === "unavailable"}>
                {status.label}
              </InlineMessage>
            )}
            {refreshed[server.id] && (
              <InlineMessage>
                已重新发现并登记 {refreshed[server.id].registered?.length ?? 0}{" "}
                个工具。
              </InlineMessage>
            )}
            <div className="card-footer">
              <ActionButton
                className="button small"
                onClick={() =>
                  task(async () => {
                    const result = await mcpHealth(server.id);
                    setHealth((m) => ({ ...m, [server.id]: result }));
                  })
                }
              >
                <ShieldCheck size={14} />
                健康检查
              </ActionButton>
              {isAdmin && (
                <ActionButton
                  className="button small"
                  onClick={() =>
                    task(async () => {
                      const result = await mcpRefresh(server.id);
                      setRefreshed((m) => ({ ...m, [server.id]: result }));
                    }, "MCP 工具已重新发现")
                  }
                >
                  <RefreshCw size={14} />
                  刷新发现
                </ActionButton>
              )}
            </div>
          </Panel>
        );
      })}
      {!p.data.mcp_servers?.length && <Empty title="尚未登记 MCP 服务" />}
    </div>
  );
}
export function SkillsPage(p: PageProps) {
  const [tab, setTab] = useState("skills");
  const stats = p.data.capability_stats;
  return (
    <>
      <SectionTitle
        eyebrow="SKILLS & TOOLS"
        title="技能工具箱"
        detail="技能编排工具、工具由本地 / HTTP / MCP 提供方执行；通过实际测试验证可用性。"
      />
      <div className="toolbar">
        <div className="tabs">
          {[
            ["skills", `技能 ${stats?.skills ?? p.data.skills?.length ?? 0}`],
            ["tools", `工具 ${stats?.tools ?? p.data.tools?.length ?? 0}`],
            [
              "mcp",
              `MCP 服务 ${stats?.mcp_servers ?? p.data.mcp_servers?.length ?? 0}`,
            ],
          ].map(([id, n]) => (
            <button
              key={id}
              className={tab === id ? "active" : ""}
              onClick={() => setTab(id)}
            >
              {n}
            </button>
          ))}
        </div>
        {stats && (
          <span className="muted">健康工具 {stats.healthy_tools} 个</span>
        )}
      </div>
      {tab === "skills" ? (
        <SkillsTab p={p} />
      ) : tab === "tools" ? (
        <ToolsTab p={p} />
      ) : (
        <McpServersTab p={p} />
      )}
    </>
  );
}
export function WorkflowsPage(p: PageProps) {
  const [category, setCategory] = useState("all"),
    [search, setSearch] = useState("");
  const task = useTask(p),
    items = (p.data.workflows || []).filter(
      (w: any) =>
        (category === "all" || w.category === category) &&
        `${w.name} ${w.description}`.includes(search),
    );
  return (
    <>
      <SectionTitle
        eyebrow="WORKFLOW STUDIO"
        title="流程与模板"
        detail="复用研究方法，在可视化画布中编排智能体、检索、条件与人工审核。"
        actions={
          <button
            className="button primary"
            disabled={!p.canEdit}
            onClick={() =>
              task(async () => {
                const w = await post("/workflows", {
                  name: "未命名研究流程",
                  description: "",
                  category: "technology",
                  nodes: [
                    {
                      id: "start",
                      type: "task",
                      position: { x: 100, y: 140 },
                      data: { label: "开始研究", kind: "start", config: {} },
                    },
                    {
                      id: "end",
                      type: "task",
                      position: { x: 480, y: 140 },
                      data: { label: "完成", kind: "end", config: {} },
                    },
                  ],
                  edges: [{ id: "start-end", source: "start", target: "end" }],
                });
                p.editWorkflow(w);
              })
            }
          >
            <Plus size={17} />
            空白流程
          </button>
        }
      />
      <div className="toolbar">
        <div className="tabs">
          {[["all", "全部流程"], ...Object.entries(categoryNames)].map(
            ([id, n]) => (
              <button
                className={category === id ? "active" : ""}
                key={id}
                onClick={() => setCategory(id)}
              >
                {n}
              </button>
            ),
          )}
        </div>
        <div className="search-field compact">
          <Search size={17} />
          <input
            placeholder="搜索流程"
            aria-label="搜索流程"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
      </div>
      {items.length ? (
        <div className="workflow-grid">
          {items.map((w: any, i: number) => (
            <Panel key={w.id} className="workflow-card">
              <div className={`workflow-mini tint-${i % 3}`}>
                <div className="mini-node">
                  <Play size={15} />
                </div>
                <i />
                <div className="mini-node">
                  <Search size={15} />
                </div>
                <i />
                <div className="mini-node">
                  <BrainCircuit size={15} />
                </div>
                <i />
                <div className="mini-node">
                  <FileText size={15} />
                </div>
                <span>{w.nodes?.length || 0} NODES</span>
              </div>
              <div className="workflow-card-body">
                <div className="card-top">
                  <Badge>{categoryNames[w.category] || w.category}</Badge>
                  <Badge status={w.status} />
                </div>
                <h2>{w.name}</h2>
                <p className="card-description">
                  {w.description || "打开画布配置流程节点、技能及执行规则。"}
                </p>
                <div className="workflow-meta">
                  <span>版本 {w.version || 1}</span>
                  <span>{w.nodes?.length || 0} 个节点</span>
                  <span>{w.edges?.length || 0} 条连线</span>
                </div>
                <div className="card-footer">
                  <button
                    className="button small"
                    onClick={() => p.editWorkflow(w)}
                  >
                    <GitBranch size={14} />
                    打开画布
                  </button>
                  <div>
                    <ActionButton
                      className="icon-button"
                      disabled={!p.canEdit}
                      onClick={() =>
                        task(async () => {
                          const copy = await post(`/workflows/${w.id}/clone`);
                          p.editWorkflow(copy);
                        }, "已复制流程")
                      }
                    >
                      <Copy size={16} />
                    </ActionButton>
                    <ActionButton
                      className="icon-button danger"
                      disabled={!p.canEdit}
                      onClick={() => {
                        if (
                          window.confirm(
                            `删除流程“${w.name}”？已产生的运行记录会保留执行快照，不受影响。`,
                          )
                        )
                          return task(
                            () => remove(`/workflows/${w.id}`),
                            "流程已删除",
                          );
                      }}
                    >
                      <Trash2 size={16} />
                    </ActionButton>
                    <button
                      className="button primary small"
                      disabled={!p.canEdit}
                      onClick={() =>
                        w.status === "published"
                          ? p.go("runs", `workflow:${w.id}`)
                          : p.editWorkflow(w)
                      }
                    >
                      <Play size={14} />
                      {w.status === "published" ? "运行" : "编辑并发布"}
                    </button>
                  </div>
                </div>
              </div>
            </Panel>
          ))}
        </div>
      ) : (
        <Empty title="没有匹配的流程" />
      )}
    </>
  );
}
