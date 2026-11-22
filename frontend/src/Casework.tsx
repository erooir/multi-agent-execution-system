import { useEffect, useState } from "react";
import { Activity, ArrowRight, Layers3, Search, Users } from "lucide-react";
import { api, post } from "./api";
import type { Item, Mutation } from "./types";
import { SectionTitle, Empty } from "./ui";
import { TaskForm } from "./Forms";
import SemanticEvidence from "./SemanticEvidence";
import { CaseRoleComparison, caseRoleAgent, RoleFacts } from "./CaseRoles";
import "./case-catalog.css";

export type CaseTab = "catalog" | "roles" | "semantic";

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
    [openingCase, setOpeningCase] = useState<string | null>(null),
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
  const boundCount = catalog.filter(
    (p) => p.installed && p.project_tools_current,
  ).length;
  const grantTotal = catalog.reduce(
    (sum, p) =>
      sum +
      (Array.isArray(p.role_contract?.grants)
        ? p.role_contract.grants.length
        : 0),
    0,
  );
  const needsUpgrade =
    installed && catalog.some((c) => c.project_tools_current === false);
  async function provision(upgrade: boolean, caseId?: string) {
    setBusy(true);
    const params = new URLSearchParams({ project_id: projectId });
    if (caseId) params.set("case_id", caseId);
    try {
      await mutate(
        () =>
          post(
            `/v2/casework/${upgrade ? "upgrade" : "install"}?${params}`,
          ),
        caseId
          ? undefined
          : upgrade
            ? "新版用例岗位与流程已发布，后续任务可使用新版本"
            : "本项目用例岗位与流程已发布",
      );
      refresh((n) => n + 1);
      setError("");
      return true;
    } catch (e) {
      setError((e as Error).message);
      return false;
    } finally {
      setBusy(false);
    }
  }
  async function openCase(item: Item) {
    if (!item.project_tools_current) {
      setOpeningCase(item.id);
      const ready = await provision(true, item.id);
      setOpeningCase(null);
      if (!ready) return;
    }
    setProfile(item);
  }
  return (
    <>
      <SectionTitle
        eyebrow="场景分析"
        title="场景分析"
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
          打开用例时会尝试更新旧版内置流程，历史版本和任务记录会保留。
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
          <div className="cc-hero">
            <h2>用例任务目录</h2>
            <p>
              按业务问题选择分析流程 · {catalog.length} 项真实用例 ·{" "}
              {catalogLoaded
                ? `${boundCount} 项已发布并绑定工具`
                : "正在读取目录"}
            </p>
            <div className="cc-metrics">
              <div>
                <span>用例总数</span>
                <strong>
                  {catalog.length}
                  <em>项</em>
                </strong>
              </div>
              <div>
                <span>业务分类</span>
                <strong>
                  {Object.keys(families).length}
                  <em>类</em>
                </strong>
              </div>
              <div>
                <span>已发布绑定</span>
                <strong>
                  {catalogLoaded ? boundCount : "…"}
                  <em>项</em>
                </strong>
              </div>
              <div>
                <span>岗位工具授权</span>
                <strong>
                  {catalogLoaded ? grantTotal : "…"}
                  <em>项</em>
                </strong>
              </div>
            </div>
          </div>
          <div className="cc-filters">
            {Object.entries({ all: "全部用例", ...families }).map(([k, v]) => (
              <button
                key={k}
                aria-pressed={family === k}
                className={`cc-chip ${family === k ? "on" : ""}`}
                onClick={() => setFamily(k)}
              >
                {v}
                <i>
                  {k === "all"
                    ? catalog.length
                    : catalog.filter((p) => p.id[0] === k).length}
                </i>
              </button>
            ))}
            <label className="cc-search">
              <Search size={15} />
              <input
                type="search"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="编号、用例岗位名称、工具或所需材料"
              />
            </label>
          </div>
          {catalogLoaded && (
            <p className="cc-count" aria-live="polite">
              <b>{matches.length}</b> 项符合当前条件 · 目录共 {catalog.length} 项
            </p>
          )}
          {!catalogLoaded && !error && (
            <p role="status">正在读取项目用例目录…</p>
          )}
          {catalogLoaded && matches.length === 0 && (
            <Empty
              title="没有符合条件的用例"
              description="可切换类别或修改搜索内容。"
            />
          )}
          <div className="cc-grid">
            {matches.map((p) => {
              const rc = p.role_contract;
              const bound = !!p.project_tools_current;
              const grants = Array.isArray(rc?.grants) ? rc.grants.length : 0;
              const shared = Array.isArray(rc?.shared_cases)
                ? rc.shared_cases.length
                : 0;
              const specialists = Array.isArray(p.specialists)
                ? p.specialists
                : [];
              return (
                <article
                  key={p.id}
                  className={`cc-card cc-${String(p.id[0]).toLowerCase()}`}
                >
                  <div className="cc-watermark">{p.id}</div>
                  <div className="cc-kicker">
                    <span>
                      {p.id} · {families[p.id[0]] || "其他"}
                    </span>
                    <span className={`cc-state ${bound ? "" : "warn"}`}>
                      <i />
                      {bound ? "已绑定" : "待发布绑定"}
                    </span>
                  </div>
                  <h3>{p.title}</h3>
                  <ul className="cc-list">
                    {(p.inputs || []).slice(0, 2).map((x: string, i: number) => (
                      <li key={i}>{x}</li>
                    ))}
                  </ul>
                  <div className="cc-rule" />
                  <div className="cc-facts">
                    <span className="cc-fact">
                      <i />
                      {grants} 项工具
                    </span>
                    <span className="cc-fact">
                      <i />
                      {shared > 1 ? `${shared} 例共用` : "专用算法"}
                    </span>
                    <span className="cc-fact">
                      <i />
                      {specialists[1] || "来源审查"}
                    </span>
                  </div>
                  <div className="cc-cta">
                    {canEdit && (
                      <button
                        className="cc-primary"
                        disabled={!p.installed || busy}
                        onClick={() => openCase(p)}
                      >
                        {openingCase === p.id
                          ? "正在更新流程…"
                          : "创建用例任务"}
                        <ArrowRight size={14} />
                      </button>
                    )}
                    <button
                      className="cc-ghost"
                      onClick={() => {
                        setCompareId(p.id);
                        setTab("roles");
                      }}
                    >
                      岗位对照
                    </button>
                  </div>
                  <div className="cc-details">
                    <details>
                      <summary>输入字段与交付契约</summary>
                      {(p.record_fields || []).length > 0 && (
                        <p>{(p.record_fields || []).join("、")}</p>
                      )}
                      <p>交付字段：{(p.outputs || []).join("、")}</p>
                      {rc && (
                        <p className="cc-tech">
                          主算法：<code>{rc.operation}</code>
                        </p>
                      )}
                    </details>
                    {rc && (
                      <details>
                        <summary>职责、共用算法与授权清单</summary>
                        <RoleFacts
                          role={rc}
                          tools={tools}
                          agent={caseRoleAgent(agents, p.id, "analyze")}
                        />
                      </details>
                    )}
                  </div>
                </article>
              );
            })}
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
      ) : null}
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
