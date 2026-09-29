import { useEffect, useState } from "react";
import { ArrowRight, GitBranch, History } from "lucide-react";
import { api, time, type RecordData } from "./api";
import type { PageProps } from "./types";
import {
  instanceStatusNames,
  lifecycleActionNames,
  lifecycleEventDetail,
  lifecycleNames,
  lifecycleStatus,
  releaseReasonNames,
} from "./agentLifecycle";
import { Badge, Empty, InlineMessage, Modal, ModeBadge } from "./ui";

type LifecycleData = {
  agent: RecordData;
  workflows: RecordData[];
  instances: RecordData[];
  events: RecordData[];
};

export function AgentLifecycleDetails({
  id,
  p,
  onClose,
}: {
  id: string;
  p: PageProps;
  onClose: () => void;
}) {
  const [detail, setDetail] = useState<LifecycleData | null>(null);
  const [error, setError] = useState("");
  const [tab, setTab] = useState("workflows");
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const refresh = async () => {
      try {
        const data = await api<LifecycleData>(
          `/agents/${encodeURIComponent(id)}/lifecycle`,
          {
            signal: controller.signal,
          },
        );
        if (!controller.signal.aborted) {
          setDetail(data);
          setError("");
        }
      } catch (e) {
        if (!controller.signal.aborted) setError((e as Error).message);
      } finally {
        if (!controller.signal.aborted) timer = setTimeout(refresh, 4000);
      }
    };
    void refresh();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [id]);

  const agent = detail?.agent;
  return (
    <Modal
      title={`${agent?.name || "智能体"} · 生命周期`}
      onClose={onClose}
      wide
    >
      {error && (
        <InlineMessage error>
          读取最新状态失败：{error}。页面会自动重试。
        </InlineMessage>
      )}
      {!detail ? (
        !error && <p className="muted">正在读取生命周期记录…</p>
      ) : (
        <>
          <div className="lifecycle-summary">
            <Badge
              status={
                lifecycleStatus(agent!) === "active" ? "ready" : "cancelled"
              }
            >
              {lifecycleNames[lifecycleStatus(agent!)]}
            </Badge>
            <span>当前配置 v{agent!.version || 1}</span>
            <span>
              {agent!.origin === "built_in" ? "系统内置" : "自建智能体"}
            </span>
            <span>{agent!.usage?.workflow_count || 0} 个流程引用</span>
            <span>
              {agent!.usage?.active_instance_count || 0} 个未释放上下文
            </span>
          </div>
          <p className="lifecycle-description">
            多个流程可绑定同一个智能体，复用职责、指令与技能配置。每次任务使用独立的执行上下文与配置快照，任务之间不共享对话记忆。这里的上下文记录用于追踪执行，并非持续驻留的模型进程。
          </p>
          {agent!.cloned_from && (
            <p className="muted">
              源自独立复制 ·{" "}
              {p.data.agents.find(
                (item: RecordData) => item.id === agent!.cloned_from,
              )?.name || agent!.cloned_from}
            </p>
          )}
          <div
            className="tabs lifecycle-tabs"
            role="tablist"
            aria-label="生命周期详情"
          >
            {[
              ["workflows", `流程复用 ${detail.workflows.length}`],
              ["instances", `执行上下文 ${detail.instances.length}`],
              ["events", `生命周期记录 ${detail.events.length}`],
            ].map(([key, label]) => (
              <button
                key={key}
                role="tab"
                aria-selected={tab === key}
                className={tab === key ? "active" : ""}
                onClick={() => setTab(key)}
              >
                {label}
              </button>
            ))}
          </div>
          {tab === "workflows" && (
            <div className="lifecycle-list">
              <p className="muted">
                在流程画布的“执行智能体”中选择已有角色，即可跨流程复用；需要独立调整职责时，可先创建副本。销毁前需解除以下流程绑定。
              </p>
              {detail.workflows.length ? (
                detail.workflows.map((workflow) => (
                  <article className="lifecycle-row" key={workflow.id}>
                    <div>
                      <strong>
                        <GitBranch size={16} />
                        {workflow.name}
                      </strong>
                      <p>
                        {(workflow.nodes || [])
                          .map((node: RecordData) => node.label || node.id)
                          .join("、")}
                      </p>
                    </div>
                    <button
                      className="button small"
                      onClick={async () => {
                        try {
                          const record = p.data.workflows.find(
                            (item: RecordData) => item.id === workflow.id,
                          );
                          if (!record)
                            throw new Error(
                              "该流程已不在当前列表，请关闭后刷新重试",
                            );
                          onClose();
                          p.editWorkflow(record);
                        } catch (e) {
                          p.notify((e as Error).message, true);
                        }
                      }}
                    >
                      查看流程
                      <ArrowRight size={14} />
                    </button>
                  </article>
                ))
              ) : (
                <Empty
                  title="尚无流程引用"
                  detail="在流程画布中选择该智能体，即可开始复用。"
                />
              )}
            </div>
          )}
          {tab === "instances" && (
            <div className="lifecycle-list">
              <p className="muted">
                显示最近 100
                条上下文记录。等待审核、完成、失败或取消时释放执行上下文，历史记录保留；审核后需要继续执行时会创建新的上下文。
              </p>
              {detail.instances.length ? (
                detail.instances.map((instance) => (
                  <article className="lifecycle-instance" key={instance.id}>
                    <div className="lifecycle-row">
                      <div>
                        <strong>{instance.workflow_name || "任务执行"}</strong>
                        <p>
                          配置 v{instance.agent_version} · 使用{" "}
                          {instance.use_count || 0} 次 ·{" "}
                          {(instance.node_ids || []).length} 个节点
                        </p>
                      </div>
                      <Badge
                        status={
                          instance.status === "running"
                            ? "running"
                            : instance.status === "released"
                              ? "cancelled"
                              : "pending"
                        }
                      >
                        {instanceStatusNames[instance.status] ||
                          instance.status}
                      </Badge>
                    </div>
                    <div className="lifecycle-instance-meta">
                      <ModeBadge mode={instance.mode} />
                      <span>创建于 {time(instance.created_at)}</span>
                      {instance.released_at && (
                        <span>释放于 {time(instance.released_at)}</span>
                      )}
                    </div>
                    {instance.release_reason && (
                      <p className="muted">
                        释放原因：
                        {releaseReasonNames[instance.release_reason] ||
                          instance.release_reason}
                      </p>
                    )}
                    <div className="lifecycle-row">
                      <code className="lifecycle-id">{instance.id}</code>
                      <button
                        className="button small"
                        onClick={() => {
                          onClose();
                          p.go("runs", instance.run_id);
                        }}
                      >
                        查看任务
                        <ArrowRight size={14} />
                      </button>
                    </div>
                  </article>
                ))
              ) : (
                <Empty
                  title="尚无执行上下文记录"
                  detail="此功能启用后，新启动或显式重试的任务会记录上下文生命周期；历史任务不会补造实例。"
                />
              )}
            </div>
          )}
          {tab === "events" && (
            <div className="lifecycle-list">
              <p className="muted">最近 100 条配置及上下文变更记录。</p>
              {detail.events.length ? (
                detail.events.map((event, index) => (
                  <article
                    className="lifecycle-event"
                    key={event.id || `${event.created_at}-${index}`}
                  >
                    <History size={17} />
                    <div>
                      <strong>
                        {lifecycleActionNames[event.action] || event.action}
                      </strong>
                      <p>{lifecycleEventDetail(event.detail)}</p>
                      <small>
                        {time(event.created_at)} · {event.user || "system"}
                      </small>
                    </div>
                    {event.run_id && (
                      <button
                        className="button small"
                        onClick={() => {
                          onClose();
                          p.go("runs", event.run_id);
                        }}
                      >
                        查看任务
                      </button>
                    )}
                  </article>
                ))
              ) : (
                <Empty
                  title="尚无生命周期记录"
                  detail="此功能启用后的创建、配置变更和执行上下文事件会显示在这里。"
                />
              )}
            </div>
          )}
        </>
      )}
    </Modal>
  );
}
