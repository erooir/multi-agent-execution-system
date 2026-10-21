import { useEffect, useMemo, useState } from "react";
import {
  Background,
  Controls,
  Handle,
  MiniMap,
  MarkerType,
  Position,
  ReactFlow,
  useNodesState,
  useNodesInitialized,
  useReactFlow,
  type Node,
} from "@xyflow/react";
import {
  Bot,
  CheckCircle2,
  LayoutGrid,
  Plus,
  Trash2,
  Workflow,
} from "lucide-react";
import type { Item } from "./types";
import { graphLayout } from "./domain";
import { Field } from "./ui";
import ToolParameters from "./ToolParameters";
import {
  connectWorkflow,
  removeWorkflowNode,
  workflowIssues,
} from "./workflowEditorModel";

function BuilderNode({ data, selected }: any) {
  return (
    <div
      className={`builder-node ${selected ? "selected" : ""} ${data.invalid ? "invalid" : ""}`}
    >
      <Handle type="target" position={Position.Left} aria-label="输入连接点" />
      <div className="node-head">
        <span className="node-icon">
          <Bot size={20} />
        </span>
        <div>
          <small>
            步骤 {data.order} ·{" "}
            {data.structure === "composite" ? "复合智能体" : "元智能体"}
          </small>
          <strong>{data.label || "未命名节点"}</strong>
        </div>
      </div>
      <p>{data.agentName || "尚未绑定智能体"}</p>
      <div className="node-foot">
        <span>{data.version ? `当前 v${data.version}` : "待绑定"}</span>
        <span>
          {data.depends_on.length
            ? `${data.depends_on.length} 个前置节点`
            : "起始节点"}
        </span>
      </div>
      <Handle type="source" position={Position.Right} aria-label="输出连接点" />
    </div>
  );
}
const nodeTypes = { builder: BuilderNode };

function FitCanvas({ signature }: { signature: string }) {
  const initialized = useNodesInitialized();
  const { fitView } = useReactFlow();
  useEffect(() => {
    if (!initialized) return;
    const frame = requestAnimationFrame(() => {
      void fitView({ padding: 0.2, maxZoom: 1, duration: 150 });
    });
    return () => cancelAnimationFrame(frame);
  }, [initialized, signature, fitView]);
  return null;
}

export default function WorkflowBuilder({
  nodes,
  agents,
  taskType,
  onChange,
  configTexts,
  onConfigChange,
}: {
  nodes: Item[];
  agents: Item[];
  taskType: string;
  onChange: (nodes: Item[]) => void;
  configTexts: Record<string, string>;
  onConfigChange: (id: string, value: string) => void;
}) {
  const [selectedId, setSelectedId] = useState<string>(nodes[0]?.id || "");
  const [selectedAgent, setSelectedAgent] = useState("");
  const [connectionError, setConnectionError] = useState("");
  const [positions, setPositions] = useState<
    Record<string, { x: number; y: number }>
  >({});
  const [layoutRevision, setLayoutRevision] = useState(0);
  const [canvasNodes, setCanvasNodes, onCanvasNodesChange] =
    useNodesState<Node>([]);
  const available = agents.filter(
    (agent) =>
      agent.status === "published" && agent.capabilities?.includes(taskType),
  );
  const issues = workflowIssues(nodes, agents, taskType);
  const selected = nodes.find((node) => node.id === selectedId) || nodes[0];
  const bound = agents.find((agent) => agent.id === selected?.agent_id);
  const graph = useMemo(() => {
    const layout = graphLayout(nodes);
    return {
      nodes: layout.map((node, index) => {
        const agent = agents.find((item) => item.id === node.agent_id);
        return {
          id: node.id,
          type: "builder",
          selected: node.id === (selected?.id || ""),
          position: positions[node.id] || node.position,
          data: {
            ...node,
            order: index + 1,
            agentName: agent?.name,
            version: agent?.current_version,
            structure: agent?.structure,
            invalid:
              !agent ||
              agent.status !== "published" ||
              !agent.capabilities?.includes(taskType),
          },
        };
      }),
      edges: nodes.flatMap((node) =>
        (node.depends_on || []).map((id: string) => ({
          id: `${id}-${node.id}`,
          source: id,
          target: node.id,
          type: "smoothstep",
          markerEnd: { type: MarkerType.ArrowClosed, color: "#a99bcf" },
          style: { stroke: "#a99bcf", strokeWidth: 2 },
        })),
      ),
    };
  }, [nodes, agents, positions, selected?.id, taskType]);
  useEffect(() => {
    // Preserve measured dimensions while refreshing the persisted definition.
    // React Flow needs its dimension changes applied to initialize edge handles.
    setCanvasNodes((current) =>
      graph.nodes.map((node) => ({
        ...current.find((item) => item.id === node.id),
        ...node,
      })),
    );
  }, [graph.nodes, setCanvasNodes]);
  const updateNode = (changes: Item) =>
    onChange(
      nodes.map((node) =>
        node.id === selected?.id ? { ...node, ...changes } : node,
      ),
    );
  function connect(source: string, target: string) {
    try {
      onChange(connectWorkflow(nodes, source, target));
      setConnectionError("");
    } catch (error) {
      setConnectionError(
        error instanceof Error ? error.message : String(error),
      );
    }
  }
  function addNode() {
    const agent =
      available.find((item) => item.id === selectedAgent) || available[0];
    if (!agent) return;
    const id = `node-${crypto.randomUUID().slice(0, 8)}`;
    onChange([
      ...nodes,
      { id, label: agent.name, agent_id: agent.id, depends_on: [], config: {} },
    ]);
    setSelectedId(id);
    setConnectionError("");
    setPositions({});
  }
  return (
    <section className="workflow-builder" aria-label="可视化流程编辑器">
      <div className="builder-toolbar">
        <div>
          <h3>
            <Workflow size={18} /> 编排协作流程
          </h3>
          <p>从能力库添加节点，连接前后依赖，选中节点配置处理规则。</p>
        </div>
        <button
          type="button"
          onClick={() => {
            setPositions({});
            setLayoutRevision((value) => value + 1);
          }}
        >
          <LayoutGrid size={16} />
          整理布局
        </button>
      </div>
      <div className="builder-add">
        <Field label="添加智能体节点">
          <select
            value={
              available.some((a) => a.id === selectedAgent)
                ? selectedAgent
                : available[0]?.id || ""
            }
            onChange={(event) => setSelectedAgent(event.target.value)}
          >
            {!available.length && (
              <option value="">暂无匹配能力的已发布智能体</option>
            )}
            {available.map((agent) => (
              <option key={agent.id} value={agent.id}>
                {agent.name} · v{agent.current_version}
              </option>
            ))}
          </select>
        </Field>
        <button
          type="button"
          disabled={!available.length || nodes.length >= 30}
          onClick={addNode}
        >
          <Plus size={16} />
          添加节点
        </button>
      </div>
      {!available.length && (
        <p className="warning">
          请先在智能体资产中发布支持此任务类型的智能体，再添加流程节点。
        </p>
      )}
      <div className="builder-workspace">
        <div className="builder-canvas">
          <ReactFlow
            nodes={canvasNodes}
            edges={graph.edges}
            nodeTypes={nodeTypes}
            colorMode="dark"
            fitView
            fitViewOptions={{ padding: 0.2 }}
            minZoom={0.2}
            maxZoom={1.5}
            onNodeClick={(_, node) => setSelectedId(node.id)}
            onConnect={(connection) =>
              connect(connection.source, connection.target)
            }
            onNodesChange={onCanvasNodesChange}
            onNodeDragStop={(_, node) =>
              setPositions((current) => ({
                ...current,
                [node.id]: node.position,
              }))
            }
            onEdgesDelete={(edges) =>
              onChange(
                nodes.map((node) => ({
                  ...node,
                  depends_on: node.depends_on.filter(
                    (id: string) =>
                      !edges.some(
                        (edge) => edge.source === id && edge.target === node.id,
                      ),
                  ),
                })),
              )
            }
            deleteKeyCode={null}
          >
            <Background gap={24} />
            <FitCanvas
              signature={`${layoutRevision}:${nodes.map((node) => `${node.id}:${node.depends_on.join(",")}`).join(";")}`}
            />
            <Controls showInteractive={false} />
            <MiniMap
              nodeColor="#74669d"
              maskColor="#151619b8"
              pannable
              zoomable
            />
          </ReactFlow>
          {!nodes.length && (
            <div className="builder-canvas-empty">
              <Workflow size={32} />
              <strong>从一个智能体开始</strong>
              <span>添加节点后，可从右侧连接点拖动到下一节点左侧。</span>
            </div>
          )}
        </div>
        <aside className="builder-inspector" aria-label="节点设置">
          {selected ? (
            <>
              <div className="row-between">
                <h3>节点设置</h3>
                <button
                  type="button"
                  className="icon-button danger"
                  aria-label={`删除节点 ${selected.label}`}
                  onClick={() => {
                    onChange(removeWorkflowNode(nodes, selected.id));
                    setSelectedId("");
                    setConnectionError("");
                  }}
                >
                  <Trash2 size={17} />
                </button>
              </div>
              <Field label="当前节点">
                <select
                  value={selected.id}
                  onChange={(event) => setSelectedId(event.target.value)}
                >
                  {nodes.map((node, index) => (
                    <option key={node.id} value={node.id}>
                      {index + 1}. {node.label}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="节点名称">
                <input
                  required
                  maxLength={100}
                  value={selected.label}
                  onChange={(event) =>
                    updateNode({ label: event.target.value })
                  }
                />
              </Field>
              <Field label="绑定智能体">
                <select
                  required
                  value={selected.agent_id}
                  onChange={(event) =>
                    updateNode({ agent_id: event.target.value })
                  }
                >
                  {!available.some(
                    (agent) => agent.id === selected.agent_id,
                  ) && (
                    <option value={selected.agent_id}>
                      {bound?.name || "请选择"} · 当前不可用
                    </option>
                  )}
                  {available.map((agent) => (
                    <option key={agent.id} value={agent.id}>
                      {agent.name} · v{agent.current_version}
                    </option>
                  ))}
                </select>
              </Field>
              <p className="note">
                {bound?.description ||
                  "发布流程时冻结绑定的智能体版本；运行任务使用发布快照。"}
              </p>
              <fieldset className="dependency-picker">
                <legend>前置节点</legend>
                {nodes.length < 2 ? (
                  <p className="muted">单节点可独立执行。</p>
                ) : (
                  nodes
                    .filter((node) => node.id !== selected.id)
                    .map((node) => (
                      <label className="check" key={node.id}>
                        <input
                          type="checkbox"
                          checked={selected.depends_on.includes(node.id)}
                          onChange={(event) =>
                            event.target.checked
                              ? connect(node.id, selected.id)
                              : updateNode({
                                  depends_on: selected.depends_on.filter(
                                    (id: string) => id !== node.id,
                                  ),
                                })
                          }
                        />
                        {node.label}
                      </label>
                    ))
                )}
                <small>勾选前置节点或在画布连线；取消勾选即可移除依赖。</small>
              </fieldset>
              <h3>节点参数</h3>
              <ToolParameters
                key={selected.id}
                inherited
                value={
                  configTexts[selected.id] ??
                  JSON.stringify(selected.config || {}, null, 2)
                }
                onChange={(value) => onConfigChange(selected.id, value)}
                structured={taskType === "structured_transform"}
                text={taskType === "source_summary"}
              />
            </>
          ) : (
            <p className="muted">添加或选择节点后，在此编辑绑定和参数。</p>
          )}
        </aside>
      </div>
      {connectionError && (
        <p role="alert" className="error-line">
          {connectionError}
        </p>
      )}
      <div
        className={`builder-validation ${issues.length ? "needs-attention" : "valid"}`}
        aria-live="polite"
      >
        {issues.length ? (
          <>
            <strong>配置检查 · {issues.length} 项待处理</strong>
            <ul>
              {issues.map((issue) => (
                <li key={issue}>{issue}</li>
              ))}
            </ul>
          </>
        ) : (
          <>
            <CheckCircle2 size={17} />
            <span>
              {nodes.length} 个节点 · {graph.edges.length} 条依赖 ·
              结构检查通过，发布时继续由后端验证权限与版本。
            </span>
          </>
        )}
      </div>
    </section>
  );
}
