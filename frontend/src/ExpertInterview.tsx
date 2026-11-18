import { useState, type FormEvent } from "react";
import { api, post } from "./api";
import { Modal, ErrorLine } from "./ui";
import { taskTypes } from "./domain";
import type { Item } from "./types";

export const businessLabels: Record<string, string> = {
  general: "通用",
  science: "科技研究",
  historical: "战场历史档案",
  public_sources: "公开情报资料",
};
export function businessLabel(a: Item) {
  const selected = a.management_profile?.business;
  if (selected && businessLabels[selected]) return businessLabels[selected];
  if (a.parameters?.case_scope?.startsWith("T")) return businessLabels.science;
  return "待分类";
}
export default function ExpertInterview({
  projectId,
  onClose,
  onSaved,
}: {
  projectId: string;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [confirmationId] = useState(() => crypto.randomUUID());
  const [answers, setAnswers] = useState({
    name: "",
    reason: "",
    business: "general",
    deliverable: "excerpts",
  });
  const [draft, setDraft] = useState<Item | null>(null);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  async function submit(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      if (draft) {
        await post("/v2/expert-design/confirm", {
          ...answers,
          project_id: projectId,
          confirmation_id: confirmationId,
        });
        onSaved();
      } else
        setDraft(
          await api("/v2/expert-design/preview", {
            method: "POST",
            body: JSON.stringify({ ...answers, project_id: projectId }),
          }),
        );
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal title="问答式创建智能体" onClose={onClose}>
      <form onSubmit={submit} className="expert-interview">
        {draft ? (
          <>
            <h3>{draft.name}</h3>
            <p>{draft.description}</p>
            <dl>
              <dt>业务方向</dt>
              <dd>{businessLabels[answers.business]}</dd>
              <dt>执行方式</dt>
              <dd>本地 · {taskTypes[draft.capabilities[0]]}</dd>
              <dt>绑定工具</dt>
              <dd>{draft.tool_ids.join("、")}</dd>
            </dl>
            <button
              type="button"
              disabled={busy}
              onClick={() => setDraft(null)}
            >
              修改回答
            </button>
          </>
        ) : (
          <>
            <label>
              为什么需要这个智能体？
              <textarea
                required
                minLength={2}
                maxLength={500}
                value={answers.reason}
                onChange={(e) =>
                  setAnswers({ ...answers, reason: e.target.value })
                }
              />
            </label>
            <label>
              为哪类业务工作？
              <select
                value={answers.business}
                onChange={(e) =>
                  setAnswers({ ...answers, business: e.target.value })
                }
              >
                {Object.entries(businessLabels).map(([id, label]) => (
                  <option key={id} value={id}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
            <label>
              它需要交付什么？
              <select
                value={answers.deliverable}
                onChange={(e) =>
                  setAnswers({ ...answers, deliverable: e.target.value })
                }
              >
                <option value="excerpts">从文本提取原文摘要</option>
                <option value="records">整理结构化记录</option>
                <option value="transcription">提取图片上的文字</option>
                <option value="image_properties">检查图像质量与基本属性</option>
              </select>
            </label>
            <label>
              名称（可选）
              <input
                maxLength={100}
                value={answers.name}
                onChange={(e) =>
                  setAnswers({ ...answers, name: e.target.value })
                }
              />
            </label>
          </>
        )}
        {error && <ErrorLine>{error}</ErrorLine>}
        <button type="submit" className="primary" disabled={busy}>
          {busy ? "处理中…" : draft ? "确认并保存草稿" : "生成配置预览"}
        </button>
      </form>
    </Modal>
  );
}
