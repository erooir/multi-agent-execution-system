import { businessLabel, caseBusiness } from "./agentBusiness";
import { CatalogRange } from "./CatalogRange";
import { OriginalDownload, pdfPreviewHref } from "./OriginalDownload";
import { useEffect, useState } from "react";
import {
  Plus,
  Search,
  Layers3,
  Archive,
  Pencil,
  ArrowUpRight,
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
import { AgentForm, WorkflowForm } from "./Forms";
import Graph from "./Graph";
import { AgentDetails, AgentVersions } from "./AssetDetails";
import { ParameterSummary } from "./ToolParameters";
import { isRestrictedAgent } from "./agentScopeModel";
import "./agent-registry.css";
type Props = {
  projectId: string;
  canEdit: boolean;
  mutate: Mutation;
  notify: Notify;
};
export function AgentDetailsDialog({
  projectId,
  agents,
  capabilities,
  canEdit,
  mutate,
  notify,
  onTask,
  workflows = [],
  assets = [],
  onWorkflow,
  initialId,
  initialToolId,
  onClose,
  offline = false,
}: Props & {
  agents: Item[];
  capabilities: Item[];
  onTask: (id: string) => void;
  workflows?: Item[];
  assets?: Item[];
  onWorkflow?: (id: string) => void;
  initialId: string;
  onClose: () => void;
  initialToolId?: string | null;
  offline?: boolean;
}) {
  const [edit, setEdit] = useState<Item>(),
    [selectedId, setSelectedId] = useState(initialId),
    [versions, setVersions] = useState<Item[]>([]),
    [instances, setInstances] = useState<Item[]>([]),
    [tab, setTab] = useState("definition"),
    [detailError, setDetailError] = useState(""),
    [action, setAction] = useState<{ item: Item; type: string } | null>(null);
  const fresh = agents.find((a) => a.id === selectedId && a.project_id === projectId);
  const caseId = fresh && caseBusiness(fresh)?.caseId;
  useEffect(() => {
    if (!fresh) return;
    let active = true;
    setDetailError("");
    setVersions([]);
    setInstances([]);
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
  const actionName: Record<string, string> = {
    publish: "发布新版本",
    disable: "停用",
    enable: "重新启用",
    retire: "退役",
    destroy: "销毁",
  };
  return (
    <>
      {edit !== undefined && (
        <AgentForm
          projectId={projectId}
          offline={offline}
          item={edit || undefined}
          agents={agents}
          capabilities={capabilities}
          onClose={() => setEdit(undefined)}
          mutate={mutate}
        />
      )}
      {fresh && (
        <Modal wide title={fresh.name} onClose={onClose}>
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
            {fresh.capabilities?.some((id:string)=>["image_inspect","image_ocr","image_understand"].includes(id)) &&
              <details className="panel"><summary>图像输入与能力边界</summary>
                <p>本地可解码图像、检查亮度与对比度，并对清晰或低对比度的文档提取文字。动图只处理首帧。</p>
                <p>视觉模型在两张合成展板上正确读取编号、图形和颜色。来源位置和版本可以校验；文字遮挡、图外日期和拍摄地点需要额外证据。自然场景尚缺独立实测。</p>
              </details>}
            <dl className="catalog-fields">
              <dt>业务方向</dt><dd>{businessLabel(fresh)}</dd>
              {caseId && <><dt>所属案例</dt><dd>{caseId}</dd></>}
              {fresh.management_profile && <>
                <dt>创建方式</dt><dd>{fresh.management_profile.creation_method==="interview" ? "对话生成" : "管理配置"}</dd>
                <dt>创建或分类原因</dt><dd>{fresh.management_profile.reason}</dd>
              </>}
            </dl>
            {detailError && (
              <p role="alert" className="error-line">
                {detailError}
              </p>
            )}
            {tab === "definition" && (
              <AgentDetails
                agent={fresh}
                canEdit={canEdit && !isRestrictedAgent(fresh)}
                offline={offline}
                initialToolId={
                  fresh.id === initialId ? initialToolId : undefined
                }
                assets={assets}
                published={versions.find(
                  (v) =>
                    (v.agent_id || v.definition_id) === fresh.id &&
                    v.version === fresh.current_version,
                )}
                agents={agents}
                workflows={workflows}
                onAgent={(id) => {
                  setSelectedId(id);
                  setTab("definition");
                }}
                onWorkflow={
                  onWorkflow
                    ? (id) => {
                        onClose();
                        onWorkflow(id);
                      }
                    : undefined
                }
              />
            )}
            {tab === "versions" && (
              <AgentVersions
                key={fresh.id}
                versions={versions}
                currentVersion={fresh.current_version}
              />
            )}
            {tab === "instances" &&
              (instances.length ? (
                instances.map((i) => (
                  <article className="record" key={i.id}>
                    <div className="row-between">
                      <Badge status={i.status} />
                      <button
                        className="link-button"
                        onClick={() => {
                          onClose();
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
          {canEdit &&
            fresh.status !== "destroyed" &&
            !isRestrictedAgent(fresh) && (
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
      {!fresh && <Modal title="智能体详情" onClose={onClose}><div className="modal-body"><Empty title="当前工作空间找不到该智能体" /></div></Modal>}
    </>
  );
}
export function WorkflowDetailsDialog({
  projectId,
  workflows,
  agents,
  canEdit,
  mutate,
  notify,
  initialId,
  onClose,
}: Props & { workflows: Item[]; agents: Item[]; initialId: string; onClose: () => void }) {
  const [edit, setEdit] = useState<Item>(),
    [versions, setVersions] = useState<Item[]>([]),
    [record, setRecord] = useState<Item | null>(null),
    [publish, setPublish] = useState<Item | null>(null);
  const fresh = workflows.find((workflow) => workflow.id === initialId);
  useEffect(() => {
    if (!fresh) return;
    let active = true;
    setVersions([]);
    api(idPath("workflows", fresh.id) + "/versions")
      .then((v) => active && setVersions(v))
      .catch((e) => notify(e.message, true));
    return () => {
      active = false;
    };
  }, [fresh?.id, fresh?.current_version]);
  return (
    <>
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
        <Modal wide title={fresh.name} onClose={onClose}>
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
            <h3>节点绑定</h3>
            <div className="binding-list">
              {(fresh.nodes || []).map((node: Item) => (
                <div key={node.id}>
                  <button
                    className="link-button"
                    onClick={() => setRecord(node)}
                  >
                    {node.label}
                    <ArrowUpRight size={14} />
                  </button>
                  <span>
                    {agents.find((agent) => agent.id === node.agent_id)?.name ||
                      node.agent_id}
                  </span>
                  <small>
                    {node.agent_version
                      ? `v${node.agent_version}`
                      : "发布时冻结版本"}
                  </small>
                </div>
              ))}
            </div>
            <h3>发布版本</h3>
            {versions.length ? (
              versions.map((version) => (
                <article className="record" key={version.id}>
                  <div className="row-between">
                    <strong>
                      v{version.version || version.version_number}
                    </strong>
                    <span className="muted">{time(version.created_at)}</span>
                  </div>
                  <p>
                    {version.nodes?.length || 0} 个节点 ·{" "}
                    {version.nodes?.reduce(
                      (total: number, node: Item) =>
                        total + (node.depends_on?.length || 0),
                      0,
                    ) || 0}{" "}
                    条依赖
                  </p>
                  <div className="tags">
                    {(version.nodes || []).map((node: Item) => (
                      <span key={node.id}>
                        {node.label} · v{node.agent_version || "—"}
                      </span>
                    ))}
                  </div>
                  <JsonView value={version} label="完整流程快照" />
                </article>
              ))
            ) : (
              <p className="muted">尚无发布版本。</p>
            )}
          </div>
          {canEdit && !fresh.task_case_id && (
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
            <h3>{record.label}</h3>
            <div className="detail-grid">
              <span>绑定智能体</span>
              <strong>
                {agents.find((agent) => agent.id === record.agent_id)?.name ||
                  record.agent_id}
              </strong>
              <span>绑定版本</span>
              <strong>
                {record.agent_version
                  ? `v${record.agent_version}`
                  : "发布时冻结"}
              </strong>
              <span>前置节点</span>
              <strong>
                {record.depends_on
                  ?.map(
                    (id: string) =>
                      fresh?.nodes?.find((node: Item) => node.id === id)
                        ?.label || id,
                  )
                  .join("、") || "无，可直接开始"}
              </strong>
            </div>
            <h3>节点执行参数</h3>
            <ParameterSummary parameters={record.config} />
            <JsonView value={record} label="完整节点配置" />
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
      {!fresh && <Modal title="流程详情" onClose={onClose}><div className="modal-body"><Empty title="当前工作空间找不到该流程" /></div></Modal>}
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
  const [selectedId, setSelectedId] = useState<string | null>(() => {
    const params = new URLSearchParams(window.location.search);
    return params.get("project") === projectId ? params.get("asset") : null;
  }),
    [loaded, setLoaded] = useState<{ id: string; value: any } | null>(null),
    [revoke, setRevoke] = useState<Item | null>(null),
    [includeSupplementary, setIncludeSupplementary] = useState(false),
    [search, setSearch] = useState("");
  const list = assets.filter(
    (a) =>
      (includeSupplementary || a.case_library_role !== "supplementary") &&
      `${a.name} ${a.source_id} ${a.original_file?.filename ?? ""}`.toLowerCase().includes(search.toLowerCase()),
  );
  const selected = assets.find((asset) => asset.project_id === projectId && asset.id === selectedId);
  const readable = selected?.status === "active" && Date.parse(selected.expires_at) > Date.now();
  const pdfPreview = pdfPreviewHref(selected);
  const contentId = readable && (!pdfPreview || selected.is_file_index) ? selected.id : null;
  const content = loaded && loaded.id === contentId ? loaded.value : undefined;
  useEffect(() => {
    let current = true;
    setLoaded(null);
    if (contentId) {
      void readAssetContent(contentId)
        .then((value) => { if (current) setLoaded({ id: contentId, value }); })
        .catch((e: Error) => { if (current) notify(e.message, true); });
    }
    return () => { current = false; };
  }, [contentId, notify]);
  const selectAsset = (id: string | null) => {
    setSelectedId(id);
    const url = new URL(window.location.href);
    if (id) {
      url.searchParams.set("view", "library");
      url.searchParams.set("tab", "data");
      url.searchParams.set("project", projectId);
      url.searchParams.set("asset", id);
    } else {
      url.searchParams.delete("asset");
    }
    window.history.replaceState(null, "", url);
  };
  return (
    <>
      <SectionTitle
        eyebrow="DATA CATALOG"
        title="上游数据目录"
        description="按原始模态接入数据，保留来源、版本、访问范围与有效期。"
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
        <CatalogRange
          assets={assets}
          includeSupplementary={includeSupplementary}
          onChange={setIncludeSupplementary}
        />
        <span className="muted">{list.length} 条记录</span>
      </div>
      <Panel>
        {list.length ? (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>来源</th>
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
                        onClick={() => selectAsset(a.id)}
                      >
                        {a.name}
                      </button>
                      <small className="mono">{a.id}</small>
                    </td>
                    <td>
                      {a.is_file_index ? "原件归档" : a.modality}
                      <small>
                        {typeof (a.original_file?.bytes ?? a.bytes) === "number"
                          ? `${(a.original_file?.bytes ?? a.bytes).toLocaleString()} bytes`
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
                      <OriginalDownload asset={a} />
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
            description="上游系统接入的项目来源会显示在这里。"
          />
        )}
      </Panel>
      {selected && (
        <Modal wide title={selected.name} onClose={() => selectAsset(null)}>
          <div className="modal-body">
            <Badge status={selected.status} />
            <p>{selected.provenance}</p>
            <JsonView value={selected} label="元数据、来源与完整性" />
            {selected.modality === "image" && readable && (
              <img
                src={`/api/v2/data-assets/${encodeURIComponent(selected.id)}/content`}
                alt={selected.name}
                style={{
                  maxWidth: "100%",
                  maxHeight: 480,
                  objectFit: "contain",
                }}
              />
            )}
            {content !== undefined && !content?.binary && (
              <>
                <h3>{selected.is_file_index ? "原件索引" : "数据内容"}</h3>
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
            {selected.original_file && (
              <div><p>{selected.original_file.filename}</p><OriginalDownload asset={selected} /></div>
            )}
            {pdfPreview && (
              <iframe
                title={`PDF 预览 · ${selected.original_file.filename}`}
                src={pdfPreview}
                style={{ width: "100%", height: "60vh", border: 0 }}
              />
            )}
            {!selected.original_file && readable && (
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
