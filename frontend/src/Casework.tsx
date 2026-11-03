import { useEffect, useState } from "react";
import { Activity, ArrowRight, Layers3, Users } from "lucide-react";
import { api, post } from "./api";
import type { Item, Mutation } from "./types";
import { Panel, SectionTitle, Empty } from "./ui";
import { TaskForm } from "./Forms";
import SemanticEvidence from "./SemanticEvidence";
import { CaseRoleComparison, caseRoleAgent, RoleFacts } from "./CaseRoles";
import AgentActivity from "./AgentActivity";

export type CaseTab = "catalog" | "roles" | "monitor" | "semantic";

const families: Record<string, string> = {
  B: "事件与历史",
  T: "科技研究",
  G: "地理与时序",
};

export default function Casework({
  projectId,
  canEdit,
  mutate,
  assets,
  agents,
  workflows,
  tasks,
  offline,
  initialTab = "catalog",
  onTask,
  onAgent,
}: {
  projectId: string;
  canEdit: boolean;
  mutate: Mutation;
  assets: Item[];
  agents: Item[];
  workflows: Item[];
  tasks: Item[];
  offline: boolean;
  initialTab?: CaseTab;
  onTask: (id: string) => void;
  onAgent: (id: string) => void;
}) {
  const [catalog, setCatalog] = useState<Item[]>([]),
    [tools, setTools] = useState<Item[]>([]),
    [compareId, setCompareId] = useState("B06"),
    [family, setFamily] = useState("all"),
    [query, setQuery] = useState(""),
    [profile, setProfile] = useState<Item | null>(null),
    [tab, setTab] = useState<CaseTab>(initialTab),
    [catalogLoaded, setCatalogLoaded] = useState(false),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [epoch, refresh] = useState(0);
  useEffect(() => {
    const c = new AbortController(),
      signal = c.signal;
    const scope = "?project_id=" + encodeURIComponent(projectId);
    Promise.all([
      api<Item[]>("/v2/casework/catalog" + scope, { signal }),
      api<Item>("/v2/capabilities" + scope, { signal }),
    ])
      .then(([profiles, capabilities]) => {
        if (!signal.aborted) {
          setCatalog(profiles);
          setTools(capabilities.tools || []);
          setCatalogLoaded(true);
          setError("");
        }
      })
      .catch((e) => {
        if (!signal.aborted) setError(e.message);
      });
    return () => c.abort();
  }, [projectId, epoch]);
  const needle = query.trim().toLowerCase();
  const matches = catalog.filter(
    (p) =>
      (family === "all" || p.id[0] === family) &&
      [
        p.id,
        p.title,
        ...(p.inputs || []),
        ...(p.outputs || []),
        ...(p.role_contract?.grants || []),
        p.role_contract?.operation,
      ]
        .filter(Boolean)
        .join(" ")
        .toLowerCase()
        .includes(needle),
  );
  const installed = catalog.length > 0 && catalog.every((c) => c.installed);
  const needsUpgrade =
    installed && catalog.some((c) => c.project_tools_current === false);
  async function provision(upgrade: boolean) {
    setBusy(true);
    try {
      await mutate(
        () =>
          post(
            "/v2/casework/" +
              (upgrade ? "upgrade" : "install") +
              "?project_id=" +
              encodeURIComponent(projectId),
          ),
        upgrade
          ? "新版用例岗位与流程已发布，后续任务可使用新版本"
          : "本项目用例岗位与流程已发布",
      );
      refresh((n) => n + 1);
      setError("");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <SectionTitle
        eyebrow="用例任务"
        title="用例任务"
        description="按业务问题选择分析流程。资料、专业计算和独立审核都有版本及运行记录。发布会将用例岗位与流程绑定到本项目，不会下载外部内容。"
        actions={
          canEdit &&
          (!installed || needsUpgrade) && (
            <button
              disabled={busy || !catalog.length}
              onClick={() => provision(needsUpgrade)}
            >
              <Users size={16} />
              {needsUpgrade
                ? "发布新版用例岗位与流程"
                : "发布本项目用例岗位与流程"}
            </button>
          )
        }
      />
      {needsUpgrade && (
        <p className="warning">
          本项目已发布旧版流程。升级会发布新版用例岗位和流程，历史版本与原任务保留；存在未发布的编辑时请先处理冲突。
        </p>
      )}
      <div className="segmented">
        <button
          className={tab === "catalog" ? "selected" : ""}
          aria-pressed={tab === "catalog"}
          onClick={() => setTab("catalog")}
        >
          <Layers3 size={16} />
          用例任务目录
        </button>
        <button
          className={tab === "roles" ? "selected" : ""}
          aria-pressed={tab === "roles"}
          onClick={() => setTab("roles")}
        >
          <Users size={16} />
          用例岗位对照
        </button>
        <button
          className={tab === "monitor" ? "selected" : ""}
          aria-pressed={tab === "monitor"}
          onClick={() => setTab("monitor")}
        >
          <Activity size={16} />
          智能体运行监测
        </button>
        {canEdit && !offline && (
          <button
            className={tab === "semantic" ? "selected" : ""}
            aria-pressed={tab === "semantic"}
            onClick={() => setTab("semantic")}
          >
            公开资料语义取证
          </button>
        )}
      </div>
      {error && (
        <p className="warning" role="alert">
          {error}
        </p>
      )}
      {tab === "catalog" ? (
        <>
          <div className="toolbar">
            {Object.entries({ all: "全部用例", ...families }).map(([k, v]) => (
              <button
                key={k}
                aria-pressed={family === k}
                className={family === k ? "primary" : ""}
                onClick={() => setFamily(k)}
              >
                {v}
              </button>
            ))}
          </div>
          <div className="case-query">
            <label>
              查找用例
              <input
                type="search"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="编号、用例岗位名称、工具或所需材料"
              />
            </label>
            {catalogLoaded && (
              <small aria-live="polite">
                {matches.length} 项符合当前条件 · 目录共 {catalog.length} 项
              </small>
            )}
          </div>
          {!catalogLoaded && !error && (
            <p role="status">正在读取项目用例目录…</p>
          )}
          {catalogLoaded && matches.length === 0 && (
            <Empty
              title="没有符合条件的用例"
              description="可切换类别或修改搜索内容。"
            />
          )}
          <div className="case-grid">
            {matches.map((p) => (
              <Panel key={p.id}>
                <small className="muted">
                  {p.id} · {families[p.id[0]]}
                </small>
                <h3>{p.title}</h3>
                <p>{p.inputs.join("；")}</p>
                {p.role_contract && (
                  <div className="case-operation">
                    <small>
                      主算法 <code>{p.role_contract.operation}</code>
                    </small>
                    <small>
                      {p.role_contract.shared_cases.length > 1
                        ? p.role_contract.shared_cases.length +
                          " 个用例共用算法"
                        : "此用例专用算法"}
                      {" · "}
                      {p.role_contract.grants.length} 项用例岗位工具
                      {p.project_tools_current ? " · 已绑定" : " · 待发布绑定"}
                    </small>
                  </div>
                )}
                <details>
                  <summary>输入字段与交付契约</summary>
                  {p.record_fields?.length > 0 && (
                    <p>{p.record_fields.join("、")}</p>
                  )}
                  <p>交付字段：{p.outputs.join("、")}</p>
                </details>
                {p.role_contract && (
                  <details>
                    <summary>职责、共用算法与授权清单</summary>
                    <RoleFacts
                      role={p.role_contract}
                      tools={tools}
                      agent={caseRoleAgent(agents, p.id, "analyze")}
                    />
                  </details>
                )}
                <button
                  className="text-button"
                  onClick={() => {
                    setCompareId(p.id);
                    setTab("roles");
                  }}
                >
                  与其他用例岗位对照
                </button>
                {canEdit && (
                  <button disabled={!p.installed} onClick={() => setProfile(p)}>
                    创建用例任务 <ArrowRight size={14} />
                  </button>
                )}
              </Panel>
            ))}
          </div>
        </>
      ) : tab === "roles" ? (
        <CaseRoleComparison
          catalog={catalog}
          tools={tools}
          agents={agents}
          initial={compareId}
        />
      ) : tab === "semantic" ? (
        <SemanticEvidence
          projectId={projectId}
          assets={assets}
          tasks={tasks}
          offline={offline}
          onTask={onTask}
        />
      ) : (
        <AgentActivity
          projectId={projectId}
          onAgent={onAgent}
          onTask={onTask}
        />
      )}
      {profile && (
        <TaskForm
          projectId={projectId}
          assets={assets}
          workflows={workflows}
          mutate={mutate}
          onClose={() => setProfile(null)}
          initial={{
            task_type: "case_analysis",
            case_id: profile.id,
            name: profile.title,
            goal: "根据授权资料完成" + profile.title + "，列出证据与信息缺口。",
            allow_partial: true,
            deadline_seconds: 1800,
            max_revisions: 1,
          }}
          afterCreate={(t) => {
            setProfile(null);
            onTask(t.id);
          }}
        />
      )}
    </>
  );
}
