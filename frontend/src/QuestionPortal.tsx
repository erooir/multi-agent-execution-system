import { useEffect, useState, type ComponentType } from "react";
import { LogOut, Network, Settings2 } from "lucide-react";
import { api, ApiError, post } from "./api";
import { projectLabel } from "./projects";
import type { User } from "./types";
import { Confirm, Empty, ErrorLine } from "./ui";
import QuestionDesk from "./QuestionDesk";
import TaskCaseDesk from "./TaskCaseDesk";
import "./portal.css";

type Project = { id: string; name: string };
type LoginComponent = ComponentType<{
  initialError: string;
  onLogin: (user: User) => void;
}>;

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
  return (
    <div className="question-portal">
      <header className="question-top">
        <div className="question-brand">
          <span className="brand-mark">
            <Network size={21} />
          </span>
          <strong>航智</strong>
          <span>{legacy ? "历史问答" : "任务工作台"}</span>
        </div>
        {projects && projects.length > 0 && (
          <label className="question-project">
            当前项目
            <select
              value={projectId}
              onChange={(e) => {
                setProjectId(e.target.value);
                try {
                  sessionStorage.setItem(
                    "hangzhi-question-project-" + user.id,
                    e.target.value,
                  );
                } catch {}
              }}
            >
              {projects.map((p) => (
                <option key={p.id} value={p.id}>
                  {projectLabel(p, projects)}
                </option>
              ))}
            </select>
          </label>
        )}
        <div className="question-account">
          <a className="desk-link" href={legacy ? "/" : "/questions"}>
            {legacy ? "任务工作台" : "历史问答"}
          </a>
          {user.role !== "requester" && (
            <a className="desk-link" href="/manage">
              <Settings2 size={16} />
              管理工作台
            </a>
          )}
          <button
            aria-label="退出登录"
            title={user.name || user.username}
            onClick={() => setLogout(true)}
          >
            <LogOut size={16} />
            退出
          </button>
        </div>
      </header>
      <main className="question-main">
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
            />
          ) : (
            <TaskCaseDesk
              key={user.id + "/" + projectId}
              projectId={projectId}
              canSubmit={user.role !== "reviewer"}
            />
          )
        ) : projects?.length === 0 ? (
          <Empty
            title="尚未分配项目"
            description="请联系管理员分配项目。项目就绪后即可创建任务并提供所需资料。"
          />
        ) : (
          !error && <p role="status">正在读取可用项目…</p>
        )}
      </main>
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
    </div>
  );
}
