import { useCallback, useEffect, useState } from "react";
import {
  Activity,
  ArrowRight,
  AudioLines,
  Bell,
  BookOpen,
  Boxes,
  BrainCircuit,
  Check,
  ChevronRight,
  ChevronsUpDown,
  Command,
  Database,
  FileCheck2,
  FlaskConical,
  FolderOpen,
  GitBranch,
  HelpCircle,
  LayoutDashboard,
  LoaderCircle,
  LogOut,
  Menu,
  Network,
  Plus,
  Search,
  Settings2,
  ShieldCheck,
  Sparkles,
  Workflow,
  X,
  Zap,
} from "lucide-react";
import { api, post, money, type RecordData } from "./api";
import { type PageId, type PageProps, roleNames } from "./types";
import {
  Badge,
  Empty,
  Field,
  InlineMessage,
  ModeBadge,
  Panel,
  SectionTitle,
  ActionButton,
} from "./ui";
import WorkflowEditor from "./WorkflowEditor";
import { ProjectProvider, useProject } from "./ProjectContext";
import {
  ProjectsPage,
  KnowledgePage,
  AgentsPage,
  SkillsPage,
  WorkflowsPage,
} from "./ResourcePages";
import {
  RunsPage,
  ReportsPage,
  EvaluationsPage,
  SystemPage,
} from "./ExecutionPages";
const navigation: { id: PageId; label: string; icon: any; group: string }[] = [
  { id: "overview", label: "工作台", icon: LayoutDashboard, group: "研究空间" },
  { id: "projects", label: "研究项目", icon: FolderOpen, group: "研究空间" },
  { id: "knowledge", label: "资料与知识", icon: Database, group: "研究空间" },
  { id: "agents", label: "智能体", icon: BrainCircuit, group: "能力编排" },
  { id: "skills", label: "技能工具箱", icon: Boxes, group: "能力编排" },
  { id: "workflows", label: "流程与模板", icon: Workflow, group: "能力编排" },
  { id: "runs", label: "运行中心", icon: Activity, group: "执行与交付" },
  { id: "reports", label: "审核与报告", icon: FileCheck2, group: "执行与交付" },
  {
    id: "evaluations",
    label: "样本评测",
    icon: FlaskConical,
    group: "执行与交付",
  },
  { id: "system", label: "系统与审计", icon: Settings2, group: "管理" },
];
export default function App() {
  return (
    <ProjectProvider>
      <Workspace />
    </ProjectProvider>
  );
}
function Workspace() {
  const [activeProject, setActiveProject] = useProject();
  const [user, setUser] = useState<RecordData | null>(null),
    [loading, setLoading] = useState(true),
    [data, setData] = useState<RecordData | null>(null),
    [page, setPage] = useState<PageId>("overview"),
    [selectedId, setSelectedId] = useState<string | undefined>(),
    [workflow, setWorkflow] = useState<RecordData | null>(null),
    [toast, setToast] = useState<{ text: string; error?: boolean } | null>(
      null,
    ),
    [connectionError, setConnectionError] = useState(""),
    [mobileNav, setMobileNav] = useState(false);
  const notify = useCallback(
    (text: string, error = false) => setToast({ text, error }),
    [],
  );
  const refresh = useCallback(async () => {
    try {
      const d = await api("/bootstrap");
      setData(d);
      setUser(d.user || null);
      setConnectionError("");
    } catch (e) {
      if ((e as any).status === 401) {
        setUser(null);
        setData(null);
      } else setConnectionError((e as Error).message);
    }
  }, []);
  useEffect(() => {
    refresh().finally(() => setLoading(false));
  }, [refresh]);
  useEffect(() => {
    if (!user) return;
    const t = setInterval(refresh, 4000);
    return () => clearInterval(t);
  }, [Boolean(user), refresh]);
  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 6500);
    return () => clearTimeout(t);
  }, [toast]);
  useEffect(() => {
    if (
      data &&
      (!activeProject ||
        !data.projects?.some((x: any) => x.id === activeProject))
    )
      setActiveProject(
        data.projects?.find((x: any) => x.category === "technology")?.id ||
          data.projects?.[0]?.id ||
          "",
      );
  }, [data?.projects, activeProject]);
  const go = (p: PageId, id?: string) => {
    if (p === "knowledge" && id) setActiveProject(id);
    setPage(p);
    setSelectedId(id);
    setMobileNav(false);
  };
  const canEdit = ["admin", "operator"].includes(user?.role),
    canReview = ["admin", "reviewer"].includes(user?.role);
  if (loading)
    return (
      <div className="splash">
        <div className="brand-symbol">
          <Network />
        </div>
        <LoaderCircle className="spin" />
        <p>正在连接研究工作台…</p>
      </div>
    );
  if (!user)
    return (
      <Login
        onLogin={async () => {
          await refresh();
        }}
        connectionError={connectionError}
      />
    );
  if (!data)
    return (
      <div className="splash">
        <InlineMessage error>
          {connectionError || "无法加载工作台数据"}
        </InlineMessage>
        <button className="button" onClick={refresh}>
          重试连接
        </button>
      </div>
    );
  const props: PageProps = {
    data,
    refresh,
    notify,
    canEdit,
    canReview,
    go,
    editWorkflow: setWorkflow,
    selectedId,
  };
  const pending = (data.runs || []).filter(
    (r: any) => r.status === "waiting_review",
  ).length;
  return (
    <div className="app-shell">
      <aside className={`sidebar ${mobileNav ? "open" : ""}`}>
        <a
          className="brand"
          href="#"
          onClick={(e) => {
            e.preventDefault();
            go("overview");
          }}
        >
          <div className="brand-symbol">
            <Network size={22} />
          </div>
          <div>
            <strong>
              知序<span> RESEARCH</span>
            </strong>
            <small>研究智能体工作台</small>
          </div>
        </a>
        <div className="workspace-switch">
          <div className="workspace-avatar">研</div>
          <div>
            <strong>研究协作空间</strong>
            <small>LOCAL WORKSPACE</small>
          </div>
          <ChevronsUpDown size={14} />
        </div>
        <nav>
          {navigation.map((n, i) => {
            const Icon = n.icon;
            return (
              <div key={n.id}>
                {(i === 0 || navigation[i - 1].group !== n.group) && (
                  <div className="nav-group">{n.group}</div>
                )}
                <button
                  className={`nav-item ${page === n.id ? "active" : ""}`}
                  onClick={() => go(n.id)}
                >
                  <Icon size={18} />
                  <span>{n.label}</span>
                  {n.id === "runs" && pending > 0 && <em>{pending}</em>}
                  {page === n.id && <span className="active-dot" />}
                </button>
              </div>
            );
          })}
        </nav>
        <div className="sidebar-bottom">
          <div className="runtime-indicator">
            <span
              className={connectionError ? "status-dot warning" : "status-dot"}
            />
            <span>AgentOS 本地运行时</span>
            <Badge>
              {data.system?.agno_version
                ? `v${data.system.agno_version}`
                : "LOCAL"}
            </Badge>
          </div>
          <div className="user-box">
            <div className="user-avatar">
              {(user.username || "U").slice(0, 1).toUpperCase()}
            </div>
            <div>
              <strong>{user.username}</strong>
              <small>{roleNames[user.role] || user.role}</small>
            </div>
            <button
              className="icon-button"
              aria-label="退出登录"
              title="退出登录"
              onClick={async () => {
                try {
                  await post("/auth/logout");
                  setUser(null);
                  setData(null);
                } catch (e) {
                  notify((e as Error).message, true);
                }
              }}
            >
              <LogOut size={16} />
            </button>
          </div>
        </div>
      </aside>
      <main className="main-shell">
        <header className="topbar">
          <div className="breadcrumb">
            <button
              className="icon-button mobile-menu"
              onClick={() => setMobileNav(!mobileNav)}
              aria-label="展开导航"
            >
              <Menu size={20} />
            </button>
            <span>研究协作空间</span>
            <ChevronRight size={14} />
            <strong>{navigation.find((x) => x.id === page)?.label}</strong>
          </div>
          <div className="topbar-right">
            <select
              className="global-project-select"
              aria-label="当前研究项目"
              value={activeProject}
              onChange={(e) => setActiveProject(e.target.value)}
            >
              {(data.projects || []).map((x: any) => (
                <option key={x.id} value={x.id}>
                  {x.name}
                </option>
              ))}
            </select>
            <span className="local-tag">
              <span className="status-dot" />
              本地 Demo
            </span>
            <button
              className="notification-button"
              title="待审核运行"
              aria-label="查看待审核运行"
              onClick={() => go("reports")}
            >
              <Bell size={18} />
              {pending > 0 && <span />}
            </button>
          </div>
        </header>
        {connectionError && (
          <div className="connection-error">
            连接暂时中断：{connectionError}
            <button onClick={refresh}>重新连接</button>
          </div>
        )}
        <div className="page-content" key={`${page}:${selectedId || ""}`}>
          {page === "overview" ? (
            <Overview {...props} />
          ) : page === "projects" ? (
            <ProjectsPage {...props} />
          ) : page === "knowledge" ? (
            <KnowledgePage {...props} />
          ) : page === "agents" ? (
            <AgentsPage {...props} />
          ) : page === "skills" ? (
            <SkillsPage {...props} />
          ) : page === "workflows" ? (
            <WorkflowsPage {...props} />
          ) : page === "runs" ? (
            <RunsPage {...props} />
          ) : page === "reports" ? (
            <ReportsPage {...props} />
          ) : page === "evaluations" ? (
            <EvaluationsPage {...props} />
          ) : (
            <SystemPage {...props} />
          )}
        </div>
        <footer className="app-footer">
          <span>知序研究工作台 · AGNO AGENTOS</span>
          <span>证据可追溯 · 过程可审查 · 成本可控制</span>
        </footer>
      </main>
      {workflow && (
        <WorkflowEditor
          key={workflow.id}
          workflow={workflow}
          agents={data.agents || []}
          skills={data.skills || []}
          canEdit={canEdit}
          onClose={() => setWorkflow(null)}
          onSaved={refresh}
          notify={notify}
          onRun={(id) => {
            setWorkflow(null);
            go("runs", `workflow:${id}`);
          }}
        />
      )}
      {toast && (
        <div role="status" className={`toast ${toast.error ? "error" : ""}`}>
          {toast.error ? <X size={18} /> : <Check size={18} />}
          <span>{toast.text}</span>
          <button
            className="icon-button"
            aria-label="关闭通知"
            onClick={() => setToast(null)}
          >
            <X size={15} />
          </button>
        </div>
      )}
    </div>
  );
}
function Login({
  onLogin,
  connectionError,
}: {
  onLogin: () => Promise<void>;
  connectionError: string;
}) {
  const [username, setUsername] = useState("admin"),
    [password, setPassword] = useState("demo12345"),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  return (
    <div className="login-page">
      <section className="login-story">
        <div className="brand">
          <div className="brand-symbol">
            <Network />
          </div>
          <strong>知序 RESEARCH</strong>
        </div>
        <div>
          <div className="eyebrow">FROM QUESTIONS TO EVIDENCE</div>
          <h1>
            让每一次研究，
            <br />
            都有据可循。
          </h1>
          <p>
            连接资料、智能体与研究流程。
            <br />
            在一个工作空间中，完成从问题到报告的全过程。
          </p>
          <div className="login-flow">
            <span>
              <Search size={21} />
              证据检索
            </span>
            <i />
            <span>
              <BrainCircuit size={21} />
              协同分析
            </span>
            <i />
            <span>
              <FileCheck2 size={21} />
              审核交付
            </span>
          </div>
        </div>
        <small>AGNO AGENTOS · LOCAL FIRST</small>
      </section>
      <section className="login-form">
        <div className="eyebrow">欢迎回到工作空间</div>
        <h2>登录研究工作台</h2>
        <p className="muted">本地演示账号，角色权限由后端验证。</p>
        <form
          onSubmit={async (e) => {
            e.preventDefault();
            setBusy(true);
            setError("");
            try {
              await post("/auth/login", { username, password });
              await onLogin();
            } catch (err) {
              setError((err as Error).message);
            } finally {
              setBusy(false);
            }
          }}
        >
          <Field label="账号">
            <select
              value={username}
              onChange={(e) => setUsername(e.target.value)}
            >
              <option value="admin">admin · 管理员</option>
              <option value="operator">operator · 研究员</option>
              <option value="reviewer">reviewer · 审核员</option>
            </select>
          </Field>
          <Field label="密码">
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
              required
            />
          </Field>
          {(error || connectionError) && (
            <InlineMessage error>{error || connectionError}</InlineMessage>
          )}
          <button
            type="submit"
            className="button primary login-submit"
            disabled={busy}
          >
            {busy ? <LoaderCircle size={18} className="spin" /> : "进入工作台"}
            <ArrowRight size={18} />
          </button>
        </form>
        <div className="demo-credentials">
          <ShieldCheck size={18} />
          <span>
            本地 Demo 默认密码：<code>demo12345</code>
            <br />
            支持管理员、研究员和审核员三个角色。
          </span>
        </div>
      </section>
    </div>
  );
}
function Overview({
  data,
  refresh,
  notify,
  canEdit,
  go,
  editWorkflow,
}: PageProps) {
  const [project, setProject] = useProject();
  const [prompt, setPrompt] = useState(""),
    [mode, setMode] = useState(data.system?.default_mode || "rehearsal");
  const runs = data.runs || [],
    active = runs.filter((r: any) => ["running", "queued"].includes(r.status)),
    pending = runs.filter((r: any) => r.status === "waiting_review");
  const create = async () => {
    if (!prompt.trim()) {
      notify("请先描述你的研究任务", true);
      return;
    }
    try {
      const w = await post("/plan", { prompt, project_id: project, mode });
      await refresh();
      editWorkflow(w);
      notify("研究流程已生成，可在画布中检查并调整");
    } catch (e) {
      notify((e as Error).message, true);
    }
  };
  return (
    <>
      <SectionTitle
        eyebrow="RESEARCH WORKSPACE"
        title="把研究想法，变成可执行的流程"
        detail="以证据为基础，让智能体协作完成检索、分析与报告。"
        actions={
          <button className="button" onClick={() => go("workflows")}>
            <Workflow size={16} />
            浏览流程模板
          </button>
        }
      />
      <section className="prompt-panel">
        <div className="prompt-intro">
          <div className="sparkle-circle">
            <Sparkles size={21} />
          </div>
          <div>
            <h2>今天，你想研究什么？</h2>
            <p>用自然语言描述目标，我们将为你规划一条可编辑的研究流程。</p>
          </div>
          <span className="prompt-number">01 / START</span>
        </div>
        <textarea
          aria-label="研究任务描述"
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          placeholder="例如：梳理现有资料中的民用航空复合材料关键技术，比较技术路线，形成附带来源的研究简报…"
          rows={3}
        />
        <div className="prompt-tools">
          <div className="prompt-options">
            <select
              aria-label="研究项目"
              value={project}
              onChange={(e) => setProject(e.target.value)}
            >
              <option value="">选择研究项目</option>
              {(data.projects || []).map((p: any) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
            <select
              aria-label="执行模式"
              value={mode}
              onChange={(e) => setMode(e.target.value)}
            >
              <option value="rehearsal">本地演练</option>
              <option value="live">真实模型 · 计费</option>
            </select>
          </div>
          <ActionButton
            className="button primary"
            disabled={!canEdit || !project}
            onClick={create}
          >
            生成研究流程
            <ArrowRight size={17} />
          </ActionButton>
        </div>
        <div className="prompt-disclaimer">
          <ModeBadge mode={mode} />
          <span>
            {mode === "rehearsal"
              ? "使用本地规则规划，检索真实本地资料；不调用外部模型。"
              : "使用 DeepSeek，通过统一预算网关调用；仅外发允许联网的资料。"}
          </span>
        </div>
      </section>
      <div className="metrics-grid">
        {[
          {
            label: "研究项目",
            value: data.projects?.length || 0,
            icon: FolderOpen,
            note: "已建立的研究空间",
            page: "projects",
          },
          {
            label: "正在运行",
            value: active.length,
            icon: Activity,
            note: "来自实际执行状态",
            page: "runs",
          },
          {
            label: "待人工审核",
            value: pending.length,
            icon: ShieldCheck,
            note: "等待审核后继续执行",
            page: "reports",
          },
          {
            label: "模型计费上界",
            value: `¥ ${money(data.budget?.spent_cny)}`,
            icon: Zap,
            note: `预算余量 ¥ ${Number(data.budget?.remaining_cny || 0).toFixed(2)}`,
            page: "system",
          },
        ].map((m) => (
          <button
            className="metric-card"
            key={m.label}
            onClick={() => go(m.page as PageId)}
          >
            <div>
              <span>{m.label}</span>
              <m.icon size={19} />
            </div>
            <strong>{m.value}</strong>
            <small>
              {m.note}
              <ArrowRight size={14} />
            </small>
          </button>
        ))}
      </div>
      <div className="overview-columns">
        <Panel
          title="最近研究任务"
          detail="从规划到交付，跟进每一步进展"
          actions={
            <button className="text-button" onClick={() => go("runs")}>
              查看全部
              <ArrowRight size={14} />
            </button>
          }
        >
          {runs.length ? (
            <div className="run-table">
              {runs.slice(0, 5).map((r: any) => (
                <button
                  key={r.id}
                  className="recent-run"
                  onClick={() => go("runs", r.id)}
                >
                  <div className="file-tile">
                    <FileCheck2 size={20} />
                  </div>
                  <div className="recent-run-text">
                    <strong>{r.name || r.workflow_name || r.prompt}</strong>
                    <small>{r.prompt?.slice(0, 60) || r.workflow_name}</small>
                  </div>
                  <Badge status={r.status} />
                  <ChevronRight size={15} />
                </button>
              ))}
            </div>
          ) : (
            <Empty
              title="你的第一项研究，从这里开始"
              detail="描述一个任务，或从已有流程模板发起一次演练。"
              action={
                <button className="button" onClick={() => go("workflows")}>
                  选择模板
                  <ArrowRight size={15} />
                </button>
              }
            />
          )}
        </Panel>
        <Panel title="工作空间就绪情况" detail="真实配置与资源状态">
          <div className="readiness-row">
            <div>
              <Database size={18} />
              <span>知识资料</span>
            </div>
            <strong>{data.documents?.length || 0} 份</strong>
          </div>
          <div className="readiness-row">
            <div>
              <BrainCircuit size={18} />
              <span>可用智能体</span>
            </div>
            <strong>
              {
                (data.agents || []).filter((a: any) => a.enabled !== false)
                  .length
              }{" "}
              个
            </strong>
          </div>
          <div className="readiness-row">
            <div>
              <Boxes size={18} />
              <span>技能工具</span>
            </div>
            <strong>{data.skills?.length || 0} 项</strong>
          </div>
          <div className="readiness-row">
            <div>
              <Zap size={18} />
              <span>DeepSeek 凭据</span>
            </div>
            <Badge status={data.system?.key_configured ? "ready" : "pending"}>
              {data.system?.key_configured ? "已配置" : "未配置"}
            </Badge>
          </div>
          <div className="workspace-note">
            <ShieldCheck size={20} />
            <p>
              每个结论保留检索证据。关键步骤可插入人工审核，模型请求受项目预算约束。
            </p>
          </div>
        </Panel>
      </div>
      <div className="section-subtitle">
        <div>
          <h2>从一个研究场景开始</h2>
          <p>六条预置流程，覆盖三类场景，可按任务调整节点与执行参数。</p>
        </div>
        <button className="text-button" onClick={() => go("workflows")}>
          全部模板
          <ArrowRight size={15} />
        </button>
      </div>
      <div className="template-grid">
        {(data.workflows || []).slice(0, 3).map((w: any, i: number) => (
          <button
            className={`template-card tint-${i}`}
            key={w.id}
            onClick={() => editWorkflow(w)}
          >
            <div className="template-icon">
              {i === 0 ? (
                <BookOpen size={22} />
              ) : i === 1 ? (
                <Network size={22} />
              ) : (
                <GitBranch size={22} />
              )}
            </div>
            <div>
              <h3>{w.name}</h3>
              <p>{w.description || "打开流程画布，配置你的研究任务。"}</p>
              <span>
                {w.nodes?.length || 0} 个节点 <ArrowRight size={14} />
              </span>
            </div>
          </button>
        ))}
      </div>
    </>
  );
}
