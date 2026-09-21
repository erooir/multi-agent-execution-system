import type { RecordData } from "./api";
export type SkillSummary = {
  id: string;
  name: string;
  description?: string;
  version?: string;
  execution?: string;
  execution_mode?: "agent" | "recipe";
  node_kinds?: string[];
  allowed_tools?: string[];
  evidence_required?: boolean;
  requires_documents?: boolean;
  status?: string;
  note?: string;
  input_schema?: Record<string, any>;
  output_schema?: Record<string, any>;
  enabled?: boolean;
};
export type ToolSummary = {
  id: string;
  name: string;
  version?: string;
  provider: "local" | "http" | "mcp";
  read_only?: boolean;
  network?: "none" | "required";
  data_egress?: "none" | "query" | "derived" | "raw";
  timeout_seconds?: number;
  requires_confirmation?: boolean;
  input_schema?: Record<string, any>;
};
export type McpServerSummary = {
  id: string;
  transport: "stdio" | "streamable_http";
  enabled: boolean;
  tool_allowlist?: string[];
  roots?: string[];
  startup_timeout_seconds?: number;
  call_timeout_seconds?: number;
};
export type CapabilityStats = {
  skills: number;
  tools: number;
  mcp_servers: number;
  healthy_tools: number;
};
export type ToolCallRecord = {
  tool_id: string;
  tool_version?: string;
  provider?: string;
  status: string;
  duration_ms?: number;
};
export type CapabilityTrace = {
  tool_id?: string;
  tool_version?: string;
  provider?: string;
  duration_ms?: number;
  tool_calls?: ToolCallRecord[];
};
export type CapabilityErrorInfo = { code: string; message: string };
export type BootstrapData = RecordData & {
  skills?: SkillSummary[];
  tools?: ToolSummary[];
  mcp_servers?: McpServerSummary[];
  capability_stats?: CapabilityStats;
};
export type PageId =
  | "overview"
  | "projects"
  | "knowledge"
  | "agents"
  | "skills"
  | "workflows"
  | "runs"
  | "reports"
  | "evaluations"
  | "system";
export type PageProps = {
  data: BootstrapData;
  refresh: () => Promise<void>;
  notify: (s: string, error?: boolean) => void;
  canEdit: boolean;
  canReview: boolean;
  go: (p: PageId, id?: string) => void;
  editWorkflow: (w: RecordData) => void;
  selectedId?: string;
};
export const categoryNames: Record<string, string> = {
  technology: "科技研究",
  geography: "地理信息",
  situational: "态势分析",
};
export const roleNames: Record<string, string> = {
  planner: "任务规划",
  retriever: "知识检索",
  writer: "报告撰写",
  coordinator: "协同调度",
  parser: "文档解析",
  admin: "管理员",
  operator: "研究员",
  reviewer: "审核员",
};
