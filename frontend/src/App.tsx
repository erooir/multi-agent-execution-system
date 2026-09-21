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
  Modal,
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
    window.scrollTo({ top: 0, left: 0, behavior: "instant" });
  }, [page]);
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
        <p>正在连接航空情报平台…</p>
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
              航智<span> AVIATION</span>
            </strong>
            <small>航空情报平台</small>
          </div>
        </a>
        <div className="workspace-switch">
          <div className="workspace-avatar">研</div>
          <div>
            <strong>航空情报工作空间</strong>
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
            <span>
              {connectionError ? "工作空间连接待恢复" : "工作空间已连接"}
            </span>
            <Badge>{connectionError ? "重连中" : "在线"}</Badge>
          </div>
          <div className="user-box">
            <div className="user-avatar">
              {(user.name || user.username || "U").slice(0, 1).toUpperCase()}
            </div>
            <div>
              <strong>{user.name || user.username}</strong>
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
            <span>航空情报工作空间</span>
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
              服务就绪
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
          <span>航智 · 航空情报平台</span>
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
  const [mode, setMode] = useState<"login" | "register">("login"),
    [username, setUsername] = useState(""),
    [password, setPassword] = useState(""),
    [name, setName] = useState(""),
    [confirmation, setConfirmation] = useState(""),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  const registering = mode === "register";
  function switchMode(next: "login" | "register") {
    setMode(next);
    setError("");
    setPassword("");
    setConfirmation("");
  }
  return (
    <div className="login-page">
      <section className="login-story">
        <div className="brand">
          <div className="brand-symbol">
            <Network />
          </div>
          <div>
            <strong>航智</strong>
            <small>航空情报平台</small>
          </div>
        </div>
        <div>
          <div className="eyebrow">AVIATION INTELLIGENCE</div>
          <h1>
            连接航空信息，
            <br />
            洞察产业前沿。
          </h1>
          <p>
            汇集航空技术、产业动态与研究资料。
            <br />
            从情报检索到专题研判，让每个结论都有据可循。
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
        <small>航智 · 航空情报平台</small>
      </section>
      <section className="login-form">
        <div className="auth-tabs" role="tablist" aria-label="账号入口">
          <button
            type="button"
            role="tab"
            id="login-tab"
            aria-controls="auth-panel"
            aria-selected={!registering}
            className={!registering ? "active" : ""}
            disabled={busy}
            onClick={() => switchMode("login")}
          >
            登录
          </button>
          <button
            type="button"
            role="tab"
            id="register-tab"
            aria-controls="auth-panel"
            aria-selected={registering}
            className={registering ? "active" : ""}
            disabled={busy}
            onClick={() => switchMode("register")}
          >
            注册
          </button>
        </div>
        <div className="eyebrow">
          {registering ? "开启你的情报研究" : "欢迎回到航智"}
        </div>
        <h2>{registering ? "创建账号" : "登录航空情报平台"}</h2>
        <p className="muted">
          {registering
            ? "建立你的情报工作空间，开展研究与协作。"
            : "继续你的情报检索、任务编排与专题研判。"}
        </p>
        <form
          id="auth-panel"
          role="tabpanel"
          aria-labelledby={registering ? "register-tab" : "login-tab"}
          onSubmit={async (e) => {
            e.preventDefault();
            setError("");
            if (registering && password !== confirmation) {
              setError("两次输入的密码不一致，请重新确认。");
              return;
            }
            setBusy(true);
            try {
              await post(registering ? "/auth/register" : "/auth/login", {
                username: username.trim(),
                password,
                ...(registering && name.trim() ? { name: name.trim() } : {}),
              });
              await onLogin();
            } catch (err) {
              setError((err as Error).message);
            } finally {
              setBusy(false);
            }
          }}
        >
          <Field
            label="用户名"
            hint={
              registering
                ? "3–32 位字母、数字或下划线，不区分大小写。"
                : undefined
            }
          >
            <input
              name="username"
              autoComplete="username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="请输入用户名"
              required
              minLength={registering ? 3 : undefined}
              maxLength={32}
              pattern={registering ? "[A-Za-z0-9_]{3,32}" : undefined}
              disabled={busy}
            />
          </Field>
          {registering && (
            <Field label="姓名或昵称（选填）">
              <input
                name="name"
                autoComplete="nickname"
                value={name}
                onChange={(e) => setName(e.target.value)}
                maxLength={40}
                placeholder="方便在协作中识别你的称呼"
                disabled={busy}
              />
            </Field>
          )}
          <Field
            label="密码"
            hint={registering ? "请输入 8–128 位密码。" : undefined}
          >
            <input
              type="password"
              name="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete={registering ? "new-password" : "current-password"}
              placeholder={registering ? "设置登录密码" : "请输入密码"}
              minLength={registering ? 8 : undefined}
              maxLength={128}
              required
              disabled={busy}
            />
          </Field>
          {registering && (
            <Field label="确认密码">
              <input
                type="password"
                name="confirm-password"
                autoComplete="new-password"
                value={confirmation}
                onChange={(e) => setConfirmation(e.target.value)}
                placeholder="请再次输入密码"
                maxLength={128}
                required
                disabled={busy}
              />
            </Field>
          )}
          {(error || connectionError) && (
            <InlineMessage error>{error || connectionError}</InlineMessage>
          )}
          <button
            type="submit"
            className="button primary login-submit"
            disabled={busy}
          >
            <span>
              {busy
                ? registering
                  ? "正在创建账号…"
                  : "正在登录…"
                : registering
                  ? "注册并进入平台"
                  : "登录并进入平台"}
            </span>
            {busy ? (
              <LoaderCircle size={18} className="spin" />
            ) : (
              <ArrowRight size={18} />
            )}
          </button>
        </form>
        <p className="auth-switch">
          {registering ? "已有账号？" : "还没有账号？"}
          <button
            className="text-button"
            type="button"
            disabled={busy}
            onClick={() => switchMode(registering ? "login" : "register")}
          >
            {registering ? "立即登录" : "创建账号"}
          </button>
        </p>
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
    [mode, setMode] = useState(data.system?.default_mode || "rehearsal"),
    [job, setJob] = useState<RecordData | null>(null);
  const runs = data.runs || [],
    active = runs.filter((r: any) => ["running", "queued"].includes(r.status)),
    pending = runs.filter((r: any) => r.status === "waiting_review");
  useEffect(() => {
    if (!job || job.status !== "running") return;
    let cancelled = false;
    const poll = async () => {
      try {
        const current = await api(`/planning/${job.id}`);
        if (cancelled) return;
        setJob(current);
        if (current.status === "completed" && current.workflow_id) {
          await refresh();
          const workflows = await api("/workflows");
          const created = (workflows || []).find(
            (w: any) => w.id === current.workflow_id,
          );
          if (created) {
            setJob(null);
            editWorkflow(created);
            notify("研究流程已生成，可在画布中检查并调整");
          }
        }
      } catch (e) {
        if (!cancelled)
          setJob((j: any) =>
            j ? { ...j, status: "failed", error: (e as Error).message } : j,
          );
      }
    };
    const t = setInterval(poll, 800);
    poll();
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, [job?.id, job?.status]);
  const create = async () => {
    if (!prompt.trim()) {
      notify("请先描述你的研究任务", true);
      return;
    }
    try {
      const started = await post("/plan", {
        prompt,
        project_id: project,
        mode,
      });
      setJob(started);
    } catch (e) {
      notify((e as Error).message, true);
    }
  };
  return (
    <>
      <SectionTitle
        eyebrow="AVIATION INTELLIGENCE"
        title="航空情报工作台"
        detail="跟进专题任务、核查来源证据，与团队协同完成研究交付。"
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
            <h2>创建研究任务</h2>
            <p>
              描述情报需求与交付目标，为你规划可编辑的检索、分析和报告流程。
            </p>
          </div>
          <span className="prompt-number">自然语言编排</span>
        </div>
        <textarea
          aria-label="研究任务描述"
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          placeholder="例如：对比民用航空复合材料的技术路线、适航认证进展与产业应用，输出附带来源的专题情报简报…"
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
            disabled={!canEdit || !project || job?.status === "running"}
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
              <span>技能</span>
            </div>
            <strong>
              {data.capability_stats?.skills ?? data.skills?.length ?? 0} 项
            </strong>
          </div>
          <div className="readiness-row">
            <div>
              <Zap size={18} />
              <span>工具（健康 / 全部）</span>
            </div>
            <strong>
              {data.capability_stats
                ? `${data.capability_stats.healthy_tools} / ${data.capability_stats.tools}`
                : (data.tools?.length ?? 0)}{" "}
              个
            </strong>
          </div>
          <div className="readiness-row">
            <div>
              <Network size={18} />
              <span>MCP 服务</span>
            </div>
            <strong>
              {data.capability_stats?.mcp_servers ??
                data.mcp_servers?.length ??
                0}{" "}
              个
            </strong>
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
      {job && (
        <Modal
          title="正在生成研究流程"
          onClose={() => job.status !== "running" && setJob(null)}
        >
          <div className="prompt-disclaimer">
            <ModeBadge mode={job.mode || mode} />
            <span>
              {job.mode === "live"
                ? "真实模型规划：生成 → 硬规则校验 → 不通过则携带错误原因让模型修复。"
                : "本地规则规划：不调用模型。"}
            </span>
          </div>
          <div className="readiness-row">
            <div>
              {job.status === "running" ? (
                <LoaderCircle size={18} className="spin" />
              ) : job.status === "completed" ? (
                <Check size={18} />
              ) : (
                <X size={18} />
              )}
              <span>{job.stage || "排队中"}</span>
            </div>
          </div>
          {(job.attempts || []).length > 0 && (
            <div className="log-list">
              {job.attempts.map((a: any, i: number) => (
                <div className="log-entry" key={i}>
                  <span>第 {a.attempt} 次</span>
                  <code>
                    {a.status === "validated"
                      ? `校验通过（${a.nodes} 个节点 / ${a.edges} 条连线）`
                      : `校验未通过：${a.error}`}
                  </code>
                </div>
              ))}
            </div>
          )}
          {job.status === "failed" && (
            <>
              <InlineMessage error>
                {job.error || "规划失败，未保存任何流程。"}
              </InlineMessage>
              <div className="modal-actions">
                <button className="button" onClick={() => setJob(null)}>
                  关闭
                </button>
              </div>
            </>
          )}
          {job.status === "running" && (
            <p className="muted">
              正在规划，你可以看到每一次生成与校验的真实进展…
            </p>
          )}
        </Modal>
      )}
    </>
  );
}
