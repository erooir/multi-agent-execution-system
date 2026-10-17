import { useEffect, useRef, useState } from "react";
import {
  Plus,
  Search,
  Bot,
  Layers3,
  Archive,
  Pencil,
  ArrowUpRight,
  FileUp,
  Workflow,
} from "lucide-react";
import { api, idPath, post, remove, readAssetContent } from "./api";
import { taskTypes, time } from "./domain";
import type { Item, Mutation, Notify } from "./types";
import {
  Badge,
  Confirm,
  Empty,
  JsonView,
  Modal,
  Panel,
  SectionTitle,
  Tabs,
} from "./ui";
import { AgentForm, AssetForm, WorkflowForm } from "./Forms";
import Graph from "./Graph";
type Props = {
  projectId: string;
  canEdit: boolean;
  mutate: Mutation;
  notify: Notify;
};
export function AgentsPage({
  projectId,
  agents,
  capabilities,
  canEdit,
  mutate,
  notify,
  onTask,
  initialId,
}: Props & {
  agents: Item[];
  capabilities: Item[];
  onTask: (id: string) => void;
  initialId?: string | null;
}) {
  const [search, setSearch] = useState(""),
    [purpose, setPurpose] = useState("all"),
    [structure, setStructure] = useState("all"),
    [edit, setEdit] = useState<Item | null | undefined>(),
    [selected, setSelected] = useState<Item | null>(null),
    [versions, setVersions] = useState<Item[]>([]),
    [instances, setInstances] = useState<Item[]>([]),
    [tab, setTab] = useState("definition"),
    [detailError, setDetailError] = useState(""),
    [action, setAction] = useState<{ item: Item; type: string } | null>(null);
  const fresh = agents.find((a) => a.id === selected?.id) || selected;
  useEffect(() => {
    if (initialId) setSelected(agents.find((a) => a.id === initialId) || null);
  }, [initialId]);
  useEffect(() => {
    if (!fresh) return;
    let active = true;
    setDetailError("");
    Promise.all([
      api(idPath("agents", fresh.id) + "/versions"),
      api(idPath("agents", fresh.id) + "/instances"),
    ])
      .then(([v, i]) => {
        if (active) {
          setVersions(v);
          setInstances(i);
        }
      })
      .catch((e) => active && setDetailError(e.message));
    return () => {
      active = false;
    };
  }, [fresh?.id, fresh?.current_version, fresh?.updated_at]);
  const list = agents.filter(
    (a) =>
      a.status !== "destroyed" &&
      (purpose === "all" || a.purpose === purpose) &&
      (structure === "all" || a.structure === structure) &&
      `${a.name} ${a.description}`.toLowerCase().includes(search.toLowerCase()),
  );
  const actionName: Record<string, string> = {
    publish: "发布新版本",
    disable: "停用",
    enable: "重新启用",
    retire: "退役",
    destroy: "销毁",
  };
  return (
    <>
      <SectionTitle
        eyebrow="AGENT REGISTRY"
        title="智能体资产"
        description="维护可复用的能力，区分定义、发布版本与每次任务的独立实例。"
        actions={
          canEdit && (
            <button className="primary" onClick={() => setEdit(null)}>
              <Plus size={18} />
              创建智能体
            </button>
          )
        }
      />
      <div className="toolbar">
        <div className="search">
          <Search size={17} />
          <input
            aria-label="搜索智能体"
            placeholder="搜索名称或职责"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <select
          aria-label="用途分类"
          value={purpose}
          onChange={(e) => setPurpose(e.target.value)}
        >
          <option value="all">全部用途</option>
          <option value="general">通用智能体</option>
          <option value="specialized">任务专用智能体</option>
        </select>
        <select
          aria-label="结构分类"
          value={structure}
          onChange={(e) => setStructure(e.target.value)}
        >
          <option value="all">全部结构</option>
          <option value="atomic">元智能体</option>
          <option value="composite">复合智能体</option>
        </select>
        <span className="muted">{list.length} 个定义</span>
      </div>
      {list.length ? (
        <div className="asset-grid">
          {list.map((a) => (
            <button
              className="asset-card"
              key={a.id}
              onClick={() => {
                setSelected(a);
                setTab("definition");
              }}
            >
              <div className="row-between">
                <span className="asset-icon">
                  {a.structure === "composite" ? (
                    <Layers3 size={24} />
                  ) : (
                    <Bot size={24} />
                  )}
                </span>
                <Badge status={a.status} />
              </div>
              <h3>{a.name}</h3>
              <p>{a.description || "尚未填写职责说明"}</p>
              <div className="tags">
                <span>{a.purpose === "general" ? "通用" : "任务专用"}</span>
                <span>
                  {a.structure === "atomic" ? "元智能体" : "复合智能体"}
                </span>
              </div>
              <div className="card-footer">
                <span>
                  {(a.capabilities || [])
                    .map((x: string) => taskTypes[x] || x)
                    .join(" / ")}
                </span>
                <strong>
                  {a.current_version ? `v${a.current_version}` : "未发布"}
                </strong>
              </div>
            </button>
          ))}
        </div>
      ) : (
        <Empty
          title="没有符合条件的智能体"
          description="创建定义、声明能力并发布版本，让后续任务复用经过验证的配置。"
          action={
            canEdit && (
              <button onClick={() => setEdit(null)}>
                <Plus size={16} />
                创建智能体
              </button>
            )
          }
        />
      )}
      {edit !== undefined && (
        <AgentForm
          projectId={projectId}
          item={edit || undefined}
          agents={agents}
          capabilities={capabilities}
          onClose={() => setEdit(undefined)}
          mutate={mutate}
        />
      )}
      {fresh && (
        <Modal wide title={fresh.name} onClose={() => setSelected(null)}>
          <div className="modal-body">
            <div className="row-between">
              <Badge status={fresh.status} />
              <span className="mono muted">{fresh.id}</span>
            </div>
            <Tabs
              value={tab}
              onChange={setTab}
              items={[
                { id: "definition", label: "定义与生命周期" },
                { id: "versions", label: `不可变版本 · ${versions.length}` },
                { id: "instances", label: `运行实例 · ${instances.length}` },
              ]}
            />
            {detailError && (
              <p role="alert" className="error-line">
                {detailError}
              </p>
            )}
            {tab === "definition" && (
              <>
                <p>{fresh.description || "无职责说明"}</p>
                <div className="detail-grid">
                  <span>用途</span>
                  <strong>
                    {fresh.purpose === "general" ? "通用" : "任务专用"}
                  </strong>
                  <span>结构</span>
                  <strong>
                    {fresh.structure === "atomic" ? "元智能体" : "复合智能体"}
                  </strong>
                  <span>模型策略</span>
                  <strong>
                    {fresh.model_policy === "local"
                      ? "本地工具"
                      : "统一模型网关"}
                  </strong>
                  <span>当前版本</span>
                  <strong>{fresh.current_version || "尚未发布"}</strong>
                </div>
                <h3>能力与工具</h3>
                <div className="tags">
                  {fresh.capabilities?.map((x: string) => (
                    <span key={x}>{taskTypes[x] || x}</span>
                  ))}
                  {fresh.tool_ids?.map((x: string) => (
                    <code key={x}>{x}</code>
                  ))}
                </div>
                <h3>执行指令</h3>
                <pre className="prose">{fresh.instructions || "未设置"}</pre>
                {fresh.members?.length > 0 && (
                  <>
                    <h3>复合成员</h3>
                    {fresh.members.map((m: Item) => (
                      <p key={m.agent_id}>
                        {agents.find((a) => a.id === m.agent_id)?.name ||
                          m.agent_id}{" "}
                        · v{m.version}
                      </p>
                    ))}
                  </>
                )}
                <JsonView value={fresh} />
              </>
            )}
            {tab === "versions" &&
              (versions.length ? (
                versions.map((v) => (
                  <article className="record" key={v.id}>
                    <div className="row-between">
                      <h3>版本 {v.version || v.version_number}</h3>
                      <span className="muted">{time(v.created_at)}</span>
                    </div>
                    <JsonView value={v} label="配置快照与引用" />
                  </article>
                ))
              ) : (
                <Empty
                  title="尚无发布版本"
                  description="发布后保存不可变快照，可供流程与任务引用。"
                />
              ))}
            {tab === "instances" &&
              (instances.length ? (
                instances.map((i) => (
                  <article className="record" key={i.id}>
                    <div className="row-between">
                      <Badge status={i.status} />
                      <button
                        className="link-button"
                        onClick={() => {
                          setSelected(null);
                          onTask(i.task_id);
                        }}
                      >
                        查看所属任务
                        <ArrowUpRight size={15} />
                      </button>
                    </div>
                    <p className="mono small">{i.id}</p>
                    <p>
                      绑定版本 v{i.agent_version} · 独立上下文 {i.context_id}
                    </p>
                    <JsonView value={i} />
                  </article>
                ))
              ) : (
                <Empty
                  title="尚无运行实例"
                  description="实例在任务实际执行时创建，不同任务的上下文相互隔离。"
                />
              ))}
          </div>
          {canEdit && fresh.status !== "destroyed" && (
            <div className="modal-foot wrap">
              <button
                onClick={() => setEdit(fresh)}
                disabled={fresh.status === "retired"}
              >
                <Pencil size={16} />
                编辑定义
              </button>
              {(fresh.status === "draft" || fresh.status === "published") && (
                <button
                  className="primary"
                  onClick={() => setAction({ item: fresh, type: "publish" })}
                >
                  发布新版本
                </button>
              )}
              {fresh.status === "published" && (
                <button
                  onClick={() => setAction({ item: fresh, type: "disable" })}
                >
                  停用
                </button>
              )}
              {fresh.status === "disabled" && (
                <button
                  onClick={() => setAction({ item: fresh, type: "enable" })}
                >
                  重新启用
                </button>
              )}
              {fresh.status !== "retired" && (
                <button
                  onClick={() => setAction({ item: fresh, type: "retire" })}
                >
                  <Archive size={16} />
                  退役
                </button>
              )}
              <button
                className="danger"
                onClick={() => setAction({ item: fresh, type: "destroy" })}
              >
                销毁
              </button>
            </div>
          )}
        </Modal>
      )}
      {action && (
        <Confirm
          title={`${actionName[action.type]}「${action.item.name}」`}
          description={
            action.type === "publish"
              ? "发布将校验能力、工具和复合引用，并冻结一个新版本；存量任务保持原快照。"
              : action.type === "destroy"
                ? "销毁将保留历史与审计，并执行引用保护。存在有效引用时后端会拒绝此操作。"
                : "此操作影响后续新任务的可用性，已受理任务继续按冻结快照运行。"
          }
          danger={action.type === "destroy"}
          confirm={actionName[action.type]}
          onClose={() => setAction(null)}
          onConfirm={() =>
            mutate(
              () =>
                action.type === "destroy"
                  ? remove(idPath("agents", action.item.id))
                  : post(idPath("agents", action.item.id) + "/" + action.type),
              "生命周期状态已更新",
            )
          }
        />
      )}
    </>
  );
}
export function WorkflowsPage({
  projectId,
  workflows,
  agents,
  canEdit,
  mutate,
  notify,
}: Props & { workflows: Item[]; agents: Item[] }) {
  const [edit, setEdit] = useState<Item | null | undefined>(),
    [selected, setSelected] = useState<Item | null>(null),
    [versions, setVersions] = useState<Item[]>([]),
    [record, setRecord] = useState<Item | null>(null),
    [publish, setPublish] = useState<Item | null>(null);
  const fresh = workflows.find((w) => w.id === selected?.id) || selected;
  useEffect(() => {
    if (!fresh) return;
    let active = true;
    api(idPath("workflows", fresh.id) + "/versions")
      .then((v) => active && setVersions(v))
      .catch((e) => notify(e.message, true));
    return () => {
      active = false;
    };
  }, [fresh?.id, fresh?.current_version]);
  return (
    <>
      <SectionTitle
        eyebrow="WORKFLOW LIBRARY"
        title="协作流程"
        description="用明确的依赖关系组合能力，发布后冻结图结构及智能体版本。"
        actions={
          canEdit && (
            <button className="primary" onClick={() => setEdit(null)}>
              <Plus size={18} />
              创建流程
            </button>
          )
        }
      />
      {workflows.length ? (
        <div className="asset-grid">
          {workflows.map((w) => (
            <button
              className="asset-card"
              key={w.id}
              onClick={() => setSelected(w)}
            >
              <div className="row-between">
                <span className="asset-icon">
                  <Workflow size={24} />
                </span>
                <Badge status={w.status} />
              </div>
              <h3>{w.name}</h3>
              <p>{w.description || "未填写流程说明"}</p>
              <div className="card-footer">
                <span>
                  {taskTypes[w.task_type]} · {w.nodes?.length || 0} 个节点
                </span>
                <strong>v{w.current_version || "—"}</strong>
              </div>
            </button>
          ))}
        </div>
      ) : (
        <Empty
          title="建立第一条可复用流程"
          description="先发布智能体，再将它们组织成单节点或组合协作流程。"
          action={
            canEdit && (
              <button onClick={() => setEdit(null)}>
                <Plus size={16} />
                创建流程
              </button>
            )
          }
        />
      )}
      {edit !== undefined && (
        <WorkflowForm
          projectId={projectId}
          item={edit || undefined}
          agents={agents}
          onClose={() => setEdit(undefined)}
          mutate={mutate}
        />
      )}
      {fresh && (
        <Modal wide title={fresh.name} onClose={() => setSelected(null)}>
          <div className="modal-body">
            <div className="row-between">
              <Badge status={fresh.status} />
              <span>
                {taskTypes[fresh.task_type]} · v{fresh.current_version || "—"}
              </span>
            </div>
            <p>{fresh.description}</p>
            <Graph
              nodes={fresh.nodes || []}
              agents={agents}
              onNode={setRecord}
            />
            <h3>发布版本</h3>
            {versions.length ? (
              versions.map((v) => (
                <JsonView
                  key={v.id}
                  value={v}
                  label={`版本 ${v.version || v.version_number} · ${time(v.created_at)}`}
                />
              ))
            ) : (
              <p className="muted">尚无发布版本。</p>
            )}
          </div>
          {canEdit && (
            <div className="modal-foot">
              <button onClick={() => setEdit(fresh)}>
                <Pencil size={16} />
                编辑定义
              </button>
              <button className="primary" onClick={() => setPublish(fresh)}>
                校验并发布
              </button>
            </div>
          )}
        </Modal>
      )}
      {record && (
        <Modal title="节点配置" onClose={() => setRecord(null)}>
          <div className="modal-body">
            <pre>{JSON.stringify(record, null, 2)}</pre>
          </div>
        </Modal>
      )}
      {publish && (
        <Confirm
          title={`发布「${publish.name}」`}
          description="校验有向无环依赖、项目权限与已发布智能体，保存不可变流程版本。"
          onClose={() => setPublish(null)}
          onConfirm={() =>
            mutate(
              () => post(idPath("workflows", publish.id) + "/publish"),
              "流程版本已发布",
            )
          }
        />
      )}
    </>
  );
}
export function DataPage({
  projectId,
  assets,
  canEdit,
  mutate,
  notify,
}: Props & { assets: Item[] }) {
  const [create, setCreate] = useState(false),
    [selected, setSelected] = useState<Item | null>(null),
    [content, setContent] = useState<any>(undefined),
    [revoke, setRevoke] = useState<Item | null>(null),
    [search, setSearch] = useState("");
  const list = assets.filter((a) =>
    `${a.name} ${a.source_id}`.toLowerCase().includes(search.toLowerCase()),
  );
  const activeAsset = useRef("");
  const inspect = async (item: Item) => {
    activeAsset.current = item.id;
    setSelected(item);
    setContent(undefined);
    if (item.status === "active")
      try {
        const result = await readAssetContent(item.id);
        if (activeAsset.current === item.id) setContent(result);
      } catch (e) {
        notify((e as Error).message, true);
      }
  };
  return (
    <>
      <SectionTitle
        eyebrow="DATA CATALOG"
        title="数据资产"
        description="按原始模态接入数据，保留来源、版本、访问范围与有效期。"
        actions={
          canEdit && (
            <button className="primary" onClick={() => setCreate(true)}>
              <FileUp size={18} />
              登记 / 上传数据
            </button>
          )
        }
      />
      <div className="toolbar">
        <div className="search">
          <Search size={17} />
          <input
            aria-label="搜索数据"
            placeholder="搜索资产或来源标识"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <span className="muted">{list.length} 条记录</span>
      </div>
      <Panel>
        {list.length ? (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>数据资产</th>
                  <th>模态 / 大小</th>
                  <th>来源版本</th>
                  <th>访问范围</th>
                  <th>状态 / 有效期</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {list.map((a) => (
                  <tr key={a.id}>
                    <td>
                      <button
                        className="text-button"
                        onClick={() => void inspect(a)}
                      >
                        {a.name}
                      </button>
                      <small className="mono">{a.id}</small>
                    </td>
                    <td>
                      {a.modality}
                      <small>
                        {typeof a.bytes === "number"
                          ? `${a.bytes.toLocaleString()} bytes`
                          : "—"}
                      </small>
                    </td>
                    <td>
                      {a.source_id}
                      <small>{a.source_version}</small>
                    </td>
                    <td>{a.classification === "public" ? "公开" : "仅本地"}</td>
                    <td>
                      <Badge status={a.status} />
                      <small>{time(a.expires_at)}</small>
                    </td>
                    <td>
                      {canEdit && a.status === "active" && (
                        <button
                          className="danger subtle"
                          onClick={() => setRevoke(a)}
                        >
                          撤回
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty
            title="暂无数据资产"
            description="登记带来源的文本或结构化数据，或上传支持的原始文件。"
            action={
              canEdit && (
                <button onClick={() => setCreate(true)}>登记第一份数据</button>
              )
            }
          />
        )}
      </Panel>
      {create && (
        <AssetForm
          projectId={projectId}
          onClose={() => setCreate(false)}
          mutate={mutate}
        />
      )}
      {selected && (
        <Modal wide title={selected.name} onClose={() => setSelected(null)}>
          <div className="modal-body">
            <Badge status={selected.status} />
            <p>{selected.provenance}</p>
            <JsonView value={selected} label="元数据、来源与完整性" />
            {content !== undefined && !content?.binary && (
              <>
                <h3>数据内容</h3>
                <pre className="prose">
                  {typeof content === "string"
                    ? content
                    : JSON.stringify(content, null, 2)}
                </pre>
              </>
            )}
            {content?.binary && (
              <p className="note">
                媒体文件已登记，内容通过受控下载读取。文件接入不表示已经具备语义理解能力。
              </p>
            )}
            {selected.status === "active" && (
              <a
                className="button"
                href={`/api/v2/data-assets/${encodeURIComponent(selected.id)}/content`}
                download
              >
                下载原始数据
              </a>
            )}
            {selected.status === "revoked" && (
              <p className="warning">该资产已撤回，内容不再提供给后续任务。</p>
            )}
          </div>
        </Modal>
      )}
      {revoke && (
        <Confirm
          title={`撤回「${revoke.name}」`}
          description="撤回后，后续读取和任务使用将被拒绝，相关派生记忆同步失效。历史审计记录保留。"
          danger
          onClose={() => setRevoke(null)}
          onConfirm={() =>
            mutate(
              () => post(idPath("data-assets", revoke.id) + "/revoke"),
              "数据资产已撤回",
            )
          }
        />
      )}
    </>
  );
}
