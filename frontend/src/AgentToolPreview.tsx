import { useState } from "react";
import { post } from "./api";
import type { Item } from "./types";
import { Field, JsonView } from "./ui";

function itemText(row: Item) {
  for (const value of [
    row?.quote,
    row?.after?.quote,
    row?.before?.quote,
    row?.operation,
  ]) {
    if (typeof value === "string" && value) return value;
  }
  return JSON.stringify(row)?.slice(0, 700);
}

function argumentsFor(
  spec: Item,
  ids: string[],
  assets: Item[],
  previous = "",
) {
  let old: Item = {};
  try {
    const parsed = JSON.parse(previous);
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed))
      old = parsed;
  } catch {
    /* Build a fresh template when the prior input was incomplete. */
  }
  const next: Item = {};
  let sourceIndex = 0;
  for (const [key, raw] of Object.entries(
    spec.input_schema?.properties || {},
  )) {
    const field = raw as Item,
      required = spec.input_schema.required?.includes(key);
    if (/^(?:source_id|.+_source_id)$/.test(key)) {
      const source = ids[sourceIndex++];
      if (source || required) next[key] = source || "";
    } else if (key === "page" || key === "other_page") {
      const asset = assets.find((a) => a.id === ids[key === "page" ? 0 : 1]);
      if (asset?.media_type?.startsWith("application/pdf") || required)
        next[key] = old[key] ?? 1;
    } else if (Object.hasOwn(old, key)) next[key] = old[key];
    else if (field.default != null) next[key] = field.default;
    else if (required)
      next[key] =
        field.enum?.[0] ??
        (field.type === "integer" || field.type === "number"
          ? (field.minimum ?? (field.exclusiveMinimum ?? -1) + 1)
          : field.type === "array"
            ? []
            : field.type === "object"
              ? {}
              : "");
  }
  return JSON.stringify(next, null, 2);
}

export default function AgentToolPreview({
  agent,
  version,
  specs,
  assets,
}: {
  agent: Item;
  version: Item;
  specs: Item[];
  assets: Item[];
}) {
  const [toolId, setToolId] = useState(specs[0].id);
  const tool = specs.find((t) => t.id === toolId) || specs[0];
  const [ids, setIds] = useState<string[]>([]);
  const [args, setArgs] = useState(() => argumentsFor(tool, [], assets));
  const [reply, setReply] = useState<Item | null>(null),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  const available = assets.filter(
    (a) =>
      a.project_id === agent.project_id &&
      a.status === "active" &&
      (!a.expires_at || Date.parse(a.expires_at) > Date.now()),
  );
  const currentIds = ids.filter((id) => available.some((a) => a.id === id));
  const visible =
    reply &&
    (reply.source_refs || []).every((id: string) =>
      available.some((a) => a.id === id),
    )
      ? reply
      : null;
  function selectTool(id: string) {
    const item = specs.find((t) => t.id === id);
    if (!item) return;
    setToolId(id);
    setArgs(argumentsFor(item, currentIds, available));
    setReply(null);
    setError("");
  }
  function selectSource(id: string, enabled: boolean) {
    const next = enabled
      ? [...currentIds, id]
      : currentIds.filter((ref) => ref !== id);
    setIds(next);
    setArgs(argumentsFor(tool, next, available, args));
    setReply(null);
    setError("");
  }
  async function execute() {
    setReply(null);
    setError("");
    setBusy(true);
    try {
      const parsed = JSON.parse(args);
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed))
        throw Error("参数应为 JSON 对象");
      const result = await post(
        "/v2/agents/" +
          encodeURIComponent(agent.id) +
          "/tools/" +
          encodeURIComponent(tool.id) +
          "/preview",
        { version: version.version, input_refs: currentIds, arguments: parsed },
      );
      setReply(result);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <details className="tool-test">
      <summary>试用此版本的本地工具</summary>
      <p className="muted">
        智能体 v{version.version} · 工具 v{tool.version} ·
        本机只读调用。选择本项目资料后执行，系统记录参数和返回值的摘要供审计。
      </p>
      <Field label="试用工具">
        <select
          disabled={busy}
          value={tool.id}
          onChange={(e) => selectTool(e.target.value)}
        >
          {specs.map((s) => (
            <option key={s.id} value={s.id}>
              {s.label} · {s.id}
            </option>
          ))}
        </select>
      </Field>
      <fieldset disabled={busy}>
        <legend>试用资料（1 至 4 份）</legend>
        <div className="tool-test-sources">
          {available.map((a) => (
            <label key={a.id}>
              <input
                type="checkbox"
                checked={currentIds.includes(a.id)}
                disabled={!currentIds.includes(a.id) && currentIds.length >= 4}
                onChange={(e) => selectSource(a.id, e.target.checked)}
              />
              <span>
                {a.name}
                <small>
                  {a.media_type} · {a.source_version}
                </small>
              </span>
            </label>
          ))}
          {!available.length && (
            <p className="muted">当前项目没有可用资料。请先登记或上传原件。</p>
          )}
        </div>
      </fieldset>
      <Field
        label="调用参数（JSON）"
        hint="选择资料后会填入对应 ID。请按工具输入范围调整参数。"
      >
        <textarea
          disabled={busy}
          rows={6}
          value={args}
          onChange={(e) => {
            setArgs(e.target.value);
            setReply(null);
            setError("");
          }}
        />
      </Field>
      <JsonView value={tool.input_schema} label="参数契约和取值范围" />
      <button
        type="button"
        className="primary"
        disabled={busy || !currentIds.length}
        onClick={() => void execute()}
      >
        {busy ? "执行中" : "执行本地试用"}
      </button>
      {error && (
        <p className="error-line" role="alert">
          {error}
        </p>
      )}
      {visible && (
        <div className="tool-result" aria-live="polite">
          <strong>本地试用完成</strong>
          <p className="muted">
            {tool.label} · 实际读取 {visible.source_refs?.length || 0} 份资料 ·
            摘要 {visible.checksum?.slice(0, 16)}
          </p>
          {visible.result?.status === "native_text_unavailable" && (
            <p>所选页没有可抽取的原生文字。</p>
          )}
          {typeof visible.result?.quote === "string" && (
            <blockquote>{visible.result.quote}</blockquote>
          )}
          {Array.isArray(visible.result?.items) && (
            <ul>
              {visible.result.items.slice(0, 5).map((row: Item, i: number) => (
                <li key={i}>
                  {itemText(row)}{" "}
                  {typeof row?.page === "number" && (
                    <small>第 {row.page} 页</small>
                  )}
                </li>
              ))}
            </ul>
          )}
          <JsonView value={visible} label="完整工具结果和来源" />
        </div>
      )}
    </details>
  );
}
