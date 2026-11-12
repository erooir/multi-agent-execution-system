import type { AnswerMap, AnswerTable } from "./QuestionProducts";

export type DataRequirement = {
  id: string;
  title: string;
  required: boolean;
  description: string;
  formats: string[];
  fields: string[];
  status: "missing" | "available" | "provided" | "satisfied";
  matched_asset_ids: string[];
  time_scope?: string;
  spatial_scope?: string;
  unit_scope?: string;
};
export type TaskAsset = {
  id: string;
  name: string;
  modality: string;
  media_type?: string;
  classification: string;
  bytes?: number;
  source_version?: string;
};
export type TaskCase = {
  id: string;
  project_id: string;
  title: string;
  goal: string;
  output_requirements?: string;
  status: string;
  requirements?: DataRequirement[];
  available_assets?: TaskAsset[];
  profiles?: {
    asset_id: string;
    name: string;
    readable?: boolean;
    fields?: string[];
    record_count?: number | null;
    page_count?: number | null;
    status?: string;
    issues?: string[];
  }[];
  submissions?: { id: string; input_refs: string[]; created_at: string }[];
  requirements_analysis?: {
    summary?: string;
    assumptions?: string[];
    model?: string;
    task_id?: string;
  } | null;
  plans?: Record<string, any>[];
  runs?: Record<string, any>[];
  task_id?: string | null;
  result?: {
    answer?: string;
    citations?: {
      source: string;
      source_id?: string;
      location: string;
      excerpt: string;
      id?: string;
      quote_truncated?: boolean;
      source_url?: string;
      page?: number;
    }[];
    tables?: AnswerTable[];
    maps?: AnswerMap[];
    artifacts?: Record<string, any>[];
    generated_files?: {
      name: string;
      media_type?: string;
      bytes?: number;
      checksum?: string;
      download_url?: string;
      quality_status?: string;
      task_review_passed?: boolean;
    }[];
    missing?: string[];
    warnings?: string[];
    content_withheld?: boolean;
  } | null;
  review?: Record<string, any> | null;
  trace?: Record<string, any> | null;
  usage?: {
    accounting_complete?: boolean;
    reserved_cny?: number;
    uncertain_count?: number;
    request_count?: number;
    cost_cny?: number;
  };
  progress?: { key: string; label: string; status: string }[];
  missing?: string[];
  mode: string;
  max_cost_cny: number;
  public_egress_authorized: boolean;
  created_at: string;
  updated_at: string;
  content_withheld?: boolean;
  reason_code?: string;
};

export const taskCaseStatus: Record<string, string> = {
  analyzing_requirements: "正在分析资料需求",
  awaiting_data: "待补充资料",
  ready: "资料就绪",
  accepted: "已受理",
  planning: "正在规划",
  queued: "等待执行",
  running: "正在执行",
  evaluating: "正在审核",
  revising: "正在优化流程",
  succeeded: "已完成",
  partial: "部分完成",
  failed: "未能完成",
  cancelled: "已取消",
};
export const taskCaseActive = new Set([
  "analyzing_requirements",
  "accepted",
  "planning",
  "queued",
  "running",
  "evaluating",
  "revising",
]);
export const taskCaseResumable = new Set(["failed", "partial", "cancelled"]);
export const taskCaseEditable = new Set([
  "awaiting_data",
  "ready",
  ...taskCaseResumable,
]);
export const taskCaseTerminal = new Set([
  "succeeded",
  "partial",
  "failed",
  "cancelled",
]);

export function taskCaseHeading(
  task: Pick<TaskCase, "title" | "goal">,
): string {
  // Older records stored the first 80 Python characters as their automatic title.
  // Keep that storage contract while showing the complete goal in the workspace.
  const automaticTitle = Array.from(task.goal).slice(0, 80).join("");
  return !task.title || task.title === automaticTitle ? task.goal : task.title;
}

export function taskCaseCanStart(task: TaskCase): boolean {
  return (
    task.status === "ready" &&
    !(task.missing || []).length &&
    (task.mode !== "cloud" || task.public_egress_authorized)
  );
}

export function bytesLabel(bytes: number | undefined): string {
  if (bytes === undefined) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function repairLabel(repair: Record<string, any>): string {
  return (
    repair.label ||
    repair.action_label ||
    repair.action ||
    repair.entry_kind ||
    "流程修订"
  );
}

export function repairMeasurements(repair: Record<string, any>) {
  const before = repair.before_counts || repair.before || {};
  const after = repair.outcome?.after_counts || repair.after || {};
  const labels: Record<string, string> = {
    evidence: "可定位证据",
    failed_checks: "未通过检查",
    source_gaps: "资料缺口",
    withheld_readings: "未采用的辅助译读",
  };
  return Object.entries(labels)
    .filter(
      ([key]) =>
        typeof before[key] === "number" || typeof after[key] === "number",
    )
    .map(([key, label]) => ({
      key,
      label,
      before: before[key],
      after: after[key],
    }));
}

export function stageLabels(stages: unknown): string {
  const labels: Record<string, string> = {
    prepare: "资料解析",
    analyze: "专业分析",
    review: "独立审核",
  };
  if (Array.isArray(stages))
    return stages.length
      ? stages.map((s) => labels[String(s)] || String(s)).join(" → ")
      : "无";
  if (stages && typeof stages === "object" && !Object.keys(stages).length)
    return "无";
  return textValue(stages);
}

export function textValue(value: unknown): string {
  if (value == null) return "—";
  if (typeof value === "boolean") return value ? "是" : "否";
  if (Array.isArray(value)) return value.map(textValue).join("、");
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

export function citationSourceUrl(citation: {
  source_url?: string;
  page?: number;
}): string | null {
  const url = citation.source_url;
  // Source links are generated by the authenticated API, never taken from model prose.
  if (!url || !url.startsWith("/api/v2/task-cases/") || /[\r\n\\]/.test(url))
    return null;
  return (
    url.split("#")[0] +
    (Number.isInteger(citation.page) && citation.page! > 0
      ? `#page=${citation.page}`
      : "")
  );
}

export const taskCheckLabels: Record<string, string> = {
  nonempty_output: "交付产物非空",
  source_refs: "来源属于冻结输入",
  case_artifact_type: "产物结构有效",
  case_review_present: "具备独立审核记录",
  case_contract: "满足任务交付契约",
  evidence_locations: "证据位置可定位",
  has_evidence: "存在原文证据",
  product_evidence_refs: "结论引用对应证据",
  usable_product_content: "产物包含可用内容",
  required_products: "所需交付项齐全",
  case_gaps: "未解决缺口检查",
  substantive_product: "形成实质分析结果",
  monthly_observations: "逐月观测覆盖有效",
  monthly_arithmetic: "逐月数值计算正确",
  monthly_crossfoot: "月度合计与总数复算一致",
  reference_inventory: "参考资料覆盖完整",
  record_schema: "记录格式有效",
  min_records: "记录数量符合要求",
  required_fields: "必需字段齐全",
  exact_source_spans: "原文位置一致",
  extractive_fragments: "来源片段完整",
  grounded_citations: "引用可核验",
  min_length: "内容长度符合要求",
  supported_output: "交付类型受支持",
  model_output_invalid: "模型输出格式有效",
  autonomous_schema: "自主分析产物格式有效",
};
