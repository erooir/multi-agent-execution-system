import {
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  Activity,
  ArrowRight,
  BookOpen,
  Boxes,
  ChevronRight,
  Clock,
  Compass,
  Database,
  FlaskConical,
  Folder,
  Gauge,
  Layers3,
  LogOut,
  Menu,
  Network,
  Plus,
  Settings2,
  Sparkles,
  Wallet,
  Workflow,
  X,
  type LucideIcon,
} from "lucide-react";
import { api } from "./api";
import { money } from "./domain";
import type { Item } from "./types";

export type WbTask = {
  id: string;
  title: string;
  status?: string;
  statusLabel?: string;
  createdAt?: string;
  active?: boolean;
  projectId?: string;
};

/* Desk components report their existing list to the shell and accept simple
   commands. This keeps all polling, detail rendering and backend calls inside
   the original components. */
export type WbDeskState = {
  items: WbTask[];
  selected: string;
  loading: boolean;
  error: string;
};
export type WbDeskCommand = {
  seq: number;
  kind: "select" | "compose" | "refresh";
  id?: string;
};

export type HubKind = "expert" | "skill" | "connector";

type WbProject = { id: string; name: string };

const ROLE_LABELS: Record<string, string> = {
  requester: "提问用户",
  admin: "平台管理员",
  operator: "项目操作员",
  reviewer: "只读观察员",
};

function TaskRow({
  task,
  indented = false,
  selected = false,
  onSelect,
}: {
  task: WbTask;
  indented?: boolean;
  selected?: boolean;
  onSelect?: (id: string) => void;
}) {
  return (
    <button
      className={`wb-task-row ${indented ? "is-indented" : ""} ${selected ? "is-selected" : ""}`}
      title={task.title}
      onClick={() => onSelect?.(task.id)}
    >
      <span className="wb-task-row-title">{task.title}</span>
      {task.active ? (
        <Sparkles size={16} className="wb-task-spark" />
      ) : (
        <span className="wb-task-row-time">{relativeTime(task.createdAt)}</span>
      )}
    </button>
  );
}

function relativeTime(value?: string) {
  if (!value) return "";
  const stamp = new Date(value).getTime();
  if (Number.isNaN(stamp)) return "";
  const minutes = Math.floor((Date.now() - stamp) / 60000);
  if (minutes < 1) return "刚刚";
  if (minutes < 60) return `${minutes}分钟前`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}小时前`;
  const days = Math.floor(hours / 24);
  if (days <= 7) return `${days}天前`;
  const d = new Date(stamp);
  return `${d.getMonth() + 1}月${d.getDate()}日`;
}

export type WbShellProps = {
  user: { name?: string; username: string; role: string };
  projects: WbProject[];
  projectId: string;
  projectLabel?: (project: WbProject, all: WbProject[]) => string;
  onProjectChange: (id: string) => void;
  title: string;
  tasks?: WbTask[];
  allTasks?: WbTask[];
  selectedTaskId?: string;
  onSelectTask?: (id: string) => void;
  onNewTask?: () => void;
  onRefreshTasks?: () => void;
  tasksLoading?: boolean;
  tasksError?: string;
  activePage?: string;
  onNavigate?: (page: string) => void;
  onOpenWorkdesk?: () => void;
  onOpenCenter?: () => void;
  onOpenLibrary?: () => void;
  activeNav?: string;
  topbarContent?: ReactNode;
  budget?: Item | null;
  system?: Item | null;
  onLogout: () => void;
  headerRight?: ReactNode;
  footer?: ReactNode;
  children: ReactNode;
};

export default function WbShell({
  user,
  projects,
  projectId,
  projectLabel,
  onProjectChange,
  title,
  tasks = [],
  allTasks,
  selectedTaskId = "",
  onSelectTask,
  onNewTask,
  onRefreshTasks,
  tasksLoading = false,
  tasksError = "",
  onNavigate,
  onOpenWorkdesk,
  onOpenCenter,
  onOpenLibrary,
  activeNav = "",
  topbarContent,
  budget = null,
  system = null,
  onLogout,
  headerRight,
  footer,
  children,
}: WbShellProps) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const recentTasksId = useId();
  const [recentCollapsed, setRecentCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [budgetData, setBudgetData] = useState<Item | null>(budget);
  const [systemData, setSystemData] = useState<Item | null>(system);
  const loadedRef = useRef(false);

  useEffect(() => {
    if (budget) setBudgetData(budget);
  }, [budget]);
  useEffect(() => {
    if (system) setSystemData(system);
  }, [system]);
  useEffect(() => {
    if (!menuOpen && !settingsOpen) return;
    if (loadedRef.current) return;
    loadedRef.current = true;
    if (!budget)
      void api<Item>("/v2/budget")
        .then(setBudgetData)
        .catch(() => {});
    if (!system)
      void api<Item>("/v2/bootstrap?scope=current")
        .then((value) => setSystemData(value?.system || null))
        .catch(() => {});
  }, [menuOpen, settingsOpen, budget, system]);

  const label = (project: WbProject) =>
    projectLabel ? projectLabel(project, projects) : project.name;
  const currentProject = projects.find((project) => project.id === projectId);
  const displayName = user.name || user.username || "用户";
  const groupedTasks = allTasks || tasks;
  const recent = tasks.slice(0, 6);
  const groups = useMemo(
    () =>
      [...projects].sort((a, b) => Number(b.id === projectId) - Number(a.id === projectId)).map((project) => ({
        project,
        rows: groupedTasks
          .filter((t) =>
            t.projectId ? t.projectId === project.id : project.id === projectId,
          )
          .slice(0, 3),
      })),
    [projects, groupedTasks, projectId],
  );

  const navigate = (page: string) => {
    setMenuOpen(false);
    setSettingsOpen(false);
    setMobileOpen(false);
    if (onNavigate) onNavigate(page);
    else window.location.href = "/manage?view=" + encodeURIComponent(page);
  };
  const openWorkdesk = () => {
    setMenuOpen(false);
    setMobileOpen(false);
    if (onOpenWorkdesk) onOpenWorkdesk();
    else window.location.href = "/manage?view=workdesk";
  };
  const openCenter = () => {
    setMenuOpen(false);
    setMobileOpen(false);
    if (onOpenCenter) onOpenCenter();
    else window.location.href = "/manage?view=hub";
  };
  const openLibrary = () => {
    setMenuOpen(false);
    setMobileOpen(false);
    if (onOpenLibrary) onOpenLibrary();
    else window.location.href = "/manage?view=library";
  };
  const selectTask = (id: string) => {
    setMobileOpen(false);
    onSelectTask?.(id);
  };

  const spent = Number(budgetData?.spent_cny);
  const limit = Number(budgetData?.limit_cny);
  const remaining = Number(budgetData?.remaining_cny);
  const budgetPercent =
    Number.isFinite(spent) && Number.isFinite(limit) && limit > 0
      ? Math.min(100, Math.max(0, (spent / limit) * 100))
      : null;
  const isManage = window.location.pathname.replace(/\/$/, "") === "/manage";

  return (
    <div className="wb-app">
      <a href="#main-content" className="skip-link">
        跳到主要内容
      </a>
      {mobileOpen && (
        <button
          className="wb-sidebar-backdrop"
          aria-label="关闭导航"
          onClick={() => setMobileOpen(false)}
        />
      )}
      {menuOpen && (
        <div
          className="wb-popover-backdrop"
          onClick={() => setMenuOpen(false)}
        />
      )}

      <aside className={`wb-sidebar ${mobileOpen ? "is-mobile-open" : ""}`}>
        <div className="wb-sidebar-head">
          <div className="wb-logo">
            航智 <small>2.0.0</small>
          </div>
        </div>

        <div className="wb-sidebar-fixed">
          {currentProject && (
            <div className="wb-current-workspace" title={label(currentProject)}>
              <Folder size={16} /><span>{label(currentProject)}</span>
            </div>
          )}
          <nav className="wb-quick-nav" aria-label="主导航">
            <button
              className="wb-nav-row"
              disabled={!onNewTask}
              onClick={() => {
                setMobileOpen(false);
                onNewTask?.();
              }}
            >
              <span className="wb-nav-icon is-circle">
                <Plus size={16} />
              </span>
              新建任务
            </button>
            {user.role !== "requester" && <><button
              className={`wb-nav-row ${activeNav === "workdesk" ? "is-active" : ""}`}
              onClick={openWorkdesk}
            >
              <span className="wb-nav-icon">
                <Gauge size={19} />
              </span>
              工作台·任务中心
            </button>
            <button
              className={`wb-nav-row ${activeNav === "hub" ? "is-active" : ""}`}
              onClick={openCenter}
            >
              <span className="wb-nav-icon">
                <Network size={19} />
              </span>
              智能体·技能·连接器
            </button>
            <button
              className={`wb-nav-row ${activeNav === "library" ? "is-active" : ""}`}
              onClick={openLibrary}
            >
              <span className="wb-nav-icon">
                <BookOpen size={19} />
              </span>
              知识库
            </button>
            <button
              className="wb-nav-row"
              onClick={() => navigate("improvements")}
            >
              <span className="wb-nav-icon">
                <FlaskConical size={19} />
              </span>
              效果评测
            </button>
            <button
              className="wb-nav-row"
              onClick={() => navigate("casework")}
            >
              <span className="wb-nav-icon">
                <Boxes size={19} />
              </span>
              场景分析
            </button></>}
          </nav>
        </div>

        <div className="wb-sidebar-scroll" aria-label="最近任务与工作空间">
          <div className="wb-side-section">
            <button
              className="wb-side-toggle"
              type="button"
              aria-expanded={!recentCollapsed}
              aria-controls={recentTasksId}
              onClick={() => setRecentCollapsed((collapsed) => !collapsed)}
            >
              <span>最近任务</span>
              <ChevronRight size={15} aria-hidden="true" />
            </button>
            <div id={recentTasksId} hidden={recentCollapsed}>
              {tasksError && (
                <p className="wb-task-error">
                  {tasksError}
                  {onRefreshTasks && (
                    <button onClick={onRefreshTasks}>重新连接</button>
                  )}
                </p>
              )}
              {tasksLoading && !tasks.length ? (
                <p className="wb-task-empty" role="status">正在读取任务记录…</p>
              ) : recent.length ? (
                <div className="wb-task-rows">
                  {recent.map((task) => (
                    <TaskRow
                      key={task.id}
                      task={task}
                      selected={task.id === selectedTaskId}
                      onSelect={selectTask}
                    />
                  ))}
                </div>
              ) : (
                <p className="wb-task-empty">还没有任务记录</p>
              )}
            </div>
          </div>

          <div className="wb-side-section">
            <div className="wb-side-label">工作空间</div>
            {groups.length ? (
              groups.map(({ project, rows }) => (
                <div className="wb-group-block" key={project.id}>
                  <button
                    className={`wb-group-row ${project.id === projectId ? "is-current" : ""}`}
                    title={label(project)}
                    data-project-id={project.id}
                    aria-current={project.id === projectId ? "true" : undefined}
                    onClick={() => onProjectChange(project.id)}
                  >
                    <Folder size={18} />
                    <span>{label(project)}</span>
                    {project.id === projectId && <small className="wb-current-project">当前</small>}
                  </button>
                  {rows.length > 0 && (
                    <div className="wb-task-rows">
                      {rows.map((task) => (
                        <TaskRow
                          key={task.id}
                          task={task}
                          indented
                          selected={task.id === selectedTaskId}
                          onSelect={selectTask}
                        />
                      ))}
                    </div>
                  )}
                </div>
              ))
            ) : (
              <p className="wb-task-empty">暂无分组</p>
            )}
          </div>

        </div>

        <div className="wb-sidebar-bottom">
          <button
            className={`wb-user-chip ${menuOpen ? "is-open" : ""}`}
            aria-expanded={menuOpen}
            onClick={() => setMenuOpen(!menuOpen)}
          >
            <span className="wb-avatar">{displayName.slice(0, 1)}</span>
            <span className="wb-user-text">
              <strong>{displayName}</strong>
              <small>{ROLE_LABELS[user.role] || user.role}</small>
            </span>
            <ChevronRight size={16} />
          </button>
        </div>
      </aside>

      {menuOpen && (
        <div className="wb-menu" role="menu">
          <div className="wb-menu-head">
            <span className="wb-avatar">{displayName.slice(0, 1)}</span>
            <div>
              <strong>{displayName}</strong>
              <small>
                @{user.username} · {ROLE_LABELS[user.role] || user.role}
              </small>
            </div>
          </div>
          <button
            className="wb-menu-budget"
            onClick={() => {
              setMenuOpen(false);
              setSettingsOpen(true);
            }}
          >
            <span className="wb-menu-budget-icon">
              <Wallet size={16} />
            </span>
            <span className="wb-menu-budget-text">
              <small>可用预算 · 点击查看明细</small>
              <strong>
                {budgetData?.available === false
                  ? "账本不可用"
                  : Number.isFinite(remaining)
                    ? `¥ ${money(remaining)}`
                    : "读取中…"}
              </strong>
            </span>
            <ChevronRight size={16} />
          </button>
          {budgetPercent !== null && (
            <div className="wb-menu-progress">
              <i style={{ width: `${budgetPercent}%` }} />
            </div>
          )}
          <button
            className="wb-menu-row"
            onClick={() => {
              setMenuOpen(false);
              setSettingsOpen(true);
            }}
          >
            <Settings2 size={16} />
            <span>设置</span>
            <ChevronRight size={14} />
          </button>
          {!isManage && user.role !== "requester" && (
            <>
              <div className="wb-menu-sep" />
              <button className="wb-menu-row" onClick={openWorkdesk}>
                <Activity size={16} />
                <span>管理工作台</span>
                <ArrowRight size={14} />
              </button>
            </>
          )}
          <div className="wb-menu-sep" />
          <button
            className="wb-menu-row is-danger"
            onClick={() => {
              setMenuOpen(false);
              onLogout();
            }}
          >
            <LogOut size={16} />
            <span>退出登录</span>
          </button>
        </div>
      )}

      <div className="wb-main">
        <header className="wb-topbar">
          <div className="wb-topbar-left">
            <button
              className="wb-mobile-toggle"
              aria-label="打开导航"
              onClick={() => setMobileOpen(true)}
            >
              <Menu size={20} />
            </button>
            {topbarContent || <strong>{title}</strong>}
          </div>
          <div className="wb-top-actions">{headerRight}</div>
        </header>
        <main id="main-content" className="wb-main-content" tabIndex={-1}>
          {children}
        </main>
        {footer && <footer className="wb-footer">{footer}</footer>}
      </div>

      {settingsOpen && (
        <div className="wb-drawer-wrap" role="presentation">
          <div
            className="wb-drawer"
            role="dialog"
            aria-modal="true"
            aria-label="设置"
          >
            <div className="wb-drawer-header">
              <Settings2 size={19} />
              <h2>设置</h2>
              <button
                className="wb-panel-close"
                aria-label="关闭设置"
                onClick={() => setSettingsOpen(false)}
              >
                <X size={18} />
              </button>
            </div>
            <div className="wb-drawer-body">
              <div className="wb-settings-group">
                <h3>账户</h3>
                <div className="wb-settings-card">
                  <div className="wb-settings-row">
                    <span>显示名称</span>
                    <strong>{displayName}</strong>
                  </div>
                  <div className="wb-settings-row">
                    <span>登录账号</span>
                    <strong>{user.username}</strong>
                  </div>
                  <div className="wb-settings-row">
                    <span>身份角色</span>
                    <strong>{ROLE_LABELS[user.role] || user.role}</strong>
                  </div>
                </div>
              </div>

              <div className="wb-settings-group">
                <h3>预算与用量</h3>
                <div className="wb-budget-hero">
                  <small>可用预算（人民币）</small>
                  <strong>
                    {budgetData?.available === false
                      ? "账本不可用"
                      : `¥ ${money(budgetData?.remaining_cny)}`}
                  </strong>
                  <div className="wb-budget-bar">
                    <i style={{ width: `${budgetPercent ?? 0}%` }} />
                  </div>
                </div>
                <div className="wb-settings-card">
                  <div className="wb-settings-row">
                    <span>已花费</span>
                    <strong>¥ {money(budgetData?.spent_cny)}</strong>
                  </div>
                  <div className="wb-settings-row">
                    <span>已预留</span>
                    <strong>¥ {money(budgetData?.reserved_cny)}</strong>
                  </div>
                  <div className="wb-settings-row">
                    <span>本机领取上限</span>
                    <strong>¥ {money(budgetData?.limit_cny)}</strong>
                  </div>
                  <div className="wb-settings-row">
                    <span>项目历史累计上限</span>
                    <strong>¥ {money(budgetData?.project_cap_cny)}</strong>
                  </div>
                  <div className="wb-settings-row">
                    <span>请求 / 未确认</span>
                    <strong>
                      {budgetData?.request_count ?? "—"} /{" "}
                      {budgetData?.uncertain_count ?? "—"}
                    </strong>
                  </div>
                </div>
                <p className="wb-settings-note">
                  {budgetData?.pricing_note || "服务端未返回价格说明"}
                </p>
              </div>

              <div className="wb-settings-group">
                <h3>运行状态</h3>
                <div className="wb-settings-card">
                  <div className="wb-settings-row">
                    <span>执行策略</span>
                    <strong className={systemData?.offline ? "" : "is-good"}>
                      {systemData
                        ? systemData.offline
                          ? "离线部署"
                          : "允许模型请求"
                        : "读取中…"}
                    </strong>
                  </div>
                  <div className="wb-settings-row">
                    <span>模型密钥</span>
                    <strong
                      className={
                        systemData?.key_configured === false
                          ? "is-bad"
                          : "is-good"
                      }
                    >
                      {systemData
                        ? systemData.key_configured
                          ? "已配置"
                          : "未配置"
                        : "—"}
                    </strong>
                  </div>
                  <div className="wb-settings-row">
                    <span>执行服务</span>
                    <strong
                      className={
                        systemData?.worker_online ? "is-good" : "is-bad"
                      }
                    >
                      {systemData
                        ? systemData.worker_online
                          ? "在线"
                          : "离线"
                        : "—"}
                    </strong>
                  </div>
                  <div className="wb-settings-row">
                    <span>当前模型</span>
                    <strong>{systemData?.model || "—"}</strong>
                  </div>
                  <div className="wb-settings-row">
                    <span>业务存储</span>
                    <strong>{systemData?.database || "—"}</strong>
                  </div>
                </div>
              </div>
            </div>
            <div className="wb-drawer-footer">
              <button onClick={() => navigate("operations")}>
                打开完整设置页 <ArrowRight size={15} />
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
