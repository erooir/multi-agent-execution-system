import { useEffect, useState } from "react";
import { Activity, ArrowRight, Layers3, Users } from "lucide-react";
import { api, post } from "./api";
import type { Item, Mutation } from "./types";
import { Panel, SectionTitle, Badge, Empty } from "./ui";
import { TaskForm } from "./Forms";
import SemanticEvidence from "./SemanticEvidence";
import { CaseRoleComparison, RoleFacts } from "./CaseRoles";

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
  workflows,
  tasks,
  offline,
  onTask,
  onAgent,
}: {
  projectId: string;
  canEdit: boolean;
  mutate: Mutation;
  assets: Item[];
  workflows: Item[];
  tasks: Item[];
  offline: boolean;
  onTask: (id: string) => void;
  onAgent: (id: string) => void;
}) {
  const [catalog, setCatalog] = useState<Item[]>([]),
    [tools, setTools] = useState<Item[]>([]),
    [compareId, setCompareId] = useState("B06"),
    [family, setFamily] = useState("B"),
    [profile, setProfile] = useState<Item | null>(null),
    [activity, setActivity] = useState<Item[]>([]),
    [tab, setTab] = useState("catalog"),
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
        }
      })
      .catch((e) => {
        if (!signal.aborted) setError(e.message);
      });
    return () => c.abort();
  }, [projectId, epoch]);
  useEffect(() => {
    if (tab !== "monitor") return;
    const c = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function tick() {
      try {
        const r = await api(
          "/v2/agents/activity/summary?project_id=" +
            encodeURIComponent(projectId),
          { signal: c.signal },
        );
        if (!c.signal.aborted) {
          setActivity(r.agents);
          setError("");
        }
      } catch (e) {
        if (!c.signal.aborted) setError((e as Error).message);
      } finally {
        if (!c.signal.aborted) timer = setTimeout(tick, 10000);
      }
    }
    void tick();
    return () => {
      c.abort();
      clearTimeout(timer);
    };
  }, [tab, projectId]);
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
        upgrade ? "岗位授权已升级，后续任务可使用新版本" : "协作组已安装",
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
        eyebrow="协作任务"
        title="用例研判"
        description="按业务问题选择分析流程。资料、专业计算和独立审核都有版本及运行记录。"
        actions={
          canEdit &&
          (!installed || needsUpgrade) && (
            <button
              disabled={busy || !catalog.length}
              onClick={() => provision(needsUpgrade)}
            >
              <Users size={16} />
              {needsUpgrade ? "升级岗位工具授权" : "安装项目协作组"}
            </button>
          )
        }
      />
      {needsUpgrade && (
        <p className="warning">
          本项目已安装旧版流程。升级会发布新版岗位和流程，历史版本与原任务保留；存在未发布的编辑时请先处理冲突。
        </p>
      )}
      <div className="segmented">
        <button
          className={tab === "catalog" ? "selected" : ""}
          onClick={() => setTab("catalog")}
        >
          <Layers3 size={16} />
          任务目录
        </button>
        <button
          className={tab === "roles" ? "selected" : ""}
          onClick={() => setTab("roles")}
        >
          <Users size={16} />
          岗位对照
        </button>
        <button
          className={tab === "monitor" ? "selected" : ""}
          onClick={() => setTab("monitor")}
        >
          <Activity size={16} />
          智能体运行监测
        </button>
        {canEdit && !offline && (
          <button
            className={tab === "semantic" ? "selected" : ""}
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
            {Object.entries(families).map(([k, v]) => (
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
          <div className="case-grid">
            {catalog
              .filter((p) => p.id[0] === family)
              .map((p) => (
                <Panel key={p.id}>
                  <small className="muted">
                    {p.id} · {families[family]}
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
                            " 个岗位共用实现"
                          : "此用例专用实现"}
                        {" · "}
                        {p.role_contract.grants.length} 项岗位工具
                        {p.project_tools_current
                          ? " · 已绑定"
                          : " · 待发布绑定"}
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
                      <RoleFacts role={p.role_contract} tools={tools} />
                    </details>
                  )}
                  <button
                    className="text-button"
                    onClick={() => {
                      setCompareId(p.id);
                      setTab("roles");
                    }}
                  >
                    与其他岗位对照
                  </button>
                  {canEdit && (
                    <button
                      disabled={!p.installed}
                      onClick={() => setProfile(p)}
                    >
                      创建分析任务 <ArrowRight size={14} />
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
        <Panel title="近7天节点尝试及当前运行节点">
          <p className="muted">
            按智能体聚合真实节点。耗时只统计有起止时间的尝试，未运行不显示推算值。
          </p>
          {!activity.length ? (
            <Empty title="尚无可见智能体" />
          ) : (
            <div className="table-scroll">
              <table className="result-table">
                <thead>
                  <tr>
                    <th>智能体与版本</th>
                    <th>尝试 / 运行</th>
                    <th>失败 / 修订</th>
                    <th>平均耗时</th>
                    <th>最近任务</th>
                  </tr>
                </thead>
                <tbody>
                  {activity.map((a) => (
                    <tr key={a.agent_id}>
                      <td>
                        <button onClick={() => onAgent(a.agent_id)}>
                          {a.name} · v{a.current_version}
                        </button>{" "}
                        <Badge status={a.status} />
                      </td>
                      <td>
                        {a.attempts} / {a.running}
                      </td>
                      <td>
                        {a.failed} / {a.revisions}
                      </td>
                      <td>
                        {a.mean_seconds === null ? "—" : a.mean_seconds + " 秒"}
                      </td>
                      <td>
                        {a.last ? (
                          <>
                            <button onClick={() => onTask(a.last.task_id)}>
                              {a.last.node_id} · {a.last.error || a.last.status}
                            </button>
                            <small> 审核未过 {a.review_rejections} 次</small>
                          </>
                        ) : (
                          "—"
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>
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
