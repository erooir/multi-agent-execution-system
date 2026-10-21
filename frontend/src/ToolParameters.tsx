import { useEffect, useState } from "react";
import { Field } from "./ui";
import { parseObject, splitList } from "./domain";
import type { Item } from "./types";

export function ParameterSummary({ parameters = {} }: { parameters?: Item }) {
  const names: Record<string, string> = {
    normalize: "规范整理",
    select: "选择字段",
    deduplicate: "去除重复记录",
    sort: "排序",
    filter: "筛选",
  };
  const values = [
    parameters.operation &&
      `处理方式：${names[parameters.operation] || parameters.operation}`,
    parameters.select_fields?.length &&
      `保留字段：${parameters.select_fields.join("、")}`,
    parameters.filter &&
      Object.keys(parameters.filter).length &&
      `筛选：${Object.entries(parameters.filter)
        .map(([key, value]) => `${key} = ${JSON.stringify(value)}`)
        .join("；")}`,
    parameters.sort_by &&
      `按 ${parameters.sort_by} ${parameters.descending ? "降序" : "升序"}`,
    parameters.deduplicate && "删除完全重复的记录",
    parameters.max_chars && `文本长度上限：${parameters.max_chars} 字符`,
  ].filter(Boolean);
  return values.length ? (
    <ul className="parameter-summary">
      {values.map((value) => (
        <li key={String(value)}>{value}</li>
      ))}
    </ul>
  ) : (
    <p className="muted">使用运行能力默认参数</p>
  );
}

export default function ToolParameters({
  value,
  onChange,
  structured,
  text,
  inherited = false,
}: {
  value: string;
  onChange: (value: string) => void;
  structured: boolean;
  text: boolean;
  inherited?: boolean;
}) {
  let params: Item = {},
    error = "";
  try {
    params = parseObject(value, "执行参数");
  } catch {
    error = "JSON 格式不正确，请在高级参数中修正后继续。";
  }
  const [fields, setFields] = useState((params.select_fields || []).join(", "));
  const fieldKey = JSON.stringify(params.select_fields || []);
  useEffect(() => setFields(JSON.parse(fieldKey).join(", ")), [fieldKey]);
  const update = (key: string, next: unknown) => {
    const result = { ...params };
    if (next === undefined) delete result[key];
    else result[key] = next;
    onChange(JSON.stringify(result, null, 2));
  };
  return (
    <div className="parameter-editor">
      <p className="note">
        {inherited
          ? "留空时继承智能体参数；只填写本节点需要覆盖的选项。"
          : "设置复用时的默认处理规则，具体任务会按权限与能力目录校验。"}
      </p>
      {error ? (
        <p className="error-line" role="alert">
          {error}
        </p>
      ) : (
        <>
          {structured && (
            <div className="form-grid">
              <Field label="结构化处理方式">
                <select
                  value={params.operation || ""}
                  onChange={(event) =>
                    update("operation", event.target.value || undefined)
                  }
                >
                  <option value="">
                    {inherited ? "继承智能体设置" : "默认：规范整理"}
                  </option>
                  <option value="normalize">规范整理</option>
                  <option value="select">选择字段</option>
                  <option value="deduplicate">去除重复记录</option>
                  <option value="sort">排序</option>
                  <option value="filter">筛选</option>
                </select>
              </Field>
              <Field label="保留字段" hint="逗号分隔；留空保留全部字段。">
                <input
                  value={fields}
                  placeholder="name, amount"
                  onChange={(event) => setFields(event.target.value)}
                  onBlur={() =>
                    update(
                      "select_fields",
                      fields.trim() ? splitList(fields) : undefined,
                    )
                  }
                />
              </Field>
              <Field label="排序字段" hint="按 JSON 值的字典序排序。">
                <input
                  value={params.sort_by || ""}
                  onChange={(event) =>
                    update("sort_by", event.target.value || undefined)
                  }
                />
              </Field>
              <Field label="排序方向">
                <select
                  value={
                    params.descending === undefined
                      ? ""
                      : String(params.descending)
                  }
                  onChange={(event) =>
                    update(
                      "descending",
                      event.target.value === ""
                        ? undefined
                        : event.target.value === "true",
                    )
                  }
                >
                  <option value="">
                    {inherited ? "继承智能体设置" : "默认：升序"}
                  </option>
                  <option value="false">升序</option>
                  <option value="true">降序</option>
                </select>
              </Field>
              <Field label="重复记录">
                <select
                  value={
                    params.deduplicate === undefined
                      ? ""
                      : String(params.deduplicate)
                  }
                  onChange={(event) =>
                    update(
                      "deduplicate",
                      event.target.value === ""
                        ? undefined
                        : event.target.value === "true",
                    )
                  }
                >
                  <option value="">
                    {inherited ? "继承智能体设置" : "默认：保留"}
                  </option>
                  <option value="false">保留</option>
                  <option value="true">删除完全重复记录</option>
                </select>
              </Field>
            </div>
          )}
          {text && (
            <Field
              label="文本长度上限（字符）"
              hint="本地来源文本整理有效，允许 100 至 20000；不代表模型输出字数。"
            >
              <input
                type="number"
                min={100}
                max={20000}
                value={params.max_chars ?? ""}
                placeholder={inherited ? "继承智能体设置" : "使用运行默认值"}
                onChange={(event) =>
                  update(
                    "max_chars",
                    event.target.value ? Number(event.target.value) : undefined,
                  )
                }
              />
            </Field>
          )}
        </>
      )}
      <details className="advanced-parameters" open={error ? true : undefined}>
        <summary>高级参数 JSON · 筛选条件及完整配置</summary>
        <Field
          label="执行参数 JSON"
          hint='可设置 filter 等值筛选，例如 {"filter":{"status":"active"}}。所有参数受后端白名单校验。'
        >
          <textarea
            className="mono"
            rows={5}
            value={value}
            onChange={(event) => onChange(event.target.value)}
          />
        </Field>
      </details>
    </div>
  );
}
