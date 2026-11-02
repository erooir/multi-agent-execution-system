import type { Item } from "./types";

export function callRound(revision: unknown) {
  return typeof revision === "number" &&
    Number.isInteger(revision) &&
    revision >= 0
    ? "第 " + (revision + 1) + " 轮"
    : "未记录轮次";
}

export function findFindingCall(finding: Item, calls: Item[]) {
  if (!finding.tool_call_id) return undefined;
  return calls.find(
    (call) =>
      call.id === finding.tool_call_id &&
      call.capability_id === finding.tool_id &&
      String(call.version) === String(finding.version) &&
      ((finding.status === "completed" &&
        call.status === "completed" &&
        !!finding.result_checksum &&
        call.result_checksum === finding.result_checksum) ||
        (finding.status === "unavailable" &&
          call.status === "failed" &&
          !!finding.error_code &&
          call.error_code === finding.error_code &&
          !call.result_checksum &&
          !finding.result_checksum)),
  );
}

export function roundNodes(task: Item, revision: number) {
  const plan = (task.repair_history || []).find(
    (p: Item) => p.decision === "revise" && p.revision === revision,
  );
  const reused = Object.values(plan?.reused_nodes || {});
  const nodes: Item[] = task.nodes || [];
  const executed = nodes.filter(
    (n) => n.attempt === revision + 1 && !plan?.reused_nodes?.[n.node_id],
  );
  return [
    ...executed,
    ...nodes
      .filter((n) => reused.includes(n.id))
      .map((n) => ({
        ...n,
        reusedFromAttempt: n.attempt,
      })),
  ];
}

export function callContext(task: Item, call: Item) {
  const node = (task.nodes || []).find((n: Item) =>
    call.instance_id
      ? n.instance_id === call.instance_id
      : call.node_id &&
        n.node_id === call.node_id &&
        n.attempt === call.revision + 1,
  );
  const agent = (task.snapshot?.agents || []).find(
    (a: Item) =>
      node && a.agent_id === node.agent_id && a.version === node.agent_version,
  );
  const bindings = agent?.runtime_bindings;
  const match = (
    call.kind === "skill" ? bindings?.skills : bindings?.tools
  )?.find(
    (b: Item) =>
      b.id === call.capability_id && String(b.version) === String(call.version),
  );
  return {
    node,
    agent,
    label: match?.label || match?.name || call.capability_id,
  };
}
