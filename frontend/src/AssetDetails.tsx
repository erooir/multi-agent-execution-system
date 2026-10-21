import { useState } from "react";
import { ArrowUpRight, Bot } from "lucide-react";
import type { Item } from "./types";
import { taskTypes, time } from "./domain";
import { Badge, Empty, Field, JsonView } from "./ui";
import { ParameterSummary } from "./ToolParameters";
import { changedVersionFields, versionFields } from "./workflowEditorModel";

const modalities: Record<string, string> = {
  text: "文本",
  structured: "结构化数据",
  image: "图像",
  audio: "音频",
  video: "视频",
};
export function AgentDetails({
  agent,
  agents,
  workflows,
  onAgent,
  onWorkflow,
}: {
  agent: Item;
  agents: Item[];
  workflows: Item[];
  onAgent: (id: string) => void;
  onWorkflow?: (id: string) => void;
}) {
  const referenced = workflows.filter((workflow) =>
    workflow.nodes?.some((node: Item) => node.agent_id === agent.id),
  );
  return (
    <>
      <div className="asset-description">
        <Bot size={23} />
        <div>
          <h3>
            {agent.purpose === "general" ? "通用能力" : "任务专用能力"} ·{" "}
            {agent.structure === "atomic" ? "元智能体" : "复合智能体"}
          </h3>
          <p>{agent.description || "尚未填写职责说明"}</p>
        </div>
      </div>
      <div className="asset-summary-grid">
        <section>
          <span>执行方式</span>
          <strong>
            {agent.execution_strategy === "tool_agent"
              ? "自主选择授权工具"
              : "固定执行能力"}
          </strong>
          <small>
            {agent.model_policy === "local"
              ? "本地确定性处理"
              : "统一网关 · 计入累计预算"}
          </small>
        </section>
        <section>
          <span>稳定发布版本</span>
          <strong>
            {agent.current_version ? `v${agent.current_version}` : "尚未发布"}
          </strong>
          <small>任务绑定不可变快照</small>
        </section>
        <section>
          <span>输入模态</span>
          <strong>
            {(agent.modalities || [])
              .map((value: string) => modalities[value] || value)
              .join("、") || "未声明"}
          </strong>
          <small>实际支持情况由能力目录验证</small>
        </section>
      </div>
      <h3>任务能力</h3>
      <div className="tags">
        {agent.capabilities?.map((value: string) => (
          <span key={value}>{taskTypes[value] || value}</span>
        ))}
      </div>
      <div className="asset-detail-columns">
        <section>
          <h3>工具与技能授权</h3>
          <div className="binding-list">
            {(agent.tool_ids || []).map((id: string) => (
              <div key={id}>
                <span>{taskTypes[id] || id}</span>
                <small>固定工具</small>
              </div>
            ))}
            {(agent.tool_refs || []).map((ref: Item) => (
              <div key={ref.tool_id}>
                <code>{ref.tool_id}</code>
                <small>工具 v{ref.version}</small>
              </div>
            ))}
            {(agent.skill_refs || []).map((ref: Item) => (
              <div key={ref.skill_id}>
                <code>{ref.skill_id}</code>
                <small>技能 v{ref.version}</small>
              </div>
            ))}
            {!agent.tool_ids?.length &&
              !agent.tool_refs?.length &&
              !agent.skill_refs?.length && (
                <p className="muted">未授权工具或技能。</p>
              )}
          </div>
        </section>
        <section>
          <h3>默认执行参数</h3>
          <ParameterSummary parameters={agent.parameters} />
        </section>
      </div>
      <h3>执行指令</h3>
      <pre className="prose">{agent.instructions || "未设置额外执行指令"}</pre>
      {agent.structure === "composite" && (
        <section>
          <h3>复合成员 · 固定版本</h3>
          <div className="binding-list">
            {(agent.members || []).map((member: Item) => {
              const definition = agents.find(
                (item) => item.id === member.agent_id,
              );
              return (
                <div key={member.agent_id}>
                  <button
                    className="link-button"
                    disabled={!definition}
                    onClick={() => onAgent(member.agent_id)}
                  >
                    {definition?.name || member.agent_id}
                    <ArrowUpRight size={14} />
                  </button>
                  <span>v{member.version}</span>
                  {definition && <Badge status={definition.status} />}
                </div>
              );
            })}
          </div>
          <p className="note">
            成员版本固定，不同任务创建独立上下文；成员权限受复合边界约束。
          </p>
        </section>
      )}
      <section>
        <h3>直接引用此定义的流程 · {referenced.length}</h3>
        {referenced.length ? (
          <div className="binding-list">
            {referenced.map((workflow) => (
              <div key={workflow.id}>
                {onWorkflow ? (
                  <button
                    className="link-button"
                    onClick={() => onWorkflow(workflow.id)}
                  >
                    {workflow.name}
                    <ArrowUpRight size={14} />
                  </button>
                ) : (
                  <span>{workflow.name}</span>
                )}
                <span>v{workflow.current_version || "—"}</span>
                <Badge status={workflow.status} />
              </div>
            ))}
          </div>
        ) : (
          <p className="muted">
            当前项目暂无流程直接引用此定义。复合成员间接引用仍由后端执行保护。
          </p>
        )}
      </section>
      <JsonView value={agent} label="完整定义与标识" />
    </>
  );
}

function VersionValue({ field, value }: { field: string; value: any }) {
  if (value === undefined || value === null || value === "")
    return <span className="muted">未设置</span>;
  if (["capabilities", "tool_ids"].includes(field))
    return (
      <span>
        {value.map((item: string) => taskTypes[item] || item).join("、") ||
          "无"}
      </span>
    );
  if (field === "modalities")
    return (
      <span>
        {value.map((item: string) => modalities[item] || item).join("、") ||
          "无"}
      </span>
    );
  if (field === "parameters") return <ParameterSummary parameters={value} />;
  if (typeof value === "object")
    return (
      <pre className="version-value">{JSON.stringify(value, null, 2)}</pre>
    );
  const names: Record<string, string> = {
    local: "本地工具",
    cloud: "统一网关模型",
    general: "通用",
    specialized: "任务专用",
    atomic: "元智能体",
    composite: "复合智能体",
    fixed: "固定执行能力",
    tool_agent: "自主工具型",
  };
  return <span className="version-text">{names[value] || String(value)}</span>;
}

export function AgentVersions({
  versions,
  currentVersion,
}: {
  versions: Item[];
  currentVersion: number;
}) {
  const ordered = [...versions].sort((a, b) => b.version - a.version);
  const [leftId, setLeftId] = useState("");
  const [rightId, setRightId] = useState("");
  if (!ordered.length)
    return (
      <Empty
        title="尚无发布版本"
        description="发布后保存不可变快照，可供流程与任务引用。"
      />
    );
  const left =
    ordered.find((item) => item.id === leftId) || ordered[1] || ordered[0];
  const right = ordered.find((item) => item.id === rightId) || ordered[0];
  const changes = changedVersionFields(left, right);
  return (
    <>
      {ordered.length > 1 && (
        <section className="version-comparison">
          <h3>版本差异</h3>
          <div className="form-grid">
            <Field label="对比基线">
              <select
                value={left.id}
                onChange={(event) => setLeftId(event.target.value)}
              >
                {ordered.map((version) => (
                  <option key={version.id} value={version.id}>
                    v{version.version} · {time(version.created_at)}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="目标版本">
              <select
                value={right.id}
                onChange={(event) => setRightId(event.target.value)}
              >
                {ordered.map((version) => (
                  <option key={version.id} value={version.id}>
                    v{version.version} · {time(version.created_at)}
                  </option>
                ))}
              </select>
            </Field>
          </div>
          {changes.length ? (
            <div className="version-diff-list">
              {changes.map((field) => (
                <div className="version-diff" key={field}>
                  <h4>{versionFields[field]}</h4>
                  <div>
                    <section>
                      <small>v{left.version}</small>
                      <VersionValue field={field} value={left[field]} />
                    </section>
                    <section>
                      <small>v{right.version}</small>
                      <VersionValue field={field} value={right[field]} />
                    </section>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <p className="note">
              两个版本的职责、能力、指令、授权、成员和参数相同。
            </p>
          )}
        </section>
      )}
      {ordered.map((version) => (
        <article className="record" key={version.id}>
          <div className="row-between">
            <h3>
              v{version.version}{" "}
              {version.version === currentVersion && (
                <span className="current-version">当前稳定版本</span>
              )}
            </h3>
            <span className="muted">{time(version.created_at)}</span>
          </div>
          <div className="tags">
            {version.capabilities?.map((id: string) => (
              <span key={id}>{taskTypes[id] || id}</span>
            ))}
            <span>{version.members?.length || 0} 个成员</span>
            <span>
              {(version.tool_ids?.length || 0) +
                (version.tool_refs?.length || 0)}{" "}
              项工具授权
            </span>
          </div>
          <ParameterSummary parameters={version.parameters} />
          <JsonView value={version} label="不可变配置快照与引用" />
        </article>
      ))}
    </>
  );
}
