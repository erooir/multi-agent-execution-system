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
  Plus,
  RefreshCw,
  Search,
  Settings2,
  ShieldCheck,
  Sparkles,
  Workflow,
  X,
} from "lucide-react";
import { api, ApiError, post } from "./api";
import { money, projectItems, taskSummary, taskTypes, time } from "./domain";
import type { Bootstrap, Item, Mutation, Page, User } from "./types";
import { Badge, Confirm, Empty, ErrorLine, Panel, SectionTitle } from "./ui";
import { AgentsPage, DataPage, WorkflowsPage } from "./AssetPages";
import TasksPage from "./TaskPages";
import { modeName } from "./domain";
import {
  ImprovementsPage,
  KnowledgePage,
  OperationsPage,
} from "./ManagementPages";
const nav: { id: Page; name: string; icon: any; group: string }[] = [
  { id: "overview", name: "工作台总览", icon: Gauge, group: "工作空间" },
  { id: "agents", name: "智能体资产", icon: Bot, group: "工作空间" },
  { id: "tasks", name: "任务运行", icon: Activity, group: "工作空间" },
  { id: "workflows", name: "协作流程", icon: Workflow, group: "资产与知识" },
  { id: "data", name: "数据资产", icon: Database, group: "资产与知识" },
  { id: "knowledge", name: "知识与记忆", icon: Layers3, group: "资产与知识" },
  {
    id: "improvements",
    name: "评测与改进",
    icon: FlaskConical,
    group: "治理与运维",
  },
  { id: "operations", name: "平台运维", icon: Settings2, group: "治理与运维" },
];
const roles: Record<string, string> = {
  admin: "平台管理员",
  operator: "项目操作员",
  reviewer: "只读观察员",
};
export default function App() {
  const [user, setUser] = useState<User | null>(null),
    [checking, setChecking] = useState(true),
    [authError, setAuthError] = useState(""),
    [data, setData] = useState<Bootstrap | null>(null),
    [projectId, setProjectId] = useState(""),
    [page, setPage] = useState<Page>("overview"),
    [taskId, setTaskId] = useState<string | null>(null),
    [agentId, setAgentId] = useState<string | null>(null),
    [error, setError] = useState(""),
    [lastSync, setLastSync] = useState(""),
    [refreshing, setRefreshing] = useState(false),
    [logout, setLogout] = useState(false),
    [mobile, setMobile] = useState(false),
    [toast, setToast] = useState<{ text: string; error: boolean } | null>(null);
  const toastTimer = useRef<ReturnType<typeof setTimeout> | null>(null),
    currentUser = useRef<string | null>(null);
  currentUser.current = user?.id || null;
  const notify = useCallback((text: string, error = false) => {
    setToast({ text, error });
    if (toastTimer.current) clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(() => setToast(null), 6500);
  }, []);
  const refresh = useCallback(async () => {
    const expected = currentUser.current;
    try {
      const value = await api<Bootstrap>("/v2/bootstrap");
      if (currentUser.current !== expected) return;
      setData((previous) => ({
        ...value,
        tasks: value.tasks.map((task) => {
          const known = previous?.tasks.find((item) => item.id === task.id);
          return known &&
            Date.parse(known.updated_at) > Date.parse(task.updated_at)
            ? known
            : task;
        }),
      }));
      setUser(value.user);
      setError("");
      setProjectId((old) =>
        value.projects.some((p) => p.id === old)
          ? old
          : value.projects[0]?.id || "",
      );
      setLastSync(new Date().toISOString());
    } catch (e) {
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
    setMobile(false);
    setTaskId(null);
    setAgentId(null);
  }
  function selectProject(id: string) {
    setProjectId(id);
    setTaskId(null);
    setAgentId(null);
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
          setUser(value);
          setAuthError("");
          setData(null);
          setProjectId("");
          setPage("overview");
        }}
      />
    );
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
    openAgent = (id: string) => {
      setAgentId(id);
      setPage("agents");
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
            <span>智能体管理平台</span>
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
                  {p.name}
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
              <strong>{error ? "连接异常" : "本地持久工作空间"}</strong>
              <small>AgentOS · 权限与预算受控</small>
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
            <span>{project?.name || "平台"}</span>
            <ChevronRight size={15} />
            <strong>{nav.find((n) => n.id === page)?.name}</strong>
          </div>
          <div className="top-actions">
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
              {data?.system.offline ? "离线部署" : "本机环境"}
            </span>
          </div>
        </header>
        <main id="main-content" tabIndex={-1}>
          {error && (
            <ErrorLine>
              同步失败：{error}。已加载的记录保留显示，请检查服务连接并刷新。
            </ErrorLine>
          )}
          {!data ? (
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
                  data={data}
                  project={project}
                  agents={agents}
                  tasks={tasks}
                  assets={assets}
                  workflows={workflows}
                  go={go}
                  openTask={openTask}
                />
              )}
              {page === "agents" && (
                <AgentsPage
                  key={projectId}
                  {...common}
                  agents={agents}
                  capabilities={
                    Array.isArray(data.capabilities) ? data.capabilities : []
                  }
                  onTask={openTask}
                  initialId={agentId}
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
                  agents={agents}
                  tasks={tasks}
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
          <span>航智智能体管理平台</span>
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
          <span className="eyebrow">HANGZHI AGENT PLATFORM</span>
          <h1>
            让每一种能力，
            <br />
            成为可管理的资产。
          </h1>
          <p>
            组织智能体，连接可信数据，
            <br />
            让任务从目标走向可追溯的交付。
          </p>
          <div className="login-diagram" aria-hidden="true">
            <div>
              <Database size={21} />
              <span>数据与目标</span>
            </div>
            <ArrowRight size={22} />
            <div className="accent">
              <Bot size={23} />
              <span>智能体协作</span>
            </div>
            <ArrowRight size={22} />
            <div>
              <ShieldCheck size={22} />
              <span>自动校验</span>
            </div>
          </div>
          <div className="login-principles">
            <span>独立版本与上下文</span>
            <span>自动评审与有限修订</span>
            <span>预算与权限受控</span>
          </div>
        </div>
        <small className="login-footnote">
          基于自托管 Agno AgentOS · 本地管理工作空间
        </small>
      </section>
      <section className="login-form-side">
        <form className="login-form" onSubmit={submit}>
          <h2>{register ? "创建本地账户" : "登录工作空间"}</h2>
          <p>
            {register
              ? "创建操作员身份，使用独立项目管理任务。"
              : "使用本地账户访问获授权的项目与资产。"}
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
  go,
  openTask,
}: {
  data: Bootstrap;
  project?: Item;
  agents: Item[];
  tasks: Item[];
  assets: Item[];
  workflows: Item[];
  go: (page: Page) => void;
  openTask: (id: string) => void;
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
        title="工作台总览"
        description={`${project?.name || "当前项目"} · 从可复用能力到可追溯任务`}
        actions={
          <button className="primary" onClick={() => go("tasks")}>
            <Plus size={18} />
            进入任务工作台
          </button>
        }
      />
      <div className="overview-hero">
        <div>
          <span className="eyebrow">从目标到交付</span>
          <h2>管理能力，协作执行。</h2>
          <p>
            以智能体为中心，连接数据、流程与自动评审。
            <br />
            每一次执行都保留版本、来源和真实结果。
          </p>
          <div className="actions">
            <button onClick={() => go("agents")}>
              管理智能体
              <ArrowRight size={16} />
            </button>
            <button className="subtle" onClick={() => go("data")}>
              接入数据
            </button>
          </div>
        </div>
        <div className="hero-nodes" aria-hidden="true">
          <div>
            <Database />
            <span>授权数据</span>
          </div>
          <div className="node-connector" />
          <div className="hero-primary">
            <Bot />
            <span>智能体资产</span>
            <small>定义 · 版本 · 实例</small>
          </div>
          <div className="node-connector" />
          <div>
            <ShieldCheck />
            <span>可验证交付</span>
          </div>
        </div>
      </div>
      <div className="metric-grid four">
        <button className="metric" onClick={() => go("agents")}>
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
        </button>
        <button className="metric" onClick={() => go("tasks")}>
          <span>
            进行中的真实任务
            <Activity size={17} />
          </span>
          <strong>{summary.active}</strong>
          <small>不包含配置演练</small>
        </button>
        <button className="metric" onClick={() => go("tasks")}>
          <span>
            成功终态 / 终态样本
            <ShieldCheck size={17} />
          </span>
          <strong>
            {summary.terminal
              ? `${summary.succeeded} / ${summary.terminal}`
              : "尚无样本"}
          </strong>
          <small>当前项目全部真实任务 · 部分 / 失败 / 取消计入终态</small>
        </button>
        <button className="metric" onClick={() => go("operations")}>
          <span>
            全项目可用预算
            <WalletIcon />
          </span>
          <strong>¥ {money(data.budget?.remaining_cny)}</strong>
          <small>历史累计上限 ¥ {data.budget?.limit_cny ?? "—"}</small>
        </button>
      </div>
      <div className="overview-columns">
        <Panel
          title="最近任务"
          actions={
            <button className="text-button" onClick={() => go("tasks")}>
              查看全部
              <ArrowRight size={15} />
            </button>
          }
        >
          {recent.length ? (
            <div className="recent-tasks">
              {recent.map((t) => (
                <button
                  key={t.id}
                  className="recent-row"
                  onClick={() => openTask(t.id)}
                >
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
                  <ChevronRight size={16} />
                </button>
              ))}
            </div>
          ) : (
            <Empty
              title="任务记录从这里开始"
              description="先登记数据，再提交带质量约束的任务。执行记录将自动保存在当前项目。"
              action={
                <button onClick={() => go("data")}>
                  接入第一份数据
                  <ArrowRight size={16} />
                </button>
              }
            />
          )}
        </Panel>
        <div className="overview-side">
          <Panel title="工作空间资源">
            <button className="resource-row" onClick={() => go("data")}>
              <Database size={18} />
              <span>有效数据资产</span>
              <strong>
                {assets.filter((a) => a.status === "active").length}
              </strong>
              <ChevronRight size={15} />
            </button>
            <button className="resource-row" onClick={() => go("workflows")}>
              <Workflow size={18} />
              <span>已发布流程</span>
              <strong>
                {workflows.filter((w) => w.status === "published").length}
              </strong>
              <ChevronRight size={15} />
            </button>
            <button className="resource-row" onClick={() => go("operations")}>
              <Activity size={18} />
              <span>执行服务</span>
              <Badge
                status={data.system.worker_online ? "online" : "offline"}
              />
            </button>
          </Panel>
          <Panel title="能力边界">
            <div className="capability-list">
              {capabilities.map((c) => (
                <div key={c.id}>
                  <span>{c.name || taskTypes[c.id]}</span>
                  <Badge status={c.available ? "ready" : "unsupported"}>
                    {c.available ? "可用" : "待验证"}
                  </Badge>
                </div>
              ))}
            </div>
            <p className="muted small">
              能力由实际工具与模型提供；未验证的模态不会被当作成功处理。
            </p>
          </Panel>
        </div>
      </div>
    </>
  );
}
function WalletIcon() {
  return <span aria-hidden="true">¥</span>;
}
