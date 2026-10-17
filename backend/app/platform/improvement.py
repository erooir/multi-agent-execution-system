"""Frozen local replay, measured promotion gates and reversible version selection."""

from __future__ import annotations

import time

from . import service as s
from .contracts import ToolParameters
from .db import now, repo


def _validate_sample_scope(sample, agent_id):
    nodes = sample["snapshot"].get("workflow", {}).get("nodes", [])
    agents = sample["snapshot"].get("agents", [])
    if (
        len(nodes) != 1
        or nodes[0]["agent_id"] != agent_id
        or nodes[0].get("depends_on")
        or len(agents) != 1
        or agents[0].get("structure") != "atomic"
        or agents[0].get("agent_id", agents[0]["id"]) != agent_id
    ):
        s.fail(
            "unsupported_evaluation_scope",
            "当前改进评测仅支持单节点元智能体；不能用局部重放证明复合或完整DAG改进",
            422,
        )


def _verify_sources(record, repository):
    for sample in record["samples"]:
        _validate_sample_scope(sample, record["agent_id"])
        for frozen in sample["snapshot"]["inputs"]:
            current = repository.get(
                "data_assets", frozen["id"], for_update=repository.connection is not None
            )
            if frozen.get("project_id") != record["project_id"]:
                s.fail("permission_denied", "评测来源不能跨项目", 403)
            s.verify_frozen_asset(frozen, current)


def create(body, user, repository=None):
    repository = repository or repo
    with repository.transaction() as tx:
        agent = s.required("agents", body["agent_id"], user, True, tx)
        if agent["project_id"] != body["project_id"] or agent["status"] != "published":
            s.fail("invalid_candidate", "候选必须引用同项目已发布智能体")
        if agent.get("structure") != "atomic":
            s.fail("unsupported_evaluation_scope", "当前改进评测仅支持单节点元智能体", 422)
        if body.get("source_task_id"):
            prior = tx.list("improvements", body["project_id"], source_task_id=body["source_task_id"])
            if prior:
                return prior[0]
        if set(body["tool_ids"]) - set(agent["tool_ids"]):
            s.fail("permission_denied", "改进候选不能扩充工具权限", 403)
        ToolParameters.model_validate(body.get("parameters", {}))
        sample_ids = list(dict.fromkeys(body["sample_task_ids"]))
        if len(sample_ids) < 2:
            s.fail("insufficient_evaluation_data", "至少需要两个独立任务作为冻结样本和保留样本")
        samples = []
        for task_id in sample_ids:
            task = s.required("tasks", task_id, user, repository=tx)
            if task["project_id"] != body["project_id"] or task["status"] not in s.TERMINAL:
                s.fail("invalid_evaluation_sample", "评测样本必须是同项目已结束任务")
            if task["request"]["mode"] != "local":
                s.fail(
                    "unsupported_evaluation_mode",
                    "自动发布当前只允许真实本地工具评测；云模型需单独预算和保留集验收",
                )
            if not any(a.get("agent_id", a["id"]) == agent["id"] for a in task["snapshot"]["agents"]):
                s.fail("invalid_evaluation_sample", "样本未使用目标智能体")
            _validate_sample_scope(task, agent["id"])
            samples.append(
                {
                    "task_id": task_id,
                    "request": task["request"],
                    "snapshot": task["snapshot"],
                    "split": "holdout" if len(samples) == len(sample_ids) - 1 else "development",
                }
            )
        baseline = tx.get("agent_versions", f"{agent['id']}:{agent['current_version']}")
        candidate = {
            **baseline,
            "instructions": body["instructions"],
            "tool_ids": body["tool_ids"],
            "parameters": body.get("parameters", {}),
        }
        record = tx.put(
            "improvements",
            {
                **body,
                "status": "candidate",
                "baseline_version": agent["current_version"],
                "baseline": baseline,
                "candidate": candidate,
                "candidate_hash": s.digest(candidate),
                "samples": samples,
                "dataset_hash": s.digest(samples),
                "policy": {
                    "no_hard_failures": True,
                    "no_regression": True,
                    "strict_quality_gain": True,
                    "permissions_unchanged": True,
                    "scope": "local_atomic_single_node",
                },
                "created_by": user["id"],
            },
        )
        _verify_sources(record, tx)
        s.audit(tx, body["project_id"], "improvement.created", record["id"], user)
        return record


async def evaluate(candidate_id, user, repository=None):
    from .quality import deterministic_quality
    from .runtime import execute_local

    repository = repository or repo
    record = s.required("improvements", candidate_id, user, True, repository)
    if record["status"] in ("promoted", "rolled_back"):
        s.fail("invalid_state", "已发布候选不能重新评测", 409)
    if s.digest(record["samples"]) != record["dataset_hash"]:
        s.fail("evaluation_changed", "冻结评测数据校验失败")
    if s.digest(record["candidate"]) != record.get("candidate_hash"):
        s.fail("evaluation_changed", "冻结候选配置校验失败")
    _verify_sources(record, repository)
    results = []
    for sample in record["samples"]:
        inputs = sample["snapshot"]["inputs"]
        pair = {"task_id": sample["task_id"], "split": sample["split"]}
        for label in ("baseline", "candidate"):
            version = record[label]
            started = time.perf_counter()
            config = dict(version.get("parameters", {}))
            nodes = sample["snapshot"]["workflow"]["nodes"]
            target_nodes = [n for n in nodes if n["agent_id"] == record["agent_id"]]
            if target_nodes:
                config.update(target_nodes[0].get("config", {}))
            try:
                task_type = sample["request"]["task_type"]
                if task_type not in version["tool_ids"]:
                    raise ValueError("candidate_tool_missing")
                output = execute_local(task_type, inputs, config)
                quality = deterministic_quality(sample["request"], output, inputs)
                pair[label] = {
                    "output": output,
                    "quality": quality,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                    "cost_cny": 0,
                }
            except (ValueError, RuntimeError, OSError, TypeError, KeyError) as exc:
                pair[label] = {
                    "output": None,
                    "quality": {
                        "passed": False,
                        "hard_failures": [type(exc).__name__],
                        "issues": [getattr(exc, "code", "tool_execution_failed")],
                    },
                    "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                    "cost_cny": 0,
                }
        results.append(pair)
    base_pass = sum(p["baseline"]["quality"]["passed"] for p in results)
    candidate_pass = sum(p["candidate"]["quality"]["passed"] for p in results)
    regression = any(
        p["baseline"]["quality"]["passed"] and not p["candidate"]["quality"]["passed"] for p in results
    )
    hard_fail = any(p["candidate"]["quality"].get("hard_failures") for p in results)
    holdout_pass = all(p["candidate"]["quality"]["passed"] for p in results if p["split"] == "holdout")
    passed = not regression and not hard_fail and holdout_pass and candidate_pass > base_pass
    verdict = {
        "passed": passed,
        "baseline_passed": base_pass,
        "candidate_passed": candidate_pass,
        "sample_count": len(results),
        "regression": regression,
        "hard_failure": hard_fail,
        "holdout_passed": holdout_pass,
        "reason": "measured_quality_gain" if passed else "no_quality_gain_or_regression",
        "limitation": "结论仅适用于冻结的本地工具样本，不代表语义提示词或未测试任务普遍提升",
    }
    with repository.transaction() as tx:
        current = s.required("improvements", candidate_id, user, True, tx)
        if current["updated_at"] != record["updated_at"] or current["status"] in ("promoted", "rolled_back"):
            s.fail("evaluation_changed", "评测期间候选已变化，请重试", 409)
        _verify_sources(record, tx)
        evaluation = tx.put(
            "evaluations",
            {
                "project_id": record["project_id"],
                "candidate_id": candidate_id,
                "dataset_hash": record["dataset_hash"],
                "candidate_hash": record["candidate_hash"],
                "status": "passed" if passed else "rejected",
                "verdict": verdict,
                "samples": results,
                "evaluated_at": now(),
                "method": "actual_local_replay",
            },
        )
        record.update(
            status="evaluated" if passed else "rejected", evaluation_id=evaluation["id"], verdict=verdict
        )
        tx.put("improvements", record)
        s.audit(tx, record["project_id"], "improvement.evaluated", candidate_id, user, verdict)
    return record


def promote(candidate_id, user, repository=None):
    repository = repository or repo
    with repository.transaction() as tx:
        record = s.required("improvements", candidate_id, user, True, tx)
        if record["status"] != "evaluated" or not record.get("verdict", {}).get("passed"):
            s.fail("quality_gate_failed", "只有经过真实评测且质量改善的候选才能晋升", 409)
        evaluation = tx.get("evaluations", record.get("evaluation_id"))
        if (
            s.digest(record["samples"]) != record["dataset_hash"]
            or s.digest(record["candidate"]) != record.get("candidate_hash")
            or not evaluation
            or evaluation.get("status") != "passed"
            or evaluation.get("dataset_hash") != record["dataset_hash"]
            or evaluation.get("candidate_hash") != record.get("candidate_hash")
            or evaluation.get("verdict") != record.get("verdict")
        ):
            s.fail("quality_gate_failed", "候选、数据集或评测证据与冻结结果不一致", 409)
        _verify_sources(record, tx)
        agent = s.required("agents", record["agent_id"], user, True, tx)
        if agent["current_version"] != record["baseline_version"] or agent["status"] != "published":
            s.fail("baseline_changed", "发布基线已变化，必须重新建立候选", 409)
        next_version = max(v["version"] for v in tx.list("agent_versions", definition_id=agent["id"])) + 1
        agent.update(
            instructions=record["candidate"]["instructions"],
            tool_ids=record["candidate"]["tool_ids"],
            parameters=record["candidate"].get("parameters", {}),
            current_version=next_version,
        )
        version = agent["current_version"]
        tx.put(
            "agent_versions",
            {
                **agent,
                "id": f"{agent['id']}:{version}",
                "definition_id": agent["id"],
                "agent_id": agent["id"],
                "version": version,
                "checksum": s.digest(agent),
            },
        )
        tx.put("agents", agent)
        record.update(status="promoted", candidate_version=version, promoted_at=now())
        tx.put("improvements", record)
        s.audit(tx, record["project_id"], "improvement.promoted", candidate_id, user, {"version": version})
        return record


def rollback(candidate_id, user, repository=None):
    repository = repository or repo
    with repository.transaction() as tx:
        record = s.required("improvements", candidate_id, user, True, tx)
        if record["status"] != "promoted":
            s.fail("invalid_state", "只有已晋升版本可以回滚", 409)
        agent = s.required("agents", record["agent_id"], user, True, tx)
        if agent["current_version"] != record["candidate_version"]:
            s.fail("version_changed", "当前版本已经变化，不能覆盖后续发布", 409)
        baseline = record["baseline"]
        for field in ("instructions", "tool_ids", "parameters"):
            agent[field] = baseline.get(
                field, {} if field == "parameters" else [] if field == "tool_ids" else ""
            )
        agent["current_version"] = record["baseline_version"]
        tx.put("agents", agent)
        record.update(status="rolled_back", rolled_back_at=now())
        tx.put("improvements", record)
        s.audit(tx, record["project_id"], "improvement.rolled_back", candidate_id, user)
        return record


async def maintain(repository=None):
    """Preauthorized narrow policy: increase extractive coverage, never permissions/code.

    The opt-in deployment setting is configured before tasks begin. Two independent
    terminal samples and a holdout gain gate are required; running snapshots stay fixed.
    """
    import os

    repository = repository or repo
    if os.environ.get("HANGZHI_AUTO_IMPROVE", "false").lower() != "true":
        return 0
    actor = {"id": "system-improvement", "role": "admin"}
    count = 0
    for task in repository.list("tasks", status="failed"):
        if (
            task.get("reason_code") != "quality_not_met"
            or task["request"]["mode"] != "local"
            or task["request"]["task_type"] not in ("source_summary", "text_analysis")
        ):
            continue
        if repository.list("improvements", source_task_id=task["id"]):
            continue
        agents = task["snapshot"].get("agents", [])
        if not agents:
            continue
        agent_id = agents[0].get("agent_id", agents[0]["id"])
        agent = repository.get("agents", agent_id)
        if not agent or agent["status"] != "published":
            continue
        samples = [
            t
            for t in repository.list("tasks", task["project_id"])
            if t["status"] in s.TERMINAL
            and t["request"]["mode"] == "local"
            and t["request"]["task_type"] == task["request"]["task_type"]
            and any(a.get("agent_id", a["id"]) == agent_id for a in t["snapshot"]["agents"])
            and len(t["snapshot"].get("workflow", {}).get("nodes", [])) == 1
            and len(t["snapshot"]["agents"]) == 1
            and t["snapshot"]["agents"][0].get("structure") == "atomic"
        ]
        if len(samples) < 2:
            continue
        parameters = {
            **agent.get("parameters", {}),
            "max_chars": min(20000, max(4000, task["request"]["quality"].get("min_length", 1) * 2)),
        }
        candidate = create(
            {
                "project_id": task["project_id"],
                "name": "自动覆盖范围候选",
                "agent_id": agent_id,
                "instructions": agent["instructions"],
                "tool_ids": agent["tool_ids"],
                "parameters": parameters,
                "sample_task_ids": [t["id"] for t in samples[-10:]],
                "source_task_id": task["id"],
            },
            actor,
            repository,
        )
        if candidate["status"] not in ("candidate", "rejected", "evaluated"):
            continue
        candidate = await evaluate(candidate["id"], actor, repository)
        if candidate["verdict"]["passed"]:
            promote(candidate["id"], actor, repository)
        count += 1
    # Roll back a promoted version after a real hard quality regression.
    for candidate in repository.list("improvements", status="promoted"):
        failures = [
            t
            for t in repository.list("tasks", candidate["project_id"], status="failed")
            if t.get("reason_code") == "quality_not_met"
            and t["created_at"] > candidate["promoted_at"]
            and any(
                a.get("agent_id", a["id"]) == candidate["agent_id"]
                and a["version"] == candidate["candidate_version"]
                for a in t["snapshot"]["agents"]
            )
        ]
        if failures:
            rollback(candidate["id"], actor, repository)
    return count
