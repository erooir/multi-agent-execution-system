import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
} from "react";
import {
  Activity,
  ArrowRight,
  Bot,
  ChevronRight,
  CircleHelp,
  Database,
  FlaskConical,
  Gauge,
  Layers3,
  LogOut,
  Menu,
  Network,
  RefreshCw,
  Search,
  Settings2,
  ShieldCheck,
  Sparkles,
  Workflow,
  Wrench,
  X,
} from "lucide-react";
import { api, ApiError, post } from "./api";
import { projectLabel } from "./projects";
import { money, projectItems, taskSummary, taskTypes, time } from "./domain";
import type { Bootstrap, Item, Mutation, Page, User } from "./types";
import { Badge, Confirm, Empty, ErrorLine, Panel, SectionTitle } from "./ui";
import { AgentsPage, DataPage, WorkflowsPage } from "./AssetPages";
import TasksPage from "./TaskPages";
import CapabilityPages from "./CapabilityPages";
import { modeName } from "./domain";
import MetricsPanel from "./MetricsPanel";
import Casework, { type CaseTab } from "./Casework";
import ToolCatalog from "./ToolCatalog";
import WorkspaceEntry from "./WorkspaceEntry";
import QuestionPortal from "./QuestionPortal";
import "./task-experience.css";
import {
  ImprovementsPage,
  KnowledgePage,
  OperationsPage,
} from "./ManagementPages";
const nav: { id: Page; name: string; icon: any; group: string }[] = [
  { id: "overview", name: "工作台", icon: Gauge, group: "工作空间" },
  { id: "casework", name: "场景分析", icon: Layers3, group: "工作空间" },
  { id: "agents", name: "智能体资产", icon: Bot, group: "工作空间" },
  { id: "tasks", name: "任务中心", icon: Activity, group: "工作空间" },
  { id: "workflows", name: "工作流", icon: Workflow, group: "资产与知识" },
  { id: "tools", name: "工具箱", icon: Wrench, group: "资产与知识" },
  { id: "capabilities", name: "扩展接入", icon: Sparkles, group: "资产与知识" },
  { id: "data", name: "我的资料", icon: Database, group: "资产与知识" },
  { id: "knowledge", name: "知识库", icon: Layers3, group: "资产与知识" },
  {
    id: "improvements",
    name: "效果评测",
    icon: FlaskConical,
    group: "治理与运维",
  },
  { id: "operations", name: "设置", icon: Settings2, group: "治理与运维" },
];
const roles: Record<string, string> = {
  requester: "提问用户",
  admin: "平台管理员",
  operator: "项目操作员",
  reviewer: "只读观察员",
};
export default function App() {
  return window.location.pathname.replace(/\/$/, "") === "/manage" ? (
    <ManagementApp />
  ) : (
    <QuestionPortal Login={Login} />
  );
}
function ManagementApp() {
  const [user, setUser] = useState<User | null>(null),
    [checking, setChecking] = useState(true),
    [authError, setAuthError] = useState(""),
    [data, setData] = useState<
      (Bootstrap & { current_project_id: string | null }) | null
    >(null),
    [projectId, setProjectId] = useState(""),
    [page, setPage] = useState<Page>("overview"),
    [taskId, setTaskId] = useState<string | null>(null),
    [agentId, setAgentId] = useState<string | null>(null),
    [agentTool, setAgentTool] = useState<string | null>(null),
    [caseTab, setCaseTab] = useState<CaseTab>("catalog"),
    [workflowId, setWorkflowId] = useState<string | null>(null),
    [error, setError] = useState(""),
    [lastSync, setLastSync] = useState(""),
    [refreshing, setRefreshing] = useState(false),
    [logout, setLogout] = useState(false),
    [mobile, setMobile] = useState(false),
    [toast, setToast] = useState<{ text: string; error: boolean } | null>(null);
  const toastTimer = useRef<ReturnType<typeof setTimeout> | null>(null),
    currentUser = useRef<string | null>(null),
    selectedProject = useRef(""),
    refreshSequence = useRef(0);
  currentUser.current = user?.id || null;
  const notify = useCallback((text: string, error = false) => {
    setToast({ text, error });
    if (toastTimer.current) clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(() => setToast(null), 6500);
  }, []);
  const refresh = useCallback(async () => {
    const expected = currentUser.current,
      project = selectedProject.current,
      sequence = ++refreshSequence.current;
    const stillCurrent = () =>
      currentUser.current === expected &&
      selectedProject.current === project &&
      refreshSequence.current === sequence;
    const base = "/v2/bootstrap?scope=current";
    try {
      type Scoped = Bootstrap & { current_project_id: string | null };
      let value: Scoped;
      try {
        value = await api<Scoped>(
          base + (project ? "&project_id=" + encodeURIComponent(project) : ""),
        );
      } catch (e) {
        if (!stillCurrent()) return;
        if (
          !project ||
          !(e instanceof ApiError) ||
          ![403, 404].includes(e.status)
        )
          throw e;
        // Membership or the selected project changed. Remove old contents before loading an accessible project.
        setData(null);
        value = await api<Scoped>(base);
      }
      if (!stillCurrent()) return;
      setData((previous) => ({
        ...value,
        tasks: value.tasks.map((task) => {
          const known =
            previous?.current_project_id === value.current_project_id
              ? previous.tasks.find((item) => item.id === task.id)
              : null;
          return known &&
            Date.parse(known.updated_at) > Date.parse(task.updated_at)
            ? {
                ...task,
                status: known.status,
                revision: known.revision,
                reason_code: known.reason_code,
                finished_at: known.finished_at,
                updated_at: known.updated_at,
                cancel_requested: known.cancel_requested,
              }
            : task;
        }),
      }));
      setUser(value.user);
      setError("");
      const active = value.current_project_id || "";
      if (project !== active) {
        setTaskId(null);
        setAgentId(null);
        setWorkflowId(null);
      }
      selectedProject.current = active;
      setProjectId(active);
      setLastSync(new Date().toISOString());
    } catch (e) {
      if (!stillCurrent()) return;
      if (e instanceof ApiError && e.status === 401) {
        setUser(null);
        setData(null);
        setAuthError("登录已过期，请重新登录。");
      } else setError((e as Error).message);
      throw e;
    }
  }, []);
  const taskUpdated = useCallback((task: Item) => {
    setData((previous) =>
      previous
        ? {
            ...previous,
            tasks: previous.tasks.map((item) =>
              item.id !== task.id ||
              Date.parse(item.updated_at) > Date.parse(task.updated_at)
                ? item
                : {
                    ...item,
                    status: task.status,
                    revision: task.revision,
                    reason_code: task.reason_code,
                    finished_at: task.finished_at,
                    updated_at: task.updated_at,
                    cancel_requested: task.cancel_requested,
                  },
            ),
          }
        : previous,
    );
  }, []);
  useEffect(() => {
    let active = true;
    api<User>("/auth/me")
      .then((value) => active && setUser(value))
      .catch((e) => {
        if (active && (!(e instanceof ApiError) || e.status !== 401))
          setAuthError("无法连接服务：" + e.message);
      })
      .finally(() => active && setChecking(false));
    return () => {
      active = false;
    };
  }, []);
  useEffect(() => {
    if (!user) return;
    if (user.role === "requester") {
      window.location.replace("/");
      return;
    }
    void refresh().catch(() => {});
    const timer = setInterval(() => void refresh().catch(() => {}), 10000);
    return () => clearInterval(timer);
  }, [user?.id, refresh]);
  useEffect(
    () => () => {
      if (toastTimer.current) clearTimeout(toastTimer.current);
    },
    [],
  );
  const mutate: Mutation = async (fn, message) => {
    try {
      const value = await fn();
      await refresh().catch(() => {});
      if (message) notify(message);
      return value;
    } catch (e) {
      notify((e as Error).message, true);
      throw e;
    }
  };
  function go(next: Page) {
    setPage(next);
    setCaseTab("catalog");
    setAgentTool(null);
    setMobile(false);
    setTaskId(null);
    setAgentId(null);
    setWorkflowId(null);
  }
  function selectProject(id: string) {
    if (selectedProject.current === id) return;
    selectedProject.current = id;
    setCaseTab("catalog");
    setAgentTool(null);
    setProjectId(id);
    setTaskId(null);
    setAgentId(null);
    setWorkflowId(null);
    void refresh().catch(() => {});
  }
  if (checking)
    return (
      <div className="full-loading">
        <span className="brand-mark">
          <Network size={28} />
        </span>
        <p>正在连接航智平台…</p>
      </div>
    );
  if (!user)
    return (
      <Login
        initialError={authError}
        onLogin={(value) => {
          selectedProject.current = "";
          refreshSequence.current++;
          setUser(value);
          setAuthError("");
          setData(null);
          setProjectId("");
          setPage("overview");
        }}
      />
    );
  if (user.role === "requester")
    return <p role="status">正在进入问答工作台…</p>;
  const canEdit = user.role !== "reviewer",
    scoped = (kind: keyof Bootstrap) =>
      projectItems(data?.[kind] as Item[], projectId),
    agents = scoped("agents"),
    tasks = scoped("tasks"),
    workflows = scoped("workflows"),
    assets = scoped("data_assets"),
    project = data?.projects.find((p) => p.id === projectId);
  const common = { projectId, canEdit, mutate, notify },
    openTask = (id: string) => {
      setTaskId(id);
      setPage("tasks");
    },
    openAgent = (id: string, toolId?: string) => {
      setAgentId(id);
      setAgentTool(toolId || null);
      setPage("agents");
    },
    openCasework = (tab: CaseTab) => {
      go("casework");
      setCaseTab(tab);
    };
  return (
    <div className="app-shell">
      <a href="#main-content" className="skip-link">
        跳到主要内容
      </a>
      {mobile && (
        <button
          className="sidebar-backdrop"
          aria-label="关闭导航"
          onClick={() => setMobile(false)}
        />
      )}
      <aside className={`sidebar ${mobile ? "open" : ""}`}>
        <div className="brand">
          <span className="brand-mark">
            <Network size={25} />
          </span>
          <div>
            <strong>航智</strong>
            <span>AI 工作空间</span>
          </div>
          <span className="version">v2</span>
        </div>
        <div className="project-picker">
          <label htmlFor="project-select">当前项目</label>
          <select
            id="project-select"
            value={projectId}
            onChange={(e) => selectProject(e.target.value)}
          >
            {data?.projects.length ? (
              data.projects.map((p) => (
                <option key={p.id} value={p.id}>
                  {projectLabel(p, data.projects)}
                </option>
              ))
            ) : (
              <option value="">暂无可访问项目</option>
            )}
          </select>
        </div>
        <nav aria-label="主导航">
          {nav.map((item, index) => {
            const Icon = item.icon;
            return (
              <div key={item.id}>
                {(index === 0 || nav[index - 1].group !== item.group) && (
                  <div className="nav-label">{item.group}</div>
                )}
                <button
                  className={`nav-item ${page === item.id ? "active" : ""}`}
                  aria-current={page === item.id ? "page" : undefined}
                  onClick={() => go(item.id)}
                >
                  <Icon size={19} />
                  {item.name}
                  {item.id === "tasks" && taskSummary(tasks).active > 0 && (
                    <span className="nav-count">
                      {taskSummary(tasks).active}
                    </span>
                  )}
                </button>
              </div>
            );
          })}
        </nav>
        <div className="sidebar-bottom">
          <div className="local-state">
            <span className={`status-dot ${error ? "bad" : ""}`} />
            <div>
              <strong>{error ? "连接异常" : "服务已连接"}</strong>
            </div>
          </div>
          <button className="account" onClick={() => setLogout(true)}>
            <span className="avatar">{user.name?.slice(0, 1) || "用"}</span>
            <span>
              <strong>{user.name || user.username}</strong>
              <small>{roles[user.role]}</small>
            </span>
            <LogOut size={17} />
          </button>
        </div>
      </aside>
      <div className="main-shell">
        <header className="topbar">
          <div className="breadcrumb">
            <button
              className="icon-button mobile-toggle"
              aria-label="打开导航"
              onClick={() => setMobile(true)}
            >
              <Menu size={21} />
            </button>
            <span title={project?.id}>
              {project && data ? projectLabel(project, data.projects) : "平台"}
            </span>
            <ChevronRight size={15} />
            <strong>{nav.find((n) => n.id === page)?.name}</strong>
          </div>
          <div className="top-actions">
            <a className="desk-link" href="/">
              返回问答
            </a>
            <span className={`connection ${error ? "error" : ""}`}>
              <span className="status-dot" />
              {error
                ? "同步中断"
                : lastSync
                  ? `已同步 ${time(lastSync).split(" ").pop()}`
                  : "正在连接"}
            </span>
            <button
              className="icon-button"
              disabled={refreshing}
              aria-label="刷新工作空间"
              title="刷新持久记录"
              onClick={async () => {
                setRefreshing(true);
                try {
                  await refresh();
                } catch {
                } finally {
                  setRefreshing(false);
                }
              }}
            >
              <RefreshCw size={18} className={refreshing ? "spin" : ""} />
            </button>
            <span className="environment">
              {data
                ? data.system.offline
                  ? "本地模式"
                  : "允许云端"
                : "读取部署策略中"}
            </span>
          </div>
        </header>
        <main id="main-content" tabIndex={-1}>
          {error && (
            <ErrorLine>
              同步失败：{error}。已加载的记录保留显示，请检查服务连接并刷新。
            </ErrorLine>
          )}
          {!data || (data.current_project_id || "") !== projectId ? (
            <div className="loading">正在加载平台数据…</div>
          ) : !projectId && page !== "operations" ? (
            <Empty
              title="暂无可访问项目"
              description={
                canEdit
                  ? "创建一个项目，隔离管理智能体、数据与任务。"
                  : "请联系项目管理员添加您的项目访问权限。"
              }
              action={
                canEdit && (
                  <button className="primary" onClick={() => go("operations")}>
                    管理项目
                  </button>
                )
              }
            />
          ) : (
            <>
              {page === "overview" && (
                <Overview
                  key={projectId}
                  data={data}
                  project={project}
                  agents={agents}
                  tasks={tasks}
                  assets={assets}
                  workflows={workflows}
                />
              )}
              {page === "casework" && (
                <Casework
                  key={projectId}
                  {...common}
                  assets={assets}
                  agents={agents}
                  workflows={workflows}
                  tasks={tasks}
                  offline={!!data.system.offline}
                  initialTab={caseTab}
                  onTask={openTask}
                  onAgent={openAgent}
                />
              )}
              {page === "agents" && (
                <AgentsPage
                  key={projectId}
                  {...common}
                  agents={agents}
                  assets={assets}
                  workflows={workflows}
                  onWorkflow={(id) => {
                    setWorkflowId(id);
                    setPage("workflows");
                  }}
                  capabilities={
                    Array.isArray(data.capabilities) ? data.capabilities : []
                  }
                  onTask={openTask}
                  initialId={agentId}
                  initialToolId={agentTool}
                  offline={!!data.system.offline}
                />
              )}
              {page === "tools" && (
                <ToolCatalog
                  key={projectId}
                  projectId={projectId}
                  agents={agents}
                  canEdit={canEdit}
                  onAgent={openAgent}
                />
              )}
              {page === "tasks" && (
                <TasksPage
                  key={projectId}
                  {...common}
                  tasks={tasks}
                  agents={agents}
                  assets={assets}
                  workflows={workflows}
                  schedules={scoped("schedules")}
                  selectedId={taskId}
                  onSelect={setTaskId}
                  onAgent={openAgent}
                  onTaskUpdated={taskUpdated}
                />
              )}
              {page === "workflows" && (
                <WorkflowsPage
                  key={projectId}
                  {...common}
                  workflows={workflows}
                  agents={agents}
                  initialId={workflowId}
                />
              )}
              {page === "capabilities" && (
                <CapabilityPages
                  key={projectId}
                  {...common}
                  isAdmin={user.role === "admin"}
                />
              )}
              {page === "data" && (
                <DataPage key={projectId} {...common} assets={assets} />
              )}
              {page === "knowledge" && (
                <KnowledgePage
                  key={projectId}
                  {...common}
                  connectors={scoped("connectors")}
                  memories={scoped("memories")}
                  assets={assets}
                />
              )}
              {page === "improvements" && (
                <ImprovementsPage
                  key={projectId}
                  {...common}
                  isAdmin={user.role === "admin"}
                  improvements={scoped("improvements")}
                  evaluations={scoped("evaluations")}
                  workers={data.workers}
                  agents={agents}
                  tasks={tasks}
                  onTask={openTask}
                />
              )}
              {page === "operations" && (
                <OperationsPage
                  data={data}
                  user={user}
                  projectId={projectId}
                  mutate={mutate}
                  notify={notify}
                  onProject={selectProject}
                />
              )}
            </>
          )}
        </main>
        <footer className="app-footer">
          <span>航智 · AI 工作空间</span>
          <span>持久任务 · 独立版本 · 可追溯交付</span>
        </footer>
      </div>
      {toast && (
        <div
          className={`toast ${toast.error ? "error" : ""}`}
          role={toast.error ? "alert" : "status"}
        >
          {toast.text}
          <button
            aria-label="关闭消息"
            className="icon-button"
            onClick={() => setToast(null)}
          >
            <X size={16} />
          </button>
        </div>
      )}
      {logout && (
        <Confirm
          title="退出当前登录？"
          description="任务和计划继续由服务端执行。退出后需要重新验证身份才能访问项目。"
          confirm="退出登录"
          onClose={() => setLogout(false)}
          onConfirm={async () => {
            await post("/auth/logout");
            setLogout(false);
            setUser(null);
            setData(null);
          }}
        />
      )}
    </div>
  );
}
function Login({
  initialError,
  onLogin,
}: {
  initialError: string;
  onLogin: (user: User) => void;
}) {
  const [register, setRegister] = useState(false),
    [username, setUsername] = useState(""),
    [password, setPassword] = useState(""),
    [name, setName] = useState(""),
    [error, setError] = useState(initialError),
    [busy, setBusy] = useState(false);
  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const user = await post(
        register ? "/auth/register" : "/auth/login",
        register ? { username, password, name } : { username, password },
      );
      onLogin(user.user);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="login-layout">
      <section className="login-story">
        <div className="brand">
          <span className="brand-mark">
            <Network size={26} />
          </span>
          <strong>航智</strong>
          <span>智能体管理平台</span>
        </div>
        <div className="login-message">
          <span className="eyebrow">HANGZHI · AI WORKSPACE</span>
          <h1>
            从资料到洞见，
            <br />
            让工作更简单。
          </h1>
          <p>整理资料、协作分析，让每一步都有据可循。</p>
          <div className="login-diagram" aria-hidden="true">
            <div>
              <Database size={21} />
              <span>提交问题</span>
            </div>
            <ArrowRight size={22} />
            <div className="accent">
              <Bot size={23} />
              <span>结合资料分析</span>
            </div>
            <ArrowRight size={22} />
            <div>
              <ShieldCheck size={22} />
              <span>答复与依据</span>
            </div>
          </div>
          <div className="login-principles">
            <span>使用授权项目资料</span>
            <span>保留材料出处</span>
            <span>保留个人问题记录</span>
          </div>
        </div>
        <small className="login-footnote">航智智能体管理平台</small>
      </section>
      <section className="login-form-side">
        <form className="login-form" onSubmit={submit}>
          <h2>{register ? "创建账户" : "欢迎回来"}</h2>
          <p>
            {register
              ? "创建提问账户。管理员分配项目后，即可使用项目资料提问。"
              : "登录航智，开始今天的工作。"}
          </p>
          <fieldset disabled={busy}>
            <label className="field">
              <span>用户名</span>
              <input
                autoComplete="username"
                required
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder="请输入用户名"
              />
            </label>
            {register && (
              <label className="field">
                <span>显示名称</span>
                <input
                  required
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="您的姓名或工作名称"
                />
              </label>
            )}
            <label className="field">
              <span>密码</span>
              <input
                type="password"
                required
                autoComplete={register ? "new-password" : "current-password"}
                minLength={register ? 8 : undefined}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder={register ? "8 至 128 位" : "请输入密码"}
              />
            </label>
            {error && <ErrorLine>{error}</ErrorLine>}
            <button type="submit" className="primary login-submit">
              {busy ? "正在验证…" : register ? "创建账户" : "登录"}
              <ArrowRight size={18} />
            </button>
            <button
              type="button"
              className="login-switch"
              onClick={() => {
                setRegister(!register);
                setError("");
              }}
            >
              {register ? "已有账户？返回登录" : "没有账户？创建本地账户"}
            </button>
          </fieldset>
          <div className="login-security">
            <ShieldCheck size={16} />
            <span>身份与项目权限由服务端验证</span>
          </div>
        </form>
      </section>
    </div>
  );
}
function Overview({
  data,
  project,
  agents,
  tasks,
  assets,
  workflows,
}: {
  data: Bootstrap;
  project?: Item;
  agents: Item[];
  tasks: Item[];
  assets: Item[];
  workflows: Item[];
}) {
  const realTasks = tasks.filter((t) => t.request?.mode !== "rehearsal"),
    summary = taskSummary(realTasks),
    recent = [...tasks]
      .sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)))
      .slice(0, 6),
    capabilities = Array.isArray(data.capabilities) ? data.capabilities : [];
  return (
    <>
      <SectionTitle
        eyebrow="WORKSPACE OVERVIEW"
        title="工作台"
        description={`${project?.name || "当前项目"} · 查看智能体与用例岗位、工具授权及实际执行情况。`}
      />
      {project && <WorkspaceEntry projectId={project.id} agents={agents} />}
      <div className="metric-grid four">
        <div className="metric">
          <span>
            已发布智能体
            <Bot size={17} />
          </span>
          <strong>
            {agents.filter((a) => a.status === "published").length}
          </strong>
          <small>
            项目内 {agents.filter((a) => a.status !== "destroyed").length}{" "}
            个定义
          </small>
        </div>
        <div className="metric">
          <span>
            进行中任务
            <Activity size={17} />
          </span>
          <strong>{summary.active}</strong>
          <small>不包含配置演练</small>
        </div>
        <div className="metric">
          <span>
            成功 / 已结束
            <ShieldCheck size={17} />
          </span>
          <strong>
            {summary.terminal
              ? `${summary.succeeded} / ${summary.terminal}`
              : "尚无样本"}
          </strong>
          <small title="部分交付、失败和取消均计入已结束任务">
            不含配置演练
          </small>
        </div>
        <div className="metric">
          <span>
            可用预算
            <WalletIcon />
          </span>
          <strong>¥ {money(data.budget?.remaining_cny)}</strong>
          <small>
            项目累计上限 ¥ {data.budget?.project_cap_cny ?? "—"} · 本机领取上限
            ¥ {data.budget?.limit_cny ?? "—"}
          </small>
        </div>
      </div>
      <div className="overview-columns">
        <Panel title="最近任务">
          {recent.length ? (
            <div className="recent-tasks">
              {recent.map((t) => (
                <div key={t.id} className="recent-row">
                  <span className="recent-icon">
                    <Activity size={18} />
                  </span>
                  <div>
                    <strong>{t.name || t.request?.name || t.id}</strong>
                    <small>
                      {taskTypes[t.request?.task_type]} ·{" "}
                      {modeName(t.request?.mode)} · {time(t.created_at)}
                    </small>
                  </div>
                  <Badge status={t.status} />
                </div>
              ))}
            </div>
          ) : (
            <Empty
              title="任务记录从这里开始"
              description="先登记数据，再提交带质量约束的任务。执行记录将自动保存在当前项目。"
            />
          )}
        </Panel>
        <div className="overview-side">
          <Panel title="工作空间资源">
            <div className="resource-row">
              <Database size={18} />
              <span>有效数据资产</span>
              <strong>
                {assets.filter((a) => a.status === "active").length}
              </strong>
            </div>
            <div className="resource-row">
              <Workflow size={18} />
              <span>已发布流程</span>
              <strong>
                {workflows.filter((w) => w.status === "published").length}
              </strong>
            </div>
            <div className="resource-row">
              <Activity size={18} />
              <span>执行服务</span>
              <Badge
                status={data.system.worker_online ? "online" : "offline"}
              />
            </div>
          </Panel>
          <Panel title="可用分析方式">
            <details className="available-methods">
              <summary>查看 {capabilities.length} 种分析方式</summary>
              <div className="capability-list">
                {capabilities.map((c) => (
                  <div key={c.id}>
                    <span>{c.name || taskTypes[c.id]}</span>
                    <Badge
                      status={
                        c.available &&
                        (!data.system.offline || c.modes?.includes("local"))
                          ? "ready"
                          : "unsupported"
                      }
                    >
                      {!c.available
                        ? "待接入"
                        : data.system.offline && !c.modes?.includes("local")
                          ? "离线不可用"
                          : "已接入"}
                    </Badge>
                  </div>
                ))}
              </div>
            </details>
          </Panel>
        </div>
      </div>
      {project?.id && (
        <details className="overview-analytics">
          <summary>运行统计</summary>
          <MetricsPanel key={project.id} projectId={project.id} />
        </details>
      )}
    </>
  );
}
function WalletIcon() {
  return <span aria-hidden="true">¥</span>;
}
