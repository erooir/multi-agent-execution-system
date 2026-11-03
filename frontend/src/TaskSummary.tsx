import { modeName, reasonNames, taskTypes, time } from "./domain";
import { durationLabel, elapsedSeconds, qualityOutputs } from "./resultModel";
import type { Item } from "./types";
import { Badge, Empty, Panel } from "./ui";

const scopes: Record<string, string> = {
  reference: "参考用例的指定批次",
  sample: "缩小样本验证",
  project: "当前项目资料",
};

export default function TaskSummary({
  task,
  onNode,
  onArtifact,
}: {
  task: Item;
  onNode: (node: Item) => void;
  onArtifact: (artifact: Item) => void;
}) {
  const request = task.request || {};
  const snapshot = task.snapshot || {};
  const profile = snapshot.case_profile;
  const workflow = snapshot.workflow;
  const quality = task.result?.quality_verdict || task.quality_verdict;
  const issues = task.content_withheld
    ? []
    : qualityOutputs(quality).flatMap((output: Item) => output.issues || []);
  return (
    <div className="task-summary">
      <Panel title="任务信息">
        <dl className="trace-facts">
          <dt>任务 ID</dt>
          <dd className="mono">{task.id}</dd>
          <dt>任务目标</dt>
          <dd className="task-goal">{request.goal || "未记录"}</dd>
          <dt>任务类型</dt>
          <dd>
            {taskTypes[request.task_type] || request.task_type || "未记录"}
            {request.case_id && ` · ${request.case_id} ${profile?.title || ""}`}
          </dd>
          <dt>执行模式</dt>
          <dd>{modeName(request.mode)}</dd>
          {request.task_type === "case_analysis" && (
            <>
              <dt>验收范围</dt>
              <dd>
                {scopes[request.case_scope] || request.case_scope || "未记录"}
              </dd>
              {request.case_scope === "reference" && (
                <>
                  <dt>参考批次</dt>
                  <dd>
                    {profile?.reference_batch?.state === "registered"
                      ? `已登记 · ${profile.reference_batch.entry?.version || ""}`
                      : "未登记或不可用，不能作为完整参考验收通过"}
                  </dd>
                </>
              )}
            </>
          )}
          <dt>执行流程</dt>
          <dd>
            {workflow
              ? `${workflow.name || workflow.definition_id || workflow.id} · v${workflow.version ?? "未记录"}`
              : "任务级自动匹配流程"}
          </dd>
          <dt>创建时间</dt>
          <dd>{time(task.created_at)}</dd>
          <dt>首次执行</dt>
          <dd>{time(task.first_claimed_at)}</dd>
          <dt>结束时间</dt>
          <dd>{task.finished_at ? time(task.finished_at) : "尚未结束"}</dd>
          <dt>任务时效</dt>
          <dd>
            {request.service_class === "interactive" ? "交互任务" : "批量任务"}{" "}
            · 截止时限 {request.deadline_seconds ?? "—"} 秒
          </dd>
          <dt>资源与质量要求</dt>
          <dd>
            预算上限 ¥{request.max_cost_cny ?? 0} · 最多修订 / 恢复{" "}
            {request.max_revisions ?? 0} 轮 ·{" "}
            {request.allow_partial ? "允许部分交付" : "要求完整交付"}
          </dd>
          <dt>最低交付要求</dt>
          <dd>
            记录 {request.quality?.min_records ?? "未指定"} 条 · 文本{" "}
            {request.quality?.min_length ?? "未指定"} 字符 · 必需字段{" "}
            {request.quality?.required_fields?.join("、") || "未指定"}
          </dd>
        </dl>
      </Panel>
      <Panel title="输入资料">
        {snapshot.inputs?.length ? (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>资料</th>
                  <th>格式 / 授权</th>
                  <th>来源与版本</th>
                </tr>
              </thead>
              <tbody>
                {snapshot.inputs.map((asset: Item) => (
                  <tr key={asset.id}>
                    <td>
                      <strong>{asset.name || asset.id}</strong>
                      <small className="mono">{asset.id}</small>
                    </td>
                    <td>
                      {asset.media_type || asset.modality}
                      <small>
                        {asset.classification === "public"
                          ? "公开 · 可按授权外发"
                          : "仅本地"}
                      </small>
                    </td>
                    <td>
                      {asset.source_id || "未记录"}
                      <small className="mono">
                        {asset.source_version || "未记录版本"}
                      </small>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty title="未记录输入资料" />
        )}
      </Panel>
      <Panel title="执行结果与问题">
        <Badge status={task.status} />
        {task.reason_code && (
          <p>{reasonNames[task.reason_code] || task.reason_code}</p>
        )}
        {task.content_withheld ? (
          <p className="warning">
            来源已撤回或过期，执行内容和审核问题已屏蔽。
          </p>
        ) : (
          <>
            {issues.map((issue: Item | string, index: number) => (
              <p className="warning" key={index}>
                {typeof issue === "string"
                  ? issue
                  : issue.message || JSON.stringify(issue)}
              </p>
            ))}
            {!quality && <p className="muted">尚未生成质量判定。</p>}
            {task.artifacts?.map((artifact: Item) => (
              <div className="artifact-row" key={artifact.id}>
                <span>{artifact.name || artifact.type}</span>
                <Badge status={artifact.quality_status || "unknown"} />
                <button
                  disabled={artifact.content_withheld}
                  onClick={() => onArtifact(artifact)}
                >
                  查看产物详情
                </button>
              </div>
            ))}
          </>
        )}
      </Panel>
      <Panel title="步骤详情">
        {task.nodes?.length ? (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>执行步骤</th>
                  <th>轮次 / 状态</th>
                  <th>耗时</th>
                  <th>详情</th>
                </tr>
              </thead>
              <tbody>
                {task.nodes.map((node: Item) => (
                  <tr key={node.id}>
                    <td>
                      {node.label || node.node_id}
                      <small>智能体 v{node.agent_version ?? "—"}</small>
                    </td>
                    <td>
                      第 {node.attempt ?? 1} 轮 <Badge status={node.status} />
                    </td>
                    <td>
                      {durationLabel(
                        elapsedSeconds(node.started_at, node.finished_at),
                      )}
                    </td>
                    <td>
                      <button
                        disabled={task.content_withheld}
                        onClick={() => onNode(node)}
                      >
                        查看步骤详情
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty
            title="尚无步骤记录"
            description="执行器开始处理后，将显示每一步的状态、耗时与详情。"
          />
        )}
      </Panel>
    </div>
  );
}
