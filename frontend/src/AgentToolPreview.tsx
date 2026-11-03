import { useEffect, useState } from "react";
import { post } from "./api";
import type { Item } from "./types";
import { Field, JsonView } from "./ui";
import {
  argumentTemplate,
  canPreviewSource,
  checkArguments,
  fieldSchema,
  isSourceField,
  parameterNames,
  parsePreviewArguments,
} from "./agentPreviewModel";

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

const metricNames: Record<string, string> = {
  total_rows: "检查行数",
  missing_key_count: "缺少键的行数",
  duplicate_groups: "重复组数",
  conflict_groups: "冲突组数",
  scope: "统计范围",
  detail_count: "明细数",
  computed_total: "复算合计",
  reported_total: "原表合计",
  difference: "差值",
  unit: "单位",
  target_year: "目标年",
  target: "目标值",
  baseline_mean: "基线均值",
  percent: "相对基线变化（%）",
  ascending_rank: "从小到大排名",
  metric_crs: "面积投影",
  left_area: "主资料面积",
  right_area: "对照资料面积",
  intersection: "相交面积",
  left_exposed_percent: "主区域暴露比例（%）",
  snapshot: "路网时点",
  before: "前期",
  after: "后期",
  valid_pixels: "有效像元数",
  unknown_pixels: "未知像元数",
};

function PreviewMetrics({ result }: { result: Item }) {
  const values = Object.entries(result).filter(
    ([k, v]) => metricNames[k] && ["string", "number"].includes(typeof v),
  );
  if (!values.length) return null;
  return (
    <dl className="tool-preview-metrics">
      {values.flatMap(([key, value]) => [
        <dt key={key + "-key"}>{metricNames[key]}</dt>,
        <dd key={key}>{String(value)}</dd>,
      ])}
    </dl>
  );
}

function ArgumentField({
  name,
  property,
  required,
  value,
  sources,
  change,
}: {
  name: string;
  property: Item;
  required: boolean;
  value: any;
  sources: Item[];
  change: (value: any) => void;
}) {
  const field = fieldSchema(property);
  const label = parameterNames[name] || name;
  let control;
  if (isSourceField(name)) {
    control = (
      <select
        value={value || ""}
        onChange={(e) => change(e.target.value || undefined)}
      >
        <option value="">{required ? "请选择已勾选的资料" : "不指定"}</option>
        {sources.map((s) => (
          <option value={s.id} key={s.id}>
            {s.name}
          </option>
        ))}
      </select>
    );
  } else if (field.type === "integer" || field.type === "number") {
    control = (
      <input
        type="number"
        value={value ?? ""}
        min={
          field.minimum ??
          (field.type === "integer" && field.exclusiveMinimum != null
            ? field.exclusiveMinimum + 1
            : undefined)
        }
        max={field.maximum}
        step={field.type === "integer" ? 1 : "any"}
        onChange={(e) =>
          change(e.target.value === "" ? undefined : Number(e.target.value))
        }
      />
    );
  } else if (field.type === "array") {
    control = (
      <input
        value={Array.isArray(value) ? value.join(",") : ""}
        placeholder="多个字段用逗号分隔"
        onChange={(e) =>
          change(
            e.target.value
              ? e.target.value.split(/[,，]/).map((s) => s.trim())
              : undefined,
          )
        }
      />
    );
  } else if (field.type === "object") {
    control = (
      <textarea
        rows={2}
        value={typeof value === "object" ? JSON.stringify(value) : value || ""}
        placeholder={'{"kind":"observation"}'}
        onChange={(e) => {
          const v = e.target.value;
          try {
            change(v ? parsePreviewArguments(v) : undefined);
          } catch {
            change(v);
          }
        }}
      />
    );
  } else {
    control = (
      <input
        value={value ?? ""}
        maxLength={field.maxLength}
        onChange={(e) => change(e.target.value || undefined)}
      />
    );
  }
  return (
    <Field
      label={label}
      hint={[required ? "必填" : "选填", field.description || name].join(" · ")}
    >
      {control}
    </Field>
  );
}

export default function AgentToolPreview({
  agent,
  version,
  specs,
  assets,
  initialToolId,
}: {
  agent: Item;
  version: Item;
  specs: Item[];
  assets: Item[];
  initialToolId?: string | null;
}) {
  const [toolId, setToolId] = useState(
    specs.find((s) => s.id === initialToolId)?.id || specs[0].id,
  );
  const tool = specs.find((t) => t.id === toolId) || specs[0];
  const [ids, setIds] = useState<string[]>([]);
  const [args, setArgs] = useState(() =>
    JSON.stringify(argumentTemplate(tool, [], assets), null, 2),
  );
  const [reply, setReply] = useState<Item | null>(null),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  const [clock, setClock] = useState(Date.now);
  useEffect(() => {
    const ends = assets
      .map((a) => Date.parse(a.expires_at))
      .filter((t) => t > Date.now());
    if (!ends.length) return;
    const timeout = window.setTimeout(
      () => setClock(Date.now()),
      Math.min(2147483647, Math.max(1, Math.min(...ends) - Date.now() + 1)),
    );
    return () => clearTimeout(timeout);
  }, [assets, clock]);
  const available = assets.filter((a) => canPreviewSource(a, version, tool.id));
  const currentIds = ids.filter((id) => available.some((a) => a.id === id));
  const selected = currentIds.map((id) => available.find((a) => a.id === id)!);
  const signature = JSON.stringify([
    tool.id,
    version.id,
    selected.map((a) => [a.id, a.source_version]),
  ]);
  let parsed: Item | null = null;
  try {
    parsed = parsePreviewArguments(args);
  } catch {
    /* Raw JSON can be incomplete while editing. */
  }
  const visible =
    reply?.scope === signature &&
    reply.data.tool_id === tool.id &&
    reply.data.agent_version === version.version &&
    (reply.data.source_refs || []).every((id: string) =>
      currentIds.includes(id),
    )
      ? reply.data
      : null;
  function selectTool(id: string) {
    const item = specs.find((t) => t.id === id);
    if (!item) return;
    setToolId(id);
    const kept = assets.filter(
      (a) => currentIds.includes(a.id) && canPreviewSource(a, version, item.id),
    );
    const next = currentIds.filter((id) => kept.some((a) => a.id === id));
    setIds(next);
    setArgs(JSON.stringify(argumentTemplate(item, next, kept), null, 2));
    setReply(null);
    setError("");
  }
  function selectSource(id: string, enabled: boolean) {
    const next = enabled
      ? [...currentIds, id]
      : currentIds.filter((ref) => ref !== id);
    setIds(next);
    setArgs(
      JSON.stringify(
        argumentTemplate(tool, next, available, parsed || {}),
        null,
        2,
      ),
    );
    setReply(null);
    setError("");
  }
  function changeValue(name: string, value: any) {
    const next = { ...parsed, [name]: value };
    const page =
      name === "source_id"
        ? "page"
        : name === "other_source_id"
          ? "other_page"
          : "";
    if (page && Object.hasOwn(tool.input_schema?.properties || {}, page)) {
      if (
        selected
          .find((a) => a.id === value)
          ?.media_type?.startsWith("application/pdf")
      )
        next[page] ??= 1;
      else delete next[page];
    }
    setArgs(JSON.stringify(next, null, 2));
    setReply(null);
    setError("");
  }
  async function execute() {
    setReply(null);
    setError("");
    setBusy(true);
    try {
      const parsed = parsePreviewArguments(args);
      checkArguments(tool, parsed, currentIds);
      const result = await post(
        "/v2/agents/" +
          encodeURIComponent(agent.id) +
          "/tools/" +
          encodeURIComponent(tool.id) +
          "/preview",
        { version: version.version, input_refs: currentIds, arguments: parsed },
      );
      setReply({ data: result, scope: signature });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <details className="tool-test" open={initialToolId ? true : undefined}>
      <summary>试用此版本的本地工具</summary>
      <p className="muted">
        智能体 v{version.version} · 工具 v{tool.version} ·
        本地只读试用。选择此版本可读取的项目资料，执行后可以核对来源和返回值摘要。
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
            <p className="muted">
              没有符合此工具格式和用例岗位模态的有效资料。可在「数据资料」登记匹配的原件。
            </p>
          )}
        </div>
      </fieldset>
      {parsed && (
        <fieldset className="preview-params" disabled={busy}>
          <legend>工具参数</legend>
          <div className="preview-fields">
            {Object.entries(tool.input_schema?.properties || {}).map(
              ([name, property]) => (
                <ArgumentField
                  key={name}
                  name={name}
                  property={property as Item}
                  required={tool.input_schema.required?.includes(name)}
                  value={parsed![name]}
                  sources={selected}
                  change={(value) => changeValue(name, value)}
                />
              ),
            )}
          </div>
        </fieldset>
      )}
      <Field
        label="调用参数（JSON）"
        hint="与上面的参数同步，也可直接编辑。来源 ID 须属于勾选的资料。"
      >
        <textarea
          disabled={busy}
          rows={2}
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
          {visible.result && <PreviewMetrics result={visible.result} />}
          {Array.isArray(visible.result?.items) &&
            !visible.result.items.length && (
              <p className="note">在给定范围内没有匹配项。</p>
            )}
          {Array.isArray(visible.result?.items) &&
            visible.result.items.length > 0 && (
              <ul>
                {visible.result.items
                  .slice(0, 5)
                  .map((row: Item, i: number) => (
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
