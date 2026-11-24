import type { Item } from "./types";

/** Select a previously reviewed sample only while all its original inputs are still usable here. */
export function verifiedCaseSamples(projectId: string, tasks: Item[], assets: Item[], now = Date.now()) {
  const available = new Set(assets.filter(asset =>
    asset.project_id === projectId && !asset.task_case_id && !asset.is_file_index &&
    asset.status === "active" && asset.classification === "public" && Date.parse(asset.expires_at) > now
  ).map(asset => asset.id));
  const samples = new Map<string, Item>();
  for (const task of tasks) {
    const request = task.request;
    if (
      task.project_id !== projectId || task.deleted_at || task.task_case_id ||
      task.status !== "succeeded" || task.quality_verdict?.passed !== true ||
      !(task.result?.usage?.request_count > 0) || request?.mode !== "cloud" ||
      request.task_type !== "case_analysis" || request.case_scope !== "sample" ||
      request.use_project_knowledge || !Array.isArray(request.input_refs) ||
      !request.input_refs.length || !request.input_refs.every((id: string) => available.has(id))
    ) continue;
    const previous = samples.get(request.case_id);
    if (!previous || task.finished_at > previous.finished_at) samples.set(request.case_id, task);
  }
  return samples;
}
