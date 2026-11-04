export type Item = Record<string, any>;
export interface User {
  id: string;
  username: string;
  name: string;
  role: "admin" | "operator" | "reviewer" | "requester";
}
export interface Bootstrap {
  user: User;
  projects: (Item & { id: string; name: string })[];
  agents: Item[];
  workflows: Item[];
  tasks: Item[];
  data_assets: Item[];
  schedules: Item[];
  improvements: Item[];
  connectors: Item[];
  capabilities: Item | Item[];
  workers: Item[];
  budget: Item;
  stats: Item;
  system: Item;
  memories?: Item[];
  evaluations?: Item[];
  users?: Item[];
}
export type Page =
  | "overview"
  | "casework"
  | "agents"
  | "tools"
  | "tasks"
  | "workflows"
  | "capabilities"
  | "data"
  | "knowledge"
  | "improvements"
  | "operations";
export type Notify = (message: string, error?: boolean) => void;
export type Mutation = (
  fn: () => Promise<any>,
  message?: string,
) => Promise<any>;
