import { useEffect, useState } from "react";
import { api } from "./api";
import type { components } from "./generated/api";
import { Modal } from "./ui";

export type CaseChoice = { id: string; title: string };
export type CustomCaseScope = "project" | "reference";
export type SampleRequest = components["schemas"]["CaseDemoDraft"];
type DemoSample = components["schemas"]["CaseDemoSample"];

export function CaseDemoSample({ projectId, choice, offline, onSample, onCustom, onClose }: {
  projectId: string;
  choice: CaseChoice;
  offline: boolean;
  onSample: (sourceTask: string, request: SampleRequest) => void;
  onCustom: (scope: CustomCaseScope) => void;
  onClose: () => void;
}) {
  const [attempt, setAttempt] = useState(0);
  const [status, setStatus] = useState("loading");
  const [message, setMessage] = useState("");
  useEffect(() => {
    if (offline) return;
    const controller = new AbortController();
    setStatus("loading");
    const query = new URLSearchParams({ project_id: projectId });
    api<DemoSample>(`/v2/casework/${encodeURIComponent(choice.id)}/demo-sample?${query}`, { signal: controller.signal })
      .then((sample) => {
        if (controller.signal.aborted) return;
        if (sample.available === false) {
          setStatus("unavailable");
          return;
        }
        if (sample.available !== true || !sample.verified_task_id || !sample.request?.input_refs?.length) {
          throw new Error("样本缺少来源任务或输入资料。");
        }
        onSample(sample.verified_task_id, sample.request);
      })
      .catch((error: Error) => {
        if (!controller.signal.aborted) {
          setStatus("error");
          setMessage(error instanceof TypeError ? "无法连接平台，请检查网络连接。" : error.message);
        }
      });
    return () => controller.abort();
  }, [attempt, choice.id, offline, onSample, projectId]);
  return (
    <Modal title="提交用例任务" onClose={onClose}>
      <div className="modal-body">
        <h3>{choice.id} · {choice.title}</h3>
        {offline ? <p>当前为离线部署，模型调用不可用。可选择资料进行本地演练。</p> :
          status === "loading" ? <p role="status">正在读取本项目的演示样本…</p> :
          status === "unavailable" ? <p>本项目还没有可复用的已验证演示样本。</p> :
          <p role="alert">演示样本读取失败：{message} <button onClick={() => setAttempt((n) => n + 1)}>重试</button></p>}
        <div className="toolbar">
          <button onClick={() => onCustom("project")}>自定义项目资料</button>
          <button onClick={() => onCustom("reference")}>使用参考批次</button>
        </div>
      </div>
    </Modal>
  );
}
