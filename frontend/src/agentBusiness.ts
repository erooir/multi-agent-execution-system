import type { Item } from "./types";

export const businessLabels = {
  general: "通用",
  science: "科技情报",
  geographic: "地理态势",
  battlefield: "战场情报",
} as const;

type Business = keyof typeof businessLabels;
const caseFamilies: Record<string, Business> = {
  T: "science",
  G: "geographic",
  B: "battlefield",
};

export function caseBusiness(agent: Item): { key: Business; caseId?: string } | undefined {
  const stage = agent.role_contract?.stage ?? agent.case_role ?? agent.parameters?.case_stage;
  if (stage === "prepare" || stage === "review") return { key: "general" };

  // Older case specialists have the scope in their parameters only.
  const caseId = agent.edition_case_id ?? agent.role_contract?.case_id ?? agent.parameters?.case_scope;
  if (typeof caseId === "string" && /^[BTG]\d{2}$/.test(caseId)) {
    return { key: caseFamilies[caseId[0]], caseId };
  }
}

export function businessKey(agent: Item): Business {
  const linked = caseBusiness(agent);
  if (linked) return linked.key;
  const selected = agent.management_profile?.business;
  if (selected === "historical") return "battlefield";
  return typeof selected === "string" && Object.hasOwn(businessLabels, selected)
    ? selected as Business
    : "general";
}

export function businessLabel(agent: Item) {
  return businessLabels[businessKey(agent)];
}
