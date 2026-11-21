import { useRef, useState, type FormEvent } from "react";
import { post } from "./api";
import type { Item } from "./types";
import { ErrorLine, Modal } from "./ui";

export const businessLabels: Record<string, string> = {
  general: "通用",
  battlefield: "战场情报",
  science: "科技情报",
  geographic: "地理情报",
};

export function businessKey(agent: Item) {
  const selected = agent.management_profile?.business;
  if (selected === "historical") return "battlefield";
  if (selected && Object.hasOwn(businessLabels, selected)) return selected;
  if (agent.parameters?.case_scope?.startsWith("T")) return "science";
  return "general";
}

export function businessLabel(agent: Item) {
  return businessLabels[businessKey(agent)];
}

const example = "请创建一个月报核对智能体。它负责复算明细和合计，标出差异及原文位置，最后输出一份问题清单。";

export default function ExpertInterview({
  projectId,
  onClose,
  onSaved,
}: {
  projectId: string;
  onClose: () => void;
  onSaved: (agent: Item) => void;
}) {
  const [text, setText] = useState(example);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const request = useRef<{ text: string; id: string } | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    const prompt = text.trim();
    if (request.current?.text !== prompt) request.current = { text: prompt, id: crypto.randomUUID() };
    setBusy(true);
    setError("");
    try {
      const saved = await post("/v2/expert-design/create", {
        project_id: projectId,
        prompt,
        idempotency_key: request.current.id,
      });
      onSaved(saved);
    } catch (failure) {
      setError((failure as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title="对话创建智能体" onClose={() => { if (!busy) onClose(); }}>
      <form className="expert-interview" onSubmit={submit}>
        <label>
          描述智能体的职责和预期交付
          <textarea value={text} onChange={(event) => setText(event.target.value)}
            maxLength={5000} rows={6} required disabled={busy}/>
        </label>
        {error && <ErrorLine>{error}</ErrorLine>}
        <div className="expert-interview-actions">
          <button className="primary" type="submit" disabled={busy || text.trim().length < 5}>
            {busy ? "正在创建…" : "发送并创建"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
