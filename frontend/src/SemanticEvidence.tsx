import { useState } from "react";
import { post } from "./api";
import { Field, Panel } from "./ui";
import type { Item } from "./types";

export default function SemanticEvidence({
  projectId,
  assets,
  tasks,
  offline,
  onTask,
}: {
  projectId: string;
  assets: Item[];
  tasks: Item[];
  offline: boolean;
  onTask: (id: string) => void;
}) {
  const [refs, setRefs] = useState<string[]>([]),
    [linked, setLinked] = useState(""),
    [focus, setFocus] = useState("claim_check"),
    [limit, setLimit] = useState(0.5),
    [approved, setApproved] = useState(false),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [key, setKey] = useState(() => crypto.randomUUID());
  const usable = assets.filter(
    (a) =>
      a.status === "active" &&
      a.classification === "public" &&
      a.parsed_content_available &&
      ["structured", "text"].includes(a.modality),
  );
  const change = () => {
    setApproved(false);
    setKey(crypto.randomUUID());
    setError("");
  };
  return (
    <Panel title="公开资料语义取证">
      <p>
        由网关模型阅读明确授权的资料，生成带引用的中文报告，并单独评审。数值与空间产物仍以可复算工具为准。
      </p>
      {offline ? (
        <p className="note">当前为离线部署，付费取证不可用。</p>
      ) : (
        <>
          <Field label="关联已完成用例">
            <select
              value={linked}
              onChange={(e) => {
                change();
                setLinked(e.target.value);
                setRefs(
                  tasks.find((t) => t.id === e.target.value)?.request
                    .input_refs || [],
                );
              }}
            >
              <option value="">独立核查所选资料</option>
              {tasks
                .filter(
                  (t) =>
                    t.request?.task_type === "case_analysis" &&
                    ["succeeded", "partial"].includes(t.status),
                )
                .map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.name}
                  </option>
                ))}
            </select>
          </Field>
          <Field label="取证目标">
            <select
              value={focus}
              onChange={(e) => {
                change();
                setFocus(e.target.value);
              }}
            >
              <option value="claim_check">主张与证据核对</option>
              <option value="timeline">时间线核查</option>
              <option value="comparison">版本与表述比较</option>
              <option value="scientific_terms">
                科学文献 · 术语与测量复核
              </option>
            </select>
          </Field>
          {focus === "scientific_terms" && (
            <p className="muted">
              核对所选节选的英文术语及中文含义，分别列出实测和模拟结果。节选未定义的缩写保留英文。
            </p>
          )}
          <Field label="本任务费用上限（元）">
            <input
              type="number"
              min=".01"
              max=".8"
              step=".01"
              value={limit}
              onChange={(e) => {
                change();
                setLimit(Number(e.target.value));
              }}
            />
          </Field>
          <div className="check-list">
            {usable.map((a) => (
              <label className="check" key={a.id}>
                <input
                  type="checkbox"
                  checked={refs.includes(a.id)}
                  disabled={!!linked}
                  onChange={(e) => {
                    change();
                    setRefs(
                      e.target.checked
                        ? [...refs, a.id]
                        : refs.filter((id) => id !== a.id),
                    );
                  }}
                />
                {a.name}
              </label>
            ))}
          </div>
          {linked && refs.some((id) => !usable.some((a) => a.id === id)) && (
            <p className="warning">关联用例包含不适合外发或不可读取的资料。</p>
          )}
          <label className="check">
            <input
              type="checkbox"
              checked={approved}
              onChange={(e) => setApproved(e.target.checked)}
            />
            明确授权将本次所选公开资料发送给网关模型
          </label>
          <button
            disabled={
              busy ||
              !approved ||
              !refs.length ||
              !(limit > 0 && limit <= 0.8) ||
              refs.some((id) => !usable.some((a) => a.id === id))
            }
            onClick={async () => {
              setBusy(true);
              try {
                const t = await post("/v2/casework/semantic-evidence", {
                  project_id: projectId,
                  input_refs: refs,
                  focus,
                  idempotency_key: key,
                  max_cost_cny: limit,
                  public_egress_authorized: approved,
                  case_task_id: linked || null,
                });
                onTask(t.id);
              } catch (e) {
                setError((e as Error).message);
              } finally {
                setBusy(false);
              }
            }}
          >
            提交付费取证任务
          </button>
          {error && (
            <p role="alert" className="warning">
              {error}
            </p>
          )}
        </>
      )}
    </Panel>
  );
}
