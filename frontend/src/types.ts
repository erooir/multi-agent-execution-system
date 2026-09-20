import type { RecordData } from "./api";
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
  data: RecordData;
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
  admin: "管理员",
  operator: "研究员",
  reviewer: "审核员",
};
