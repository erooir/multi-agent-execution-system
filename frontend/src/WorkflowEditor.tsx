import { useCallback, useEffect, useState } from "react";
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  Handle,
  Position,
  addEdge,
  applyNodeChanges,
  applyEdgeChanges,
  type Connection,
  type NodeChange,
  type EdgeChange,
  type ReactFlowInstance,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import {
  Play,
  Save,
  CheckCheck,
  Upload,
  Plus,
  Trash2,
  GitBranch,
  History,
  Copy,
  ArrowLeft,
  FileText,
  Search,
  ShieldCheck,
  BrainCircuit,
  Repeat2,
  Flag,
  SlidersHorizontal,
  CheckCircle2,
  LayoutGrid,
} from "lucide-react";
import { api, post, put, type RecordData } from "./api";
import { layoutWorkflow } from "./workflowLayout";
import {
  Badge,
  Field,
  Modal,
  JsonView,
  ActionButton,
  InlineMessage,
} from "./ui";
const kinds: Record<string, { name: string; icon: any }> = {
  start: { name: "开始", icon: Play },
  parse: { name: "需求解析", icon: FileText },
  retrieve: { name: "知识检索", icon: Search },
  condition: { name: "条件判断", icon: GitBranch },
  batch: { name: "批量处理", icon: Repeat2 },
  analyze: { name: "分析推理", icon: BrainCircuit },
  review: { name: "人工审核", icon: ShieldCheck },
  report: { name: "报告生成", icon: FileText },
  end: { name: "结束", icon: Flag },
};
function TaskNode({ data, selected }: any) {
  const kind = kinds[data.kind] || kinds.analyze;
  const Icon = kind.icon;
  return (
    <div className={`task-node ${data.kind} ${selected ? "selected" : ""}`}>
      <Handle type="target" position={Position.Left} />
      <div className="task-node-icon">
        <Icon size={18} />
      </div>
      <div>
        <small>{kind.name}</small>
        <strong>{data.label}</strong>
      </div>
      {data.kind === "condition" ? (
        <>
          <Handle
            type="source"
            position={Position.Right}
            id="pass"
            style={{ top: "32%" }}
          />
          <Handle
            type="source"
            position={Position.Right}
            id="fail"
            style={{ top: "74%", background: "#d69f4d" }}
          />
          <span className="handle-label pass">通过</span>
          <span className="handle-label fail">不通过</span>
        </>
      ) : (
        <Handle type="source" position={Position.Right} />
      )}
    </div>
  );
}
const nodeTypes = { task: TaskNode };
export default function WorkflowEditor({
  workflow,
  agents,
  skills,
  canEdit,
  onClose,
  onSaved,
  onRun,
  notify,
}: {
  workflow: RecordData;
  agents: RecordData[];
  skills: RecordData[];
  canEdit: boolean;
  onClose: () => void;
  onSaved: () => void;
  onRun: (id: string) => void;
  notify: (s: string, error?: boolean) => void;
}) {
  const [record, setRecord] = useState(workflow),
    [nodes, setNodes] = useState<any[]>(workflow.nodes || []),
    [edges, setEdges] = useState<any[]>(workflow.edges || []),
    [selected, setSelected] = useState<string | null>(null),
    [selectedEdge, setSelectedEdge] = useState<string | null>(null),
    [dirty, setDirty] = useState(false),
    [validation, setValidation] = useState<any>(null),
    [versions, setVersions] = useState<any[] | null>(null),
    [configText, setConfigText] = useState("{}"),
    [configError, setConfigError] = useState(""),
    [flow, setFlow] = useState<ReactFlowInstance<any, any> | null>(null);
  const node = nodes.find((x) => x.id === selected),
    edge = edges.find((x) => x.id === selectedEdge);
  useEffect(() => {
    setConfigText(JSON.stringify(node?.data.config || {}, null, 2));
    setConfigError("");
  }, [selected]);
  const onNodesChange = useCallback((changes: NodeChange[]) => {
    setNodes((v) => applyNodeChanges(changes, v));
    if (changes.some((x) => x.type !== "select" && x.type !== "dimensions"))
      setDirty(true);
  }, []);
  const onEdgesChange = useCallback((changes: EdgeChange[]) => {
    setEdges((v) => applyEdgeChanges(changes, v));
    if (changes.some((x) => x.type !== "select")) setDirty(true);
  }, []);
  const connect = useCallback((connection: Connection) => {
    setEdges((v) =>
      addEdge(
        {
          ...connection,
          id: `edge-${Date.now()}`,
          label:
            connection.sourceHandle === "pass"
              ? "通过"
              : connection.sourceHandle === "fail"
                ? "不通过"
                : undefined,
        },
        v,
      ),
    );
    setDirty(true);
  }, []);
  const execute = async (fn: () => Promise<any>) => {
    try {
      return await fn();
    } catch (e) {
      notify((e as Error).message, true);
    }
  };
  async function save() {
    if (configError) throw new Error("请先修正节点配置 JSON");
    if (!dirty) return record;
    const payload = {
      ...record,
      nodes: nodes.map(({ id, type, position, data }) => ({
        id,
        type,
        position,
        data,
      })),
      edges: edges.map(({ id, source, target, label, sourceHandle }) => ({
        id,
        source,
        target,
        label,
        sourceHandle,
      })),
    };
    const result = await put(`/workflows/${record.id}`, payload);
    setRecord(result);
    setDirty(false);
    onSaved();
    return result;
  }
  function updateNode(patch: RecordData) {
    setNodes((v) =>
      v.map((n) =>
        n.id === selected ? { ...n, data: { ...n.data, ...patch } } : n,
      ),
    );
    setDirty(true);
  }
  function addNode(kind: string) {
    const id = `${kind}-${Date.now()}`;
    setNodes((v) => [
      ...v,
      {
        id,
        type: "task",
        position: {
          x: 80 + (v.length % 4) * 290,
          y: 100 + Math.floor(v.length / 4) * 140,
        },
        data: { label: kinds[kind].name, kind, config: {} },
      },
    ]);
    setSelected(id);
    setSelectedEdge(null);
    setDirty(true);
  }
  return (
    <div className="workflow-editor">
      <header className="editor-header">
        <button
          className="icon-button"
          onClick={() => {
            if (!dirty || window.confirm("尚有未保存的画布修改，确定退出吗？"))
              onClose();
          }}
          title="返回"
        >
          <ArrowLeft size={20} />
        </button>
        <div className="editor-title">
          <input
            aria-label="流程名称"
            disabled={!canEdit}
            value={record.name}
            onChange={(e) => {
              setRecord({ ...record, name: e.target.value });
              setDirty(true);
            }}
          />
          <div>
            <Badge status={record.status} />
            <span>
              版本 {record.version || 1} · {dirty ? "有未保存更改" : "已保存"}
            </span>
          </div>
        </div>
        <div className="editor-actions">
          <ActionButton
            className="button ghost"
            onClick={() =>
              execute(async () =>
                setVersions(await api(`/workflows/${record.id}/versions`)),
              )
            }
          >
            <History size={16} />
            版本
          </ActionButton>
          <ActionButton
            className="button ghost"
            disabled={!canEdit}
            onClick={() =>
              execute(async () => {
                if (configError) throw new Error("请先修正节点配置 JSON");
                const result = await post(`/workflows/${record.id}/validate`, {
                  nodes,
                  edges,
                });
                setValidation(result);
                notify(
                  result.valid ? "流程校验通过" : "流程需要调整",
                  !result.valid,
                );
              })
            }
          >
            <CheckCheck size={16} />
            校验
          </ActionButton>
          <ActionButton
            className="button"
            disabled={!canEdit}
            onClick={() =>
              execute(async () => {
                await save();
                notify("流程已保存");
              })
            }
          >
            <Save size={16} />
            保存
          </ActionButton>
          <ActionButton
            className="button"
            disabled={!canEdit}
            onClick={() =>
              execute(async () => {
                await save();
                const result = await post(`/workflows/${record.id}/publish`);
                setRecord(result);
                onSaved();
                notify("流程已发布");
              })
            }
          >
            <Upload size={16} />
            发布
          </ActionButton>
          <ActionButton
            className="button primary"
            disabled={!canEdit}
            onClick={() =>
              execute(async () => {
                if (dirty || record.status !== "published") {
                  await save();
                  const published = await post(
                    `/workflows/${record.id}/publish`,
                  );
                  setRecord(published);
                  onSaved();
                }
                onRun(record.id);
              })
            }
          >
            <Play size={16} />
            {dirty || record.status !== "published" ? "保存发布并运行" : "运行"}
          </ActionButton>
        </div>
      </header>
      <div className="editor-main">
        <aside className="node-palette">
          <div className="eyebrow">构建流程</div>
          <h3>节点组件</h3>
          <p>点击添加节点，拖拽端点连接。</p>
          {Object.entries(kinds).map(([key, { name, icon: Icon }]) => (
            <button key={key} disabled={!canEdit} onClick={() => addNode(key)}>
              <Icon size={17} />
              {name}
              <Plus size={14} />
            </button>
          ))}
          <div className="palette-note">
            <ShieldCheck size={18} />
            <span>流程中的审核节点会暂停运行，等待人工确认。</span>
          </div>
        </aside>
        <div className="flow-canvas">
          <div className="canvas-toolbar">
            <button
              className="button small"
              disabled={!canEdit}
              onClick={() => {
                try {
                  setNodes(layoutWorkflow(nodes, edges));
                  setDirty(true);
                  requestAnimationFrame(() =>
                    requestAnimationFrame(() =>
                      flow?.fitView({ padding: 0.18, duration: 350 }),
                    ),
                  );
                  notify(
                    "已按执行顺序整理节点；保存后生效，仍可手动拖拽调整。",
                  );
                } catch (error) {
                  notify((error as Error).message, true);
                }
              }}
            >
              <LayoutGrid size={14} />
              自动整理
            </button>
          </div>
          <ReactFlow
            onInit={setFlow}
            nodes={nodes}
            edges={edges}
            nodeTypes={nodeTypes}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={connect}
            onNodeClick={(_, n) => {
              setSelected(n.id);
              setSelectedEdge(null);
            }}
            onEdgeClick={(_, e) => {
              setSelectedEdge(e.id);
              setSelected(null);
            }}
            onPaneClick={() => {
              setSelected(null);
              setSelectedEdge(null);
            }}
            nodesDraggable={canEdit}
            nodesConnectable={canEdit}
            edgesReconnectable={canEdit}
            deleteKeyCode={canEdit ? ["Backspace", "Delete"] : null}
            fitView
            minZoom={0.15}
            maxZoom={1.6}
          >
            <Background gap={22} size={1} color="#c8d3db" />
            <Controls />
            <MiniMap nodeColor="#d6e6e4" pannable zoomable />
          </ReactFlow>
          <div className="canvas-hint">
            {nodes.length} 个节点 · {edges.length} 条连线{" "}
            <span>拖拽编排 / 滚轮缩放</span>
          </div>
        </div>
        <aside className="node-inspector">
          <div className="eyebrow">配置面板</div>
          <h3>{node ? "节点属性" : edge ? "连线属性" : "流程概览"}</h3>
          {node ? (
            <>
              <Field label="节点名称">
                <input
                  disabled={!canEdit}
                  value={node.data.label}
                  onChange={(e) => updateNode({ label: e.target.value })}
                />
              </Field>
              <Field label="节点类型">
                <input
                  disabled
                  value={kinds[node.data.kind]?.name || node.data.kind}
                />
              </Field>
              <Field label="执行智能体">
                <select
                  disabled={!canEdit}
                  value={node.data.agent_id || ""}
                  onChange={(e) =>
                    updateNode({ agent_id: e.target.value || undefined })
                  }
                >
                  <option value="">使用默认角色</option>
                  {agents.map((a) => (
                    <option key={a.id} value={a.id}>
                      {a.name}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="使用技能">
                <select
                  disabled={!canEdit}
                  value={node.data.skill_id || ""}
                  onChange={(e) =>
                    updateNode({ skill_id: e.target.value || undefined })
                  }
                >
                  <option value="">按节点类型执行</option>
                  {skills.map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.name}
                    </option>
                  ))}
                </select>
              </Field>
              <Field
                label="节点配置（JSON）"
                hint={
                  node.data.kind === "condition"
                    ? "min_evidence：最少证据数；contains：需求包含文字"
                    : node.data.kind === "batch"
                      ? "items：条目数组；count：批次数，1–10"
                      : node.data.kind === "analyze"
                        ? "instruction：分析任务说明"
                        : "以结构化配置补充节点行为"
                }
              >
                <textarea
                  className="code-input"
                  rows={9}
                  disabled={!canEdit}
                  value={configText}
                  onChange={(e) => {
                    setConfigText(e.target.value);
                    try {
                      const data = JSON.parse(e.target.value);
                      if (
                        !data ||
                        Array.isArray(data) ||
                        typeof data !== "object"
                      )
                        throw Error();
                      updateNode({ config: data });
                      setConfigError("");
                    } catch {
                      setConfigError("请输入有效的 JSON 对象");
                    }
                  }}
                />
              </Field>
              {configError && <p className="error-text">{configError}</p>}
              <button
                className="button danger"
                disabled={!canEdit}
                onClick={() => {
                  setNodes((v) => v.filter((x) => x.id !== selected));
                  setEdges((v) =>
                    v.filter(
                      (x) => x.source !== selected && x.target !== selected,
                    ),
                  );
                  setSelected(null);
                  setDirty(true);
                }}
              >
                <Trash2 size={15} />
                删除节点
              </button>
            </>
          ) : edge ? (
            <>
              <Field label="连线标签">
                <input
                  disabled={!canEdit}
                  value={edge.label || ""}
                  onChange={(e) => {
                    setEdges((v) =>
                      v.map((x) =>
                        x.id === selectedEdge
                          ? { ...x, label: e.target.value }
                          : x,
                      ),
                    );
                    setDirty(true);
                  }}
                />
              </Field>
              <p className="muted">
                条件节点请从“通过 / 不通过”两个端口分别连线。
              </p>
              <button
                className="button danger"
                disabled={!canEdit}
                onClick={() => {
                  setEdges((v) => v.filter((x) => x.id !== selectedEdge));
                  setSelectedEdge(null);
                  setDirty(true);
                }}
              >
                <Trash2 size={15} />
                删除连线
              </button>
            </>
          ) : (
            <>
              <Field label="流程说明">
                <textarea
                  rows={5}
                  disabled={!canEdit}
                  value={record.description || ""}
                  onChange={(e) => {
                    setRecord({ ...record, description: e.target.value });
                    setDirty(true);
                  }}
                />
              </Field>
              <Field label="类别">
                <select
                  disabled={!canEdit}
                  value={record.category || "technology"}
                  onChange={(e) => {
                    setRecord({ ...record, category: e.target.value });
                    setDirty(true);
                  }}
                >
                  <option value="technology">科技研究</option>
                  <option value="geography">地理信息</option>
                  <option value="situational">态势分析</option>
                </select>
              </Field>
              <div className="inspector-tip">
                <SlidersHorizontal size={24} />
                <p>选择画布上的节点，配置智能体、技能和执行参数。</p>
              </div>
            </>
          )}
          {validation && (
            <div
              className={`validation-result ${validation.valid ? "valid" : ""}`}
            >
              <strong>
                {validation.valid ? "✓ 校验通过" : "请修复以下问题"}
              </strong>
              {[
                ...(validation.errors || []),
                ...(validation.warnings || []),
              ].map((e: string, i: number) => (
                <p key={i}>{e}</p>
              ))}
            </div>
          )}
        </aside>
      </div>
      {versions && (
        <Modal title="流程版本历史" onClose={() => setVersions(null)}>
          {versions.length ? (
            versions.map((v: any, i) => (
              <div className="list-row" key={v.id || v.version || i}>
                <div>
                  <strong>版本 {v.version || i + 1}</strong>
                  <p>{v.name || v.created_at || "已保存快照"}</p>
                </div>
                <ActionButton
                  disabled={!canEdit}
                  onClick={() =>
                    execute(async () => {
                      const r = await post(`/workflows/${record.id}/restore`, {
                        version: v.version,
                      });
                      setRecord(r);
                      setNodes(r.nodes || []);
                      setEdges(r.edges || []);
                      setDirty(false);
                      setVersions(null);
                      onSaved();
                      notify("版本已恢复");
                    })
                  }
                >
                  恢复
                </ActionButton>
              </div>
            ))
          ) : (
            <p className="muted">尚无历史快照，发布后将保留版本。</p>
          )}
        </Modal>
      )}
    </div>
  );
}
