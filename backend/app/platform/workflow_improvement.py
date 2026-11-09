"""Measured workflow changes over frozen local DAGs, with no new grants or code."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from time import perf_counter

from . import service as s
from . import task_plugins
from .contracts import WorkflowImprovementInput
from .db import now
from .resource_locks import lock_snapshot_dependencies

SCOPE = "local_workflow_dag"
TASK_TYPES = {"source_summary", "text_analysis", "structured_transform"}


def _ordered(nodes):
    result, done = [], set()
    while len(done) < len(nodes):
        ready = [n for n in nodes if n["id"] not in done and set(n["depends_on"]) <= done]
        if not ready:
            s.fail("invalid_graph", "流程候选存在循环、重复节点或缺失依赖", 422)
        for node in ready:
            done.add(node["id"])
            result.append(node)
    return result


def _verify(record, repository):
    if any(s.digest(record[field]) != record[field + "_hash"] for field in ("baseline", "candidate")):
        s.fail("evaluation_changed", "冻结流程配置校验失败", 409)
    if s.digest(record["samples"]) != record["dataset_hash"]:
        s.fail("evaluation_changed", "冻结流程样本校验失败", 409)
    stored = repository.get("workflow_versions", record["baseline"]["id"])
    if s.digest(stored) != record["baseline_hash"]:
        s.fail("baseline_changed", "不可变流程基线不再匹配", 409)
    lock_snapshot_dependencies(
        repository, [sample["snapshot"] for sample in record["samples"]], include_agents=True
    )
    for sample in record["samples"]:
        try:
            task_plugins.validate_frozen(
                sample["snapshot"].get("task_plugin"), repository, record["project_id"]
            )
        except task_plugins.TaskPluginError as exc:
            s.fail(exc.code, exc.message, 409)
        for frozen in sample["snapshot"]["agents"]:
            aid = frozen["agent_id"]
            current = repository.get("agents", aid)
            pinned = repository.get("agent_versions", f"{aid}:{frozen['version']}")
            if (
                not current
                or current["project_id"] != record["project_id"]
                or current["status"] != "published"
                or not pinned
                or frozen != {**pinned, "id": aid, "agent_id": aid}
            ):
                s.fail("evaluation_changed", "冻结智能体已停用或版本发生变化", 409)
        for frozen in sample["snapshot"]["inputs"]:
            current = repository.get("data_assets", frozen["id"])
            if frozen.get("project_id") != record["project_id"]:
                s.fail("permission_denied", "评测来源不能跨项目", 403)
            s.verify_frozen_asset(frozen, current)


def create(body, user, repository):
    # Internal automatic diagnoses are metadata, not extra public configuration.
    provenance = {key: body[key] for key in ("source_task_id", "coverage_repair") if key in body}
    body = WorkflowImprovementInput.model_validate(
        {key: value for key, value in body.items() if key not in provenance}
    ).model_dump()
    with repository.transaction() as tx:
        flow = s.required("workflows", body["workflow_id"], user, True, tx)
        if flow["project_id"] != body["project_id"] or flow["status"] != "published":
            s.fail("invalid_candidate", "候选必须引用同项目已发布流程", 422)
        if flow["task_type"] not in TASK_TYPES:
            s.fail("unsupported_evaluation_scope", "流程重放仅支持已验证的本地文本或结构化处理", 422)
        if provenance.get("source_task_id"):
            tx.get("projects", body["project_id"], for_update=True)
            prior = tx.list("improvements", body["project_id"], source_task_id=provenance["source_task_id"])
            if prior:
                return prior[0]
        baseline = tx.get("workflow_versions", f"{flow['id']}:{flow['current_version']}")
        if not baseline or flow["nodes"] != baseline["nodes"]:
            s.fail("baseline_changed", "流程草稿与发布版本不一致", 409)
        original = {n["id"]: n for n in baseline["nodes"]}
        nodes = body["nodes"]
        if len(nodes) != len(original) or {n["id"] for n in nodes} != set(original):
            s.fail("permission_denied", "流程候选不能增加或移除节点", 403)
        for node in nodes:
            old = original[node["id"]]
            if node["agent_id"] != old["agent_id"] or node["label"] != old["label"]:
                s.fail("permission_denied", "流程候选不能替换智能体或节点职责", 403)
        # Reuse publication's DAG and parameter checks, but restore pinned versions:
        # a newer agent publication must never silently enter this experiment.
        s.validate_nodes(nodes, tx, body["project_id"])
        for node in nodes:
            node["agent_version"] = original[node["id"]]["agent_version"]
        candidate = {**deepcopy(baseline), "nodes": nodes}
        sample_ids = list(dict.fromkeys(body["sample_task_ids"]))
        if len(sample_ids) < 2:
            s.fail("insufficient_evaluation_data", "至少需要两个独立任务及互不重叠的来源", 422)
        samples, used, frozen_agents = [], set(), None
        for index, tid in enumerate(sample_ids):
            task = s.required("tasks", tid, user, repository=tx)
            request, snapshot = task["request"], task["snapshot"]
            if (
                task["project_id"] != flow["project_id"]
                or task["status"] not in s.TERMINAL
                or request["mode"] != "local"
                or request["task_type"] != flow["task_type"]
                or snapshot["workflow"] != baseline
            ):
                s.fail("invalid_evaluation_sample", "样本须为当前流程版本的同项目真实本地终态任务", 422)
            agents = snapshot["agents"]
            if frozen_agents is not None and agents != frozen_agents:
                s.fail("baseline_changed", "样本必须使用同一组冻结智能体版本", 409)
            frozen_agents = agents
            if any(
                a.get("structure") != "atomic"
                or a.get("execution_strategy", "fixed") != "fixed"
                or a.get("model_policy") != "local"
                or request["task_type"] not in a.get("tool_ids", [])
                for a in agents
            ):
                s.fail("unsupported_evaluation_scope", "流程重放只接受已授权的本地固定原子工具", 422)
            if any(
                n.get("config", {}).get("task_type", request["task_type"]) != request["task_type"]
                for n in baseline["nodes"]
            ) or any(
                a.get("parameters", {}).get("task_type", request["task_type"]) != request["task_type"]
                for a in agents
            ):
                s.fail("unsupported_evaluation_scope", "流程重放不支持节点任务类型覆盖", 422)
            sources = {a["checksum"] for a in snapshot["inputs"]}
            if not sources or sources & used:
                s.fail("insufficient_evaluation_data", "开发及留出样本的实际来源内容必须互不重叠", 422)
            used.update(sources)
            samples.append(
                {
                    "task_id": tid,
                    "request": request,
                    "snapshot": snapshot,
                    "split": "holdout" if index == len(sample_ids) - 1 else "development",
                }
            )
        record = {
            **body,
            **provenance,
            "target_kind": "workflow",
            "status": "candidate",
            "baseline_version": baseline["version"],
            "baseline": baseline,
            "baseline_hash": s.digest(baseline),
            "candidate": candidate,
            "candidate_hash": s.digest(candidate),
            "samples": samples,
            "dataset_hash": s.digest(samples),
            "policy": {
                "scope": SCOPE,
                "no_hard_failures": True,
                "no_regression": True,
                "development_gain_required": True,
                "permissions_unchanged": True,
                "max_sample_seconds": 60,
            },
            "created_by": user["id"],
        }
        _verify(record, tx)
        record = tx.put("improvements", record)
        s.audit(
            tx, flow["project_id"], "improvement.created", record["id"], user, {"target_kind": "workflow"}
        )
        return record


def replay_sample(sample, workflow):
    """Replay the full DAG with production tools, edge records and per-artifact gates.

    Models, composite agents and case teams require separate evaluation contracts.
    Frozen task plugins pass through their production validation and execution gates.
    """
    from .quality import deterministic_quality
    from .runtime import _read_content, execute_local

    started = perf_counter()
    request, snapshot = sample["request"], sample["snapshot"]
    if request["mode"] != "local" or request["task_type"] not in TASK_TYPES:
        raise ValueError("unsupported_evaluation_scope")
    inputs = [{**a, "content": _read_content(a)} for a in snapshot["inputs"]]
    agents = {(a["agent_id"], a["version"]): a for a in snapshot["agents"]}

    def check_deadline():
        if perf_counter() - started > min(60, request.get("deadline_seconds", 300)):
            raise RuntimeError("evaluation_deadline_exceeded")

    history = []
    for revision in range(request.get("max_revisions", 0) + 1):
        outputs, results = {}, []
        for node in _ordered(workflow["nodes"]):
            check_deadline()
            agent = agents[(node["agent_id"], node["agent_version"])]
            config = {**agent.get("parameters", {}), **node.get("config", {})}
            task_type = request["task_type"]
            if config.get("task_type", task_type) != task_type:
                raise ValueError("unsupported_evaluation_scope")
            if revision and task_type in {"source_summary", "text_analysis"}:
                config["max_chars"] = min(
                    20000, max(config.get("max_chars", 1500), request.get("quality", {}).get("min_length", 0))
                )
            previous = [outputs[dep] for dep in node["depends_on"]]
            for output in previous:
                gate = deterministic_quality(
                    {**request, "quality": {"min_records": 0, "min_length": 0}}, output, inputs
                )
                if gate["hard_failures"]:
                    raise RuntimeError("internal_output_invalid")
            tool_inputs = inputs
            if task_type == "structured_transform" and previous:
                tool_inputs = [
                    {
                        **inputs[0],
                        "content": {"records": [r for p in previous for r in p["content"]["records"]]},
                        "media_type": "application/json",
                        "modality": "structured",
                    }
                ]
            output = task_plugins.execute(
                snapshot.get("task_plugin"), task_type, tool_inputs, config, execute_local
            )
            check_deadline()
            if task_type == "structured_transform" and previous:
                output["source_refs"] = list(dict.fromkeys(r for p in previous for r in p["source_refs"]))
            outputs[node["id"]] = output
            results.append(
                {
                    "node_id": node["id"],
                    "output": output,
                    "quality": deterministic_quality(request, output, inputs),
                }
            )
        quality = {
            "passed": bool(results) and all(n["quality"]["passed"] for n in results),
            "checks": [{**c, "node_id": n["node_id"]} for n in results for c in n["quality"]["checks"]],
            "issues": [{**i, "node_id": n["node_id"]} for n in results for i in n["quality"]["issues"]],
            "hard_failures": [
                {**i, "node_id": n["node_id"]} for n in results for i in n["quality"]["hard_failures"]
            ],
        }
        history.append({"revision": revision, "nodes": results, "quality": quality})
        check_deadline()
        if quality["passed"] or quality["hard_failures"]:
            break
    check_deadline()
    return {
        "quality": quality,
        "history": history,
        "attempts": len(history),
        "duration_ms": round((perf_counter() - started) * 1000, 3),
        "cost_cny": 0,
    }


def _gain(pair):
    before, after = pair["baseline"], pair["candidate"]
    bq, aq = before["quality"], after["quality"]
    before_checks = {(c.get("node_id"), c["name"]) for c in bq.get("checks", []) if c["passed"]}
    after_checks = {(c.get("node_id"), c["name"]) for c in aq.get("checks", []) if c["passed"]}
    if before_checks - after_checks or (bq["passed"] and not aq["passed"]):
        return -1
    if aq["passed"]:
        if not bq["passed"]:
            return 1
        return (before.get("attempts", 1) > after.get("attempts", 1)) - (
            before.get("attempts", 1) < after.get("attempts", 1)
        )
    return 0


async def evaluate(record, user, repository):
    if record["status"] in {"promoted", "rolled_back"}:
        s.fail("invalid_state", "已发布候选不能重新评测", 409)
    _verify(record, repository)
    pairs = []
    for sample in record["samples"]:
        pair = {"task_id": sample["task_id"], "split": sample["split"]}
        for label in ("baseline", "candidate"):
            started = perf_counter()
            try:
                pair[label] = await asyncio.to_thread(replay_sample, sample, record[label])
            except (ValueError, RuntimeError, OSError, TypeError, KeyError) as exc:
                pair[label] = {
                    "quality": {
                        "passed": False,
                        "checks": [],
                        "issues": [],
                        "hard_failures": [type(exc).__name__],
                    },
                    "duration_ms": round((perf_counter() - started) * 1000, 3),
                    "cost_cny": 0,
                }
        pairs.append(pair)
    regression = any(_gain(p) < 0 for p in pairs)
    hard = any(p["candidate"]["quality"]["hard_failures"] for p in pairs)
    holdout = all(p["candidate"]["quality"]["passed"] for p in pairs if p["split"] == "holdout")
    gain = any(p["split"] == "development" and _gain(p) > 0 for p in pairs)
    passed = not regression and not hard and holdout and gain
    verdict = {
        "passed": passed,
        "regression": regression,
        "hard_failure": hard,
        "holdout_passed": holdout,
        "development_gain": gain,
        "sample_count": len(pairs),
        "baseline_passed": sum(p["baseline"]["quality"]["passed"] for p in pairs),
        "candidate_passed": sum(p["candidate"]["quality"]["passed"] for p in pairs),
        "reason": "measured_quality_gain" if passed else "no_quality_gain_or_regression",
        "limitation": "仅证明冻结本地工具完整流程的质量及修订变化，不证明模型提示词或任意任务的普遍提升",
    }
    with repository.transaction() as tx:
        current = s.required("improvements", record["id"], user, True, tx)
        if current["updated_at"] != record["updated_at"]:
            s.fail("evaluation_changed", "评测期间候选已变化", 409)
        _verify(record, tx)
        evidence = tx.put(
            "evaluations",
            {
                "project_id": record["project_id"],
                "candidate_id": record["id"],
                "status": "passed" if passed else "rejected",
                "method": "frozen_local_workflow_replay",
                "dataset_hash": record["dataset_hash"],
                "candidate_hash": record["candidate_hash"],
                "verdict": verdict,
                "samples": pairs,
                "evaluated_at": now(),
            },
        )
        record.update(
            status="evaluated" if passed else "rejected",
            verdict=verdict,
            evaluation_id=evidence["id"],
            evaluation_hash=s.digest(evidence),
        )
        record = tx.put("improvements", record)
        s.audit(tx, record["project_id"], "improvement.evaluated", record["id"], user, verdict)
    return record


def promote(record, user, tx):
    flow = s.required("workflows", record["workflow_id"], user, True, tx)
    _verify(record, tx)
    evidence = tx.get("evaluations", record.get("evaluation_id"))
    if (
        record["status"] != "evaluated"
        or not record.get("verdict", {}).get("passed")
        or not evidence
        or s.digest(evidence) != record.get("evaluation_hash")
        or evidence.get("verdict") != record["verdict"]
    ):
        s.fail("quality_gate_failed", "流程候选必须通过完整冻结评测且证据未被修改", 409)
    if (
        flow["status"] != "published"
        or flow["current_version"] != record["baseline_version"]
        or flow["nodes"] != record["baseline"]["nodes"]
    ):
        s.fail("baseline_changed", "流程版本或草稿已变化，拒绝覆盖", 409)
    version = max(v["version"] for v in tx.list("workflow_versions", definition_id=flow["id"])) + 1
    flow.update(nodes=deepcopy(record["candidate"]["nodes"]), current_version=version)
    flow = tx.put("workflows", flow)
    saved = tx.put(
        "workflow_versions",
        {
            **flow,
            "id": f"{flow['id']}:{version}",
            "definition_id": flow["id"],
            "version": version,
            "improvement_id": record["id"],
        },
    )
    record.update(
        status="promoted",
        candidate_version=version,
        candidate_flow_version=version,
        candidate_flow_hash=s.digest(saved),
        promoted_at=now(),
    )
    record = tx.put("improvements", record)
    s.audit(
        tx,
        flow["project_id"],
        "improvement.promoted",
        record["id"],
        user,
        {"target_kind": "workflow", "version": version},
    )
    return record


def rollback(record, user, tx):
    if record["status"] != "promoted":
        s.fail("invalid_state", "只有已晋升流程可以回滚", 409)
    flow = s.required("workflows", record["workflow_id"], user, True, tx)
    current = tx.get("workflow_versions", f"{flow['id']}:{record['candidate_version']}")
    baseline = tx.get("workflow_versions", record["baseline"]["id"])
    if (
        flow["status"] != "published"
        or flow["current_version"] != record["candidate_version"]
        or not current
        or s.digest(current) != record["candidate_flow_hash"]
        or flow["nodes"] != current["nodes"]
        or s.digest(baseline) != record["baseline_hash"]
    ):
        s.fail("version_changed", "流程指针、草稿或后续发布已变化，拒绝覆盖", 409)
    flow.update(nodes=deepcopy(baseline["nodes"]), current_version=baseline["version"])
    tx.put("workflows", flow)
    record.update(status="rolled_back", rolled_back_at=now())
    record = tx.put("improvements", record)
    s.audit(
        tx, flow["project_id"], "improvement.rolled_back", record["id"], user, {"target_kind": "workflow"}
    )
    return record


async def maintain(repository, actor):
    """Deployment opt-in learns only observed extractive coverage defects.

    Other DAG edits are explicit candidates evaluated through the same gates.
    A new failure only rolls back after baseline and candidate replay proves it.
    """
    from . import improvement

    tasks = [t for t in repository.list("tasks") if t["status"] in s.TERMINAL]
    for record in repository.list("improvements", status="promoted"):
        if record.get("target_kind") != "workflow":
            continue
        watched = 0
        for task in sorted(tasks, key=lambda t: (t.get("finished_at") or t["updated_at"], t["id"])):
            flow = task["snapshot"]["workflow"]
            key = [task.get("finished_at") or task["updated_at"], task["id"]]
            if (
                task["project_id"] != record["project_id"]
                or task.get("reason_code") != "quality_not_met"
                or task["created_at"] <= record["promoted_at"]
                or task["request"]["mode"] != "local"
                or flow.get("definition_id") != record["workflow_id"]
                or flow.get("version") != record["candidate_version"]
                or key <= record.get("observed_through", [])
            ):
                continue
            if watched >= 2:
                break
            watched += 1
            try:
                if any(
                    s.digest(record[label]) != record[label + "_hash"] for label in ("baseline", "candidate")
                ):
                    continue
                for asset in task["snapshot"]["inputs"]:
                    s.verify_frozen_asset(asset, repository.get("data_assets", asset["id"]))
                task_plugins.validate_frozen(
                    task["snapshot"].get("task_plugin"), repository, task["project_id"]
                )
                pair = {
                    label: await asyncio.to_thread(replay_sample, task, record[label])
                    for label in ("baseline", "candidate")
                }
                with repository.transaction() as tx:
                    fresh = s.required("improvements", record["id"], actor, True, tx)
                    if fresh["status"] != "promoted" or key <= fresh.get("observed_through", []):
                        continue
                    s.required("workflows", fresh["workflow_id"], actor, True, tx)
                    lock_snapshot_dependencies(tx, [task["snapshot"]])
                    for asset in task["snapshot"]["inputs"]:
                        s.verify_frozen_asset(asset, tx.get("data_assets", asset["id"]))
                    task_plugins.validate_frozen(task["snapshot"].get("task_plugin"), tx, task["project_id"])
                    fresh["observed_through"] = key
                    fresh["observed_tasks"] = [*fresh.get("observed_tasks", []), task["id"]][-50:]
                    if not pair["baseline"]["quality"]["passed"] or pair["candidate"]["quality"]["passed"]:
                        tx.put("improvements", fresh)
                        continue
                    fresh["regression_evidence"] = {
                        "task_id": task["id"],
                        "baseline_quality": pair["baseline"]["quality"],
                        "candidate_quality": pair["candidate"]["quality"],
                        "baseline_result_hash": s.digest(pair["baseline"]),
                        "candidate_result_hash": s.digest(pair["candidate"]),
                    }
                    rollback(fresh, actor, tx)
                break
            except (s.DomainError, ValueError, RuntimeError, OSError, KeyError, TypeError):
                continue
    count = 0
    for task in tasks:
        if count >= 2:
            break
        req, snap = task["request"], task["snapshot"]
        flow = snap["workflow"]
        nodes = flow["nodes"]
        if (
            task.get("reason_code") != "quality_not_met"
            or req["mode"] != "local"
            or req["task_type"] not in {"source_summary", "text_analysis"}
            or not flow.get("definition_id")
            or (len(nodes) == 1 and "max_chars" not in nodes[0].get("config", {}))
            or repository.list("improvements", source_task_id=task["id"])
        ):
            continue
        quality = task.get("quality_verdict") or {}
        if quality.get("hard_failures") or {i["code"] for i in quality.get("issues", [])} != {"min_length"}:
            continue
        candidate_nodes = deepcopy(nodes)
        changed = []
        limit = min(20000, req.get("quality", {}).get("min_length", 0))
        if limit < 100:
            continue
        for node in candidate_nodes:
            saved = repository.get("nodes", f"{task['id']}:{task.get('revision', 0)}:{node['id']}") or {}
            output = saved.get("output") or {}
            if output.get("method") == "extractive" and any(
                f.get("truncated") for f in output.get("content", {}).get("fragments", [])
            ):
                node["config"] = {**node.get("config", {}), "max_chars": limit}
                changed.append(node["id"])
            node.pop("agent_version", None)
        if not changed:
            continue
        if any(
            old.get("target_kind") == "workflow"
            and old.get("workflow_id") == flow["definition_id"]
            and old.get("baseline_version") == flow["version"]
            and [
                {key: value for key, value in n.items() if key != "agent_version"}
                for n in old["candidate"]["nodes"]
            ]
            == candidate_nodes
            for old in repository.list("improvements", task["project_id"], status="rolled_back")
        ):
            continue
        selected, used = [], set()
        for sample in [task, *reversed(tasks)]:
            if (
                sample["project_id"] != task["project_id"]
                or sample["request"]["mode"] != "local"
                or sample["snapshot"]["workflow"] != flow
            ):
                continue
            hashes = {a["checksum"] for a in sample["snapshot"]["inputs"]}
            if hashes and not hashes & used:
                selected.append(sample["id"])
                used.update(hashes)
                if len(selected) >= 5:
                    break
        if len(selected) < 2:
            continue
        try:
            record = create(
                {
                    "project_id": task["project_id"],
                    "name": "自动流程覆盖范围候选",
                    "workflow_id": flow["definition_id"],
                    "nodes": candidate_nodes,
                    "sample_task_ids": selected,
                    "source_task_id": task["id"],
                    "coverage_repair": {
                        "failed_check": "min_length",
                        "node_ids": changed,
                        "max_chars": limit,
                    },
                },
                actor,
                repository,
            )
            if record["status"] != "candidate":
                continue
            record = await evaluate(record, actor, repository)
            if record["verdict"]["passed"]:
                improvement.promote(record["id"], actor, repository)
            count += 1
        except s.DomainError as exc:
            if exc.code not in {
                "baseline_changed",
                "evaluation_changed",
                "invalid_evaluation_sample",
                "unsupported_evaluation_scope",
                "input_changed",
                "input_unavailable",
                "revoked_input",
                "expired_input",
                "quality_gate_failed",
            }:
                raise
    return count
