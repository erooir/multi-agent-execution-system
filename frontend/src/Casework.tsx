import { useEffect, useState } from "react";
import { Activity, ArrowRight, Layers3, Search, Users } from "lucide-react";
import { api, post } from "./api";
import type { Item, Mutation } from "./types";
import { SectionTitle, Empty } from "./ui";
import { TaskForm, editionDraft } from "./Forms";
import { verifiedCaseSamples } from "./caseSamples";
import type { CaseChoice } from "./CaseDemoSample";
import SemanticEvidence from "./SemanticEvidence";
import { CaseRoleComparison, caseRoleAgent, RoleFacts } from "./CaseRoles";
import "./case-catalog.css";

export type CaseTab = "catalog" | "roles" | "semantic";

const legacyFamilies: Record<string, string> = {
  B: "事件与历史",
  T: "科技研究",
  G: "地理与时序",
};
const editionFamilies: Record<string, string> = { B: "战场情报", T: "科技情报", G: "地理情报" };

export function useCaseCatalog(projectId: string, userId?: string) {
  const [revision, setRevision] = useState(0);
  const [result, setResult] = useState<{
    projectId: string; userId: string; rows?: Item[]; error?: string;
  } | null>(null);
  useEffect(() => {
    if (!projectId || !userId) {
      setResult(null);
      return;
    }
    const controller = new AbortController();
    const { signal } = controller;
    api<Item[]>("/v2/casework/catalog?project_id=" + encodeURIComponent(projectId), { signal })
      .then((rows) => { if (!signal.aborted) setResult({ projectId, userId, rows }); })
      .catch((e: Error) => { if (!signal.aborted) setResult({ projectId, userId, error: e.message }); });
    return () => controller.abort();
  }, [projectId, userId, revision]);
  const current = result?.projectId === projectId && result.userId === userId ? result : null;
  return { rows: current?.rows, error: current?.error, refresh: () => setRevision((n) => n + 1) };
}

function CaseRoleDetails({ role, agents, caseId, tools, onOpen }: {
  role: Item; agents: Item[]; caseId: string; tools: Item[]; onOpen: () => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <details onToggle={(event) => {
      const expanded = event.currentTarget.open;
      setOpen(expanded);
      if (expanded) onOpen();
    }}>
      <summary>职责、共用算法与授权清单</summary>
      {open && <RoleFacts role={role} tools={tools} agent={caseRoleAgent(agents, caseId, "analyze")} />}
    </details>
  );
}

export default function Casework({
  projectId,
  canEdit,
  mutate,
  assets,
  agents,
  workflows,
  tasks,
  directory,
  capabilities,
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
  directory: ReturnType<typeof useCaseCatalog>;
  capabilities?: Item[];
  offline: boolean;
  initialTab?: CaseTab;
  onTask: (id: string) => void;
}) {
  const catalog = directory.rows ?? [];
  const catalogLoaded = directory.rows !== undefined;
  const publishedEdition = Boolean(catalog[0]?.edition);
  const families = publishedEdition ? editionFamilies : legacyFamilies;
  const [tools, setTools] = useState<Item[]>([]),
    [toolsRequested, setToolsRequested] = useState(initialTab === "roles"),
    [toolError, setToolError] = useState(""),
    [compareId, setCompareId] = useState("B06"),
    [family, setFamily] = useState("all"),
    [query, setQuery] = useState(""),
    [draft, setDraft] = useState<CaseChoice | null>(null),
    [tab, setTab] = useState<CaseTab>(initialTab),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [openingCase, setOpeningCase] = useState<string | null>(null);
  useEffect(() => {
    if (!toolsRequested || !directory.rows) return;
    const controller = new AbortController();
    const { signal } = controller;
    api<Item>("/v2/capabilities?project_id=" + encodeURIComponent(projectId), { signal })
      .then((data) => {
        if (!signal.aborted) {
          setTools([...(data.tools || []), ...(data.skills || [])]);
          setToolError("");
        }
      })
      .catch((e) => {
        if (!signal.aborted) setToolError(e.message);
      });
    return () => controller.abort();
  }, [projectId, toolsRequested, directory.rows]);
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
  const needsUpgrade = catalog.some(
    (c) => c.installed && c.project_tools_current === false,
  );
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
      directory.refresh();
      setError("");
      return true;
    } catch (e) {
      setError((e as Error).message);
      return false;
    } finally {
      setBusy(false);
    }
  }
  const samples = verifiedCaseSamples(projectId, tasks, assets);
  async function openCase(item: Item) {
    if (!item.project_tools_current) {
      setOpeningCase(item.id);
      const ready = await provision(true, item.id);
      setOpeningCase(null);
      if (!ready) return;
    }
    setDraft({ id: item.id, title: item.title });
  }
  return (
    <>
      <SectionTitle
        eyebrow="场景分析"
        title="场景分析"
        description="按业务问题选择分析流程。资料、专业计算和独立审核都有版本及运行记录。发布会将用例岗位与流程绑定到本项目，不会下载外部内容。"
        actions={
          canEdit &&
          (needsUpgrade || (!publishedEdition && !installed)) && (
            <button
              disabled={busy || !catalog.length}
              onClick={() => provision(needsUpgrade)}
            >
              <Users size={16} />
              {needsUpgrade
                ? "发布并更新用例岗位与流程"
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
          onClick={() => { setToolsRequested(true); setTab("roles"); }}
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
      {(error || directory.error || toolError) && (
        <p className="warning" role="alert">
          {error || directory.error || toolError}
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
                <span>{publishedEdition ? "随包原件" : "岗位工具授权"}</span>
                <strong>
                  {catalogLoaded ? publishedEdition ? catalog.reduce((n, p) => n + p.inputs.length, 0) : grantTotal : "…"}
                  <em>{publishedEdition ? "份" : "项"}</em>
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
          {!catalogLoaded && !directory.error && (
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
                      {p.edition ? `${p.inputs.length} 份原件` : `${grants} 项工具`}
                    </span>
                    <span className="cc-fact">
                      <i />
                      {p.edition ? "模型调用" : shared > 1 ? `${shared} 例共用` : "专用算法"}
                    </span>
                    <span className="cc-fact">
                      <i />
                      {specialists[1] || "来源审查"}
                    </span>
                  </div>
                  <div className="cc-cta">
                    {canEdit && !p.edition && !offline && samples.has(p.id) && (
                      <button
                        className="cc-ghost"
                        disabled={!p.installed || busy}
                        onClick={() => openCase(p)}
                      >
                        使用已验证样本
                      </button>
                    )}
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
                        setToolsRequested(true);
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
                      <CaseRoleDetails role={rc} tools={tools} agents={agents} caseId={p.id}
                        onOpen={() => setToolsRequested(true)} />
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
      {draft && (
        <TaskForm
          caseProfiles={catalog}
          taskCapabilities={capabilities}
          projectId={projectId}
          assets={assets}
          workflows={workflows}
          mutate={mutate}
          onClose={() => setDraft(null)}
          demoCase={publishedEdition ? undefined : draft}
          initial={publishedEdition ? editionDraft(catalog.find((p) => p.id === draft.id)!) : undefined}
          offline={offline}
          afterCreate={(t) => {
            setDraft(null);
            onTask(t.id);
          }}
        />
      )}
    </>
  );
}
