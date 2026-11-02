import type { Item } from "./types";

export const stageNames: Record<string, string> = {
  prepare: "资料解析",
  analyze: "专业分析",
  review: "独立审核",
  other: "其他岗位",
};

export const parameterNames: Record<string, string> = {
  source_id: "主资料",
  other_source_id: "对照资料",
  aoi_source_id: "研究区资料",
  page: "主资料页码",
  other_page: "对照资料页码",
  start: "起始字符",
  end: "结束字符",
  other_start: "对照起始字符",
  other_end: "对照结束字符",
  start_ms: "起始时间（毫秒）",
  end_ms: "结束时间（毫秒）",
  length: "读取字符数",
  limit: "最多返回条数",
  acronym: "缩略语",
  key_fields: "键字段",
  exclude_kinds: "排除的记录类别",
  where: "字段筛选条件",
  scope: "统计范围",
  unit: "单位",
  value_field: "数值列名",
  detail_id_field: "明细标识列名",
  scope_field: "范围列名",
  unit_field: "单位列名",
  role_field: "类别列名",
  detail_role: "明细类别",
  total_role: "合计类别",
  baseline_start: "基线起始年",
  baseline_end: "基线结束年",
  target_year: "目标年",
  rank_start: "排名起始年",
  year_field: "年份列名",
  policy_unit_field: "口径单位列名",
  sheet: "工作表名称",
  unit_evidence_row: "单位证据行号",
  unit_evidence_field: "单位证据列名",
  min_valid_value: "最小有效值",
  metric_crs: "面积投影坐标系",
  od_id: "起终点编号",
  before_date: "前期日期",
  after_date: "后期日期",
};

export function fieldSchema(property: Item): Item {
  return {
    ...property,
    ...(property.anyOf?.find((p: Item) => p.type !== "null") || {}),
  };
}

export function isSourceField(name: string) {
  return /^(source_id|.+_source_id)$/.test(name);
}

const textTypes = [
  "text/plain",
  "text/markdown",
  "text/vtt",
  "application/x-subrip",
  "application/pdf",
];
const tableTypes = [
  "application/json",
  "application/geo+json",
  "application/jsonl",
  "application/x-ndjson",
  "text/csv",
];

export function canPreviewSource(
  asset: Item,
  version: Item,
  toolId: string,
  now = Date.now(),
) {
  if (
    asset.project_id !== version.project_id ||
    asset.status !== "active" ||
    !version.modalities?.includes(asset.modality) ||
    (asset.expires_at && !(Date.parse(asset.expires_at) > now))
  )
    return false;
  const media = (asset.media_type || "").split(";")[0].toLowerCase();
  if (toolId === "evidence.pdf_page") return media === "application/pdf";
  if (toolId === "evidence.select_cues")
    return ["text/vtt", "application/x-subrip"].includes(media);
  if (toolId.startsWith("evidence.")) return textTypes.includes(media);
  return tableTypes.includes(media) && asset.parsed_content_available === true;
}

export function argumentTemplate(
  spec: Item,
  ids: string[],
  assets: Item[],
  previous: Item = {},
): Item {
  const values: Item = {};
  let sourceIndex = 0;
  for (const [name, raw] of Object.entries(
    spec.input_schema?.properties || {},
  )) {
    const field = fieldSchema(raw as Item);
    const required = spec.input_schema.required?.includes(name);
    if (isSourceField(name)) {
      const chosen = previous[name]
        ? ids.includes(previous[name])
          ? previous[name]
          : undefined
        : ids[sourceIndex];
      sourceIndex++;
      if (chosen || required) values[name] = chosen || "";
    } else if (name === "page" || name === "other_page") {
      const sourceKey = name === "page" ? "source_id" : "other_source_id";
      const asset = assets.find((a) => a.id === values[sourceKey]);
      if (required || asset?.media_type?.startsWith("application/pdf"))
        values[name] = previous[name] ?? 1;
    } else if (Object.hasOwn(previous, name)) values[name] = previous[name];
    else if (field.default != null) values[name] = field.default;
  }
  return values;
}

export function parsePreviewArguments(text: string): Item {
  const value = JSON.parse(text);
  if (!value || typeof value !== "object" || Array.isArray(value))
    throw Error("参数应为 JSON 对象");
  return value;
}

export function checkArguments(spec: Item, values: Item, selected: string[]) {
  for (const name of spec.input_schema?.required || []) {
    if (
      values[name] == null ||
      values[name] === "" ||
      (Array.isArray(values[name]) && !values[name].length)
    ) {
      throw Error("请填写" + (parameterNames[name] || name));
    }
  }
  for (const [name, value] of Object.entries(values)) {
    if (isSourceField(name) && value != null && !selected.includes(value))
      throw Error("资料参数必须指向已勾选的可用资料");
    const property = spec.input_schema?.properties?.[name];
    if (!property || value == null) continue;
    const f = fieldSchema(property);
    if (
      (f.type === "object" &&
        (typeof value !== "object" || Array.isArray(value))) ||
      (f.type === "array" &&
        (!Array.isArray(value) ||
          value.some((s) => typeof s !== "string" || !s.length))) ||
      (f.type === "integer" && !Number.isInteger(value)) ||
      (f.type === "number" &&
        (typeof value !== "number" || !Number.isFinite(value))) ||
      (f.type === "string" && typeof value !== "string") ||
      (typeof value === "number" &&
        ((f.minimum != null && value < f.minimum) ||
          (f.maximum != null && value > f.maximum) ||
          (f.exclusiveMinimum != null && value <= f.exclusiveMinimum)))
    ) {
      throw Error(
        (parameterNames[name] || name) + "的格式或取值超出参数契约。",
      );
    }
  }
}
