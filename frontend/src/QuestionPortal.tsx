import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ComponentType,
} from "react";
import { RefreshCw, Settings2 } from "lucide-react";
import { api, ApiError, post } from "./api";
import { projectLabel } from "./projects";
import type { User } from "./types";
import { Confirm, Empty, ErrorLine } from "./ui";
import QuestionDesk from "./QuestionDesk";
import TaskCaseDesk from "./TaskCaseDesk";
import WbShell, { type WbDeskCommand, type WbDeskState } from "./WbShell";
import "./portal.css";

type Project = { id: string; name: string };
type LoginComponent = ComponentType<{
  initialError: string;
  onLogin: (user: User) => void;
}>;

const EMPTY_DESK: WbDeskState = {
  items: [],
  selected: "",
  loading: true,
  error: "",
};

export default function QuestionPortal({
  Login,
  legacy = false,
}: {
  Login: LoginComponent;
  legacy?: boolean;
}) {
  const [user, setUser] = useState<User | null>(null);
  const [checking, setChecking] = useState(true);
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [projectId, setProjectId] = useState("");
  const [error, setError] = useState("");
  const [logout, setLogout] = useState(false);
  const [retry, setRetry] = useState(0);
  const [desk, setDesk] = useState<WbDeskState>(EMPTY_DESK);
  const [command, setCommand] = useState<WbDeskCommand | null>(null);
  const commandSeq = useRef(0);
  const onDeskState = useCallback((state: WbDeskState) => setDesk(state), []);
  const sendCommand = useCallback(
    (kind: WbDeskCommand["kind"], id?: string) => {
      commandSeq.current += 1;
      setCommand({ seq: commandSeq.current, kind, id });
    },
    [],
  );

  useEffect(() => {
    const c = new AbortController();
    api<User>("/auth/me", { signal: c.signal })
      .then((u) => {
        if (!c.signal.aborted) setUser(u);
      })
      .catch((e) => {
        if (!c.signal.aborted && (!(e instanceof ApiError) || e.status !== 401))
          setError(e.message);
      })
      .finally(() => {
        if (!c.signal.aborted) setChecking(false);
      });
    return () => c.abort();
  }, []);
  useEffect(() => {
    if (!user) return;
    const selectionKey = "hangzhi-question-project-" + user.id;
    let previous =
      new URLSearchParams(window.location.search).get("project") || "";
    try {
      previous ||= sessionStorage.getItem(selectionKey) || "";
    } catch {}
    const c = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const load = async () => {
      try {
        const rows = await api<Project[]>("/v2/question-projects", {
          signal: c.signal,
        });
        if (!c.signal.aborted) {
          setProjects(rows);
          setProjectId(
            (id) =>
              rows.find((p) => p.id === (id || previous))?.id ||
              rows[0]?.id ||
              "",
          );
          setError("");
        }
      } catch (e) {
        if (!c.signal.aborted) {
          setProjects(null);
          setProjectId("");
          setError((e as Error).message);
          if (e instanceof ApiError && e.status === 401) setUser(null);
        }
      } finally {
        if (!c.signal.aborted) timer = setTimeout(load, 15000);
      }
    };
    void load();
    return () => {
      c.abort();
      clearTimeout(timer);
    };
  }, [user?.id, retry]);
  useEffect(() => {
    setDesk(EMPTY_DESK);
    setCommand(null);
  }, [projectId, legacy]);

  if (checking)
    return (
      <div className="full-loading" role="status">
        正在连接航智…
      </div>
    );
  if (!user)
    return (
      <Login
        initialError={error}
        onLogin={(u) => {
          setProjects(null);
          setProjectId("");
          setError("");
          setUser(u);
        }}
      />
    );

  const changeProject = (id: string) => {
    setProjectId(id);
    setDesk(EMPTY_DESK);
    try {
      sessionStorage.setItem("hangzhi-question-project-" + user.id, id);
    } catch {}
  };
  const headerRight = (
    <>
      <a className="desk-link" href={legacy ? "/" : "/questions"}>
        {legacy ? "任务工作台" : "历史问答"}
      </a>
      {user.role !== "requester" && (
        <a className="desk-link" href="/manage">
          <Settings2 size={15} />
          管理工作台
        </a>
      )}
      <button
        className="icon-button"
        aria-label="刷新记录"
        title="刷新任务记录"
        onClick={() => sendCommand("refresh")}
      >
        <RefreshCw size={17} />
      </button>
    </>
  );

  return (
    <WbShell
      user={user}
      projects={projects || []}
      projectId={projectId}
      projectLabel={projectLabel}
      onProjectChange={changeProject}
      title={legacy ? "历史问答" : "任务工作台"}
      tasks={desk.items}
      selectedTaskId={desk.selected}
      onSelectTask={(id) => sendCommand("select", id)}
      onNewTask={() => {
        if (legacy) window.location.href = "/";
        else sendCommand("compose");
      }}
      onRefreshTasks={() => sendCommand("refresh")}
      tasksLoading={desk.loading}
      tasksError={desk.error || error}
      onLogout={() => setLogout(true)}
      headerRight={headerRight}
      footer={
        <>
          <span>航智 · AI 工作空间</span>
          <span>持久任务 · 独立版本 · 可追溯交付</span>
        </>
      }
    >
      {error && (
        <ErrorLine>
          {error}{" "}
          <button onClick={() => setRetry((n) => n + 1)}>重新连接</button>
        </ErrorLine>
      )}
      {projectId && projects?.some((p) => p.id === projectId) ? (
        legacy ? (
          <QuestionDesk
            key={user.id + "/" + projectId}
            projectId={projectId}
            canSubmit={user.role !== "reviewer"}
            onState={onDeskState}
            command={command}
          />
        ) : (
          <TaskCaseDesk
            key={user.id + "/" + projectId}
            projectId={projectId}
            canSubmit={user.role !== "reviewer"}
            onState={onDeskState}
            command={command}
            chromeInSidebar
          />
        )
      ) : projects?.length === 0 ? (
        <Empty
          title="尚未分配项目"
          description="请联系管理员分配项目。项目就绪后即可创建任务，平台会根据任务从上游接入所需资料。"
        />
      ) : (
        !error && <p role="status">正在读取可用项目…</p>
      )}
      {logout && (
        <Confirm
          title="退出登录"
          description="已提交的任务会保留，后台分析可继续执行。"
          onClose={() => setLogout(false)}
          onConfirm={async () => {
            await post("/auth/logout", {});
            setUser(null);
            setProjectId("");
            setProjects(null);
            setLogout(false);
            setError("");
          }}
        />
      )}
    </WbShell>
  );
}
