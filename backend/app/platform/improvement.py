"""Frozen local replay, measured promotion gates and reversible version selection."""

from __future__ import annotations

import time

from ..casework import replay
from . import service as s
from . import task_plugins
from .contracts import ToolParameters
from .db import now, repo
from .resource_locks import lock_snapshot_dependencies


def _validate_sample_scope(sample, agent_id):
    if sample.get("request", {}).get("task_type") == "case_analysis":
        if sample.get("snapshot", {}).get("task_plugin"):
            s.fail("unsupported_evaluation_scope", "用例重放不支持任务插件", 422)
        replay.validate(sample, agent_id)
        return
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
    if s.digest(record["baseline"]) != record.get("baseline_hash") or s.digest(
        repository.get("agent_versions", record["baseline"]["id"])
    ) != record.get("baseline_hash"):
        s.fail("evaluation_changed", "冻结智能体基线已变化", 409)
    lock_snapshot_dependencies(
        repository, [sample["snapshot"] for sample in record["samples"]], include_agents=True
    )
    for sample in record["samples"]:
        _validate_sample_scope(sample, record["agent_id"])
        try:
            task_plugins.validate_frozen(
                sample["snapshot"].get("task_plugin"), repository, record["project_id"]
            )
        except task_plugins.TaskPluginError as exc:
            s.fail(exc.code, exc.message, 409)
        if record["policy"].get("scope") == replay.SCOPE:
            for prev in sample["snapshot"]["agents"]:
                current = repository.get("agents", prev["agent_id"])
                pinned = repository.get("agent_versions", f"{prev['agent_id']}:{prev['version']}")
                if (
                    not current
                    or current["project_id"] != record["project_id"]
                    or current["status"] != "published"
                    or not pinned
                    or prev != {**pinned, "id": prev["agent_id"], "agent_id": prev["agent_id"]}
                ):
                    s.fail("evaluation_changed", "用例专员已停用，或冻结的专员版本不再匹配", 409)
        for frozen in sample["snapshot"]["inputs"]:
            current = repository.get("data_assets", frozen["id"])
            if frozen.get("project_id") != record["project_id"]:
                s.fail("permission_denied", "评测来源不能跨项目", 403)
            s.verify_frozen_asset(frozen, current)
    evidence = record.get("source_repair")
    if (
        record["policy"].get("scope") == replay.SCOPE
        and record["candidate"]["parameters"].get("case_strategy", "strict") not in {"strict", "adaptive"}
        and not evidence
    ):
        s.fail("evaluation_changed", "用例修复候选缺少开发任务的审核依据", 409)
    if evidence is not None:
        if not isinstance(evidence, dict):
            s.fail("evaluation_changed", "用例修复候选的审核依据无效", 409)
        sample = next(
            (
                x
                for x in record["samples"]
                if x["split"] == "development" and x["task_id"] == evidence.get("task_id")
            ),
            None,
        )
        if (
            not sample
            or evidence.get("action") != record["candidate"]["parameters"].get("case_strategy")
            or replay.source_diagnosis(sample, repository) != evidence
        ):
            s.fail("evaluation_changed", "开发任务的审核证据或修复动作已经变化，拒绝使用候选", 409)


def create(body, user, repository=None):
    repository = repository or repo
    with repository.transaction() as tx:
        agent = s.required("agents", body["agent_id"], user, True, tx)
        if agent["project_id"] != body["project_id"] or agent["status"] != "published":
            s.fail("invalid_candidate", "候选必须引用同项目已发布智能体")
        if agent.get("structure") != "atomic":
            s.fail("unsupported_evaluation_scope", "当前改进评测仅支持单节点元智能体", 422)
        if body.get("source_task_id"):
            tx.get("projects", body["project_id"], for_update=True)
            prior = tx.list("improvements", body["project_id"], source_task_id=body["source_task_id"])
            if prior:
                return prior[0]
        if set(body["tool_ids"]) - set(agent["tool_ids"]):
            s.fail("permission_denied", "改进候选不能扩充工具权限", 403)
        ToolParameters.model_validate(body.get("parameters", {}))
        sample_ids = list(dict.fromkeys(body["sample_task_ids"]))
        if not 2 <= len(sample_ids) <= 20:
            s.fail("insufficient_evaluation_data", "至少需要两个独立任务作为冻结样本和保留样本")
        samples = []
        used_sources = set()
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
            checksums = {a["checksum"] for a in task["snapshot"]["inputs"]}
            if not checksums or checksums & used_sources:
                s.fail("insufficient_evaluation_data", "开发和留出任务的实际来源内容必须互不重叠", 422)
            used_sources.update(checksums)
            samples.append(
                {
                    "task_id": task_id,
                    "request": task["request"],
                    "snapshot": task["snapshot"],
                    "split": "holdout" if len(samples) == len(sample_ids) - 1 else "development",
                }
            )
        baseline = tx.get("agent_versions", f"{agent['id']}:{agent['current_version']}")
        if body.get("coverage_repair") and any(
            x["snapshot"]["agents"][0]["version"] != baseline["version"] for x in samples
        ):
            s.fail("baseline_changed", "截取范围候选的开发或保留任务已不是当前专员基线", 409)
        candidate = {
            **baseline,
            "instructions": body["instructions"],
            "tool_ids": body["tool_ids"],
            "parameters": body.get("parameters", {}),
        }
        is_case = agent.get("case_role") == "analyze"
        if any((x["request"]["task_type"] == "case_analysis") != is_case for x in samples):
            s.fail("unsupported_evaluation_scope", "样本类型必须与当前目标专员一致", 422)
        case_fields = replay.batch_checks(samples, baseline, candidate, tx) if is_case else {}
        record = tx.put(
            "improvements",
            {
                **body,
                "target_kind": "agent",
                "status": "candidate",
                "baseline_version": agent["current_version"],
                "baseline": baseline,
                "baseline_hash": s.digest(baseline),
                **case_fields,
                "candidate": candidate,
                "candidate_hash": s.digest(candidate),
                "samples": samples,
                "dataset_hash": s.digest(samples),
                "policy": {
                    "no_hard_failures": True,
                    "no_regression": True,
                    "strict_quality_gain": True,
                    "permissions_unchanged": True,
                    "scope": replay.SCOPE if is_case else "local_atomic_single_node",
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
    if record.get("target_kind") == "workflow":
        from .workflow_improvement import evaluate as evaluate_workflow

        return await evaluate_workflow(record, user, repository)
    if record["status"] in ("promoted", "rolled_back"):
        s.fail("invalid_state", "已发布候选不能重新评测", 409)
    if s.digest(record["samples"]) != record["dataset_hash"]:
        s.fail("evaluation_changed", "冻结评测数据校验失败")
    if s.digest(record["candidate"]) != record.get("candidate_hash"):
        s.fail("evaluation_changed", "冻结候选配置校验失败")
    is_case = record["policy"].get("scope") == replay.SCOPE
    if is_case and (
        s.digest(record["baseline"]) != record.get("baseline_hash")
        or s.digest(record["baseline_flow"]) != record.get("baseline_flow_hash")
    ):
        s.fail("evaluation_changed", "冻结分析专员或流程基线被修改")
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
                if is_case:
                    import asyncio

                    pair[label] = await asyncio.to_thread(replay.replay_sample, sample, version)
                    continue
                task_type = sample["request"]["task_type"]
                if task_type not in version["tool_ids"]:
                    raise ValueError("candidate_tool_missing")
                output = task_plugins.execute(
                    sample["snapshot"].get("task_plugin"), task_type, inputs, config, execute_local
                )
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
    gained = any(
        p["split"] == "development"
        and p["candidate"]["quality"]["passed"]
        and not p["baseline"]["quality"]["passed"]
        for p in results
    )
    saved_revisions = 0
    if is_case:
        gained, regression, saved_revisions = replay.quality_gain(results)
    passed = not regression and not hard_fail and holdout_pass and gained
    verdict = {
        "passed": passed,
        "baseline_passed": base_pass,
        "candidate_passed": candidate_pass,
        "sample_count": len(results),
        "regression": regression,
        "hard_failure": hard_fail,
        "holdout_passed": holdout_pass,
        "revision_attempts_saved": saved_revisions,
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
                "method": "frozen_case_team_replay" if is_case else "actual_local_replay",
            },
        )
        record.update(
            status="evaluated" if passed else "rejected",
            evaluation_id=evaluation["id"],
            verdict=verdict,
            evaluation_hash=s.digest(evaluation),
        )
        tx.put("improvements", record)
        s.audit(tx, record["project_id"], "improvement.evaluated", candidate_id, user, verdict)
    return record


def promote(candidate_id, user, repository=None):
    repository = repository or repo
    with repository.transaction() as tx:
        record = s.required("improvements", candidate_id, user, True, tx)
        if record.get("target_kind") == "workflow":
            from .workflow_improvement import promote as promote_workflow

            return promote_workflow(record, user, tx)
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
            or s.digest(evaluation) != record.get("evaluation_hash")
        ):
            s.fail("quality_gate_failed", "候选、数据集或评测证据与冻结结果不一致", 409)
        agent = s.required("agents", record["agent_id"], user, True, tx)
        _verify_sources(record, tx)
        if record["policy"].get("scope") == replay.SCOPE and (
            s.digest(record["baseline"]) != record.get("baseline_hash")
            or s.digest(tx.get("agent_versions", record["baseline"]["id"])) != record["baseline_hash"]
        ):
            s.fail("baseline_changed", "分析专员的不可变版本已变化", 409)
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
        if record["policy"].get("scope") == replay.SCOPE:
            replay.bind_promoted(record, version, tx)
        record.update(status="promoted", candidate_version=version, promoted_at=now())
        tx.put("improvements", record)
        s.audit(tx, record["project_id"], "improvement.promoted", candidate_id, user, {"version": version})
        return record


def rollback(candidate_id, user, repository=None):
    repository = repository or repo
    with repository.transaction() as tx:
        record = s.required("improvements", candidate_id, user, True, tx)
        if record.get("target_kind") == "workflow":
            from .workflow_improvement import rollback as rollback_workflow

            return rollback_workflow(record, user, tx)
        if record["status"] != "promoted":
            s.fail("invalid_state", "只有已晋升版本可以回滚", 409)
        agent = s.required("agents", record["agent_id"], user, True, tx)
        if agent["current_version"] != record["candidate_version"]:
            s.fail("version_changed", "当前版本已经变化，不能覆盖后续发布", 409)
        baseline = record["baseline"]
        if s.digest(baseline) != record.get("baseline_hash") or s.digest(
            tx.get("agent_versions", baseline["id"])
        ) != record.get("baseline_hash"):
            s.fail("version_changed", "原智能体版本校验失败，拒绝覆盖", 409)
        if record["policy"].get("scope") == replay.SCOPE:
            immutable = tx.get("agent_versions", f"{agent['id']}:{record['baseline_version']}")
            if s.digest(immutable) != record.get("baseline_hash") or s.digest(baseline) != record.get(
                "baseline_hash"
            ):
                s.fail("version_changed", "原分析专员版本校验失败，拒绝覆盖", 409)
            replay.restore_binding(record, tx)
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


def _extractive_quality(task, config, repository):
    from .quality import deterministic_quality
    from .runtime import execute_local

    snapshot = task["snapshot"]
    nodes = snapshot["workflow"]["nodes"]
    if len(nodes) != 1:
        return None
    try:
        for frozen in snapshot["inputs"]:
            s.verify_frozen_asset(frozen, repository.get("data_assets", frozen["id"]))
        task_plugins.validate_frozen(snapshot.get("task_plugin"), repository, task["project_id"])
        config = {**config, **nodes[0].get("config", {})}
        output = task_plugins.execute(
            snapshot.get("task_plugin"),
            task["request"]["task_type"],
            snapshot["inputs"],
            config,
            execute_local,
        )
        return deterministic_quality(task["request"], output, snapshot["inputs"])
    except (ValueError, RuntimeError, OSError, TypeError, KeyError):
        return None


async def maintain(repository=None):
    """Preauthorized narrow policies operate on observed, source-grounded defects.

    The opt-in deployment setting is configured before tasks begin. Two independent
    terminal samples and a holdout gain gate are required; running snapshots stay fixed.
    """
    import os

    repository = repository or repo
    if os.environ.get("HANGZHI_AUTO_IMPROVE", "false").lower() != "true":
        return 0
    actor = {"id": "system-improvement", "role": "admin"}
    count = await _maintain_cases(repository, actor)
    from .workflow_improvement import maintain as maintain_workflows

    count += await maintain_workflows(repository, actor)
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
        nodes = task["snapshot"]["workflow"]["nodes"]
        quality = task.get("quality_verdict") or {}
        if (
            len(agents) != 1
            or len(nodes) != 1
            or agents[0].get("structure") != "atomic"
            or "max_chars" in nodes[0].get("config", {})
            or quality.get("hard_failures")
            or {issue["code"] for issue in quality.get("issues", [])} != {"min_length"}
        ):
            continue
        agent_id = agents[0].get("agent_id", agents[0]["id"])
        agent = repository.get("agents", agent_id)
        if not agent or agent["status"] != "published" or agents[0]["version"] != agent["current_version"]:
            continue
        node_id = f"{task['id']}:{task.get('revision', 0)}:{nodes[0]['id']}"
        output = (repository.get("nodes", node_id) or {}).get("output") or {}
        fragments = (output.get("content") or {}).get("fragments", [])
        if output.get("method") != "extractive" or not any(row.get("truncated") for row in fragments):
            continue
        limit = min(20000, task["request"]["quality"]["min_length"])
        if limit <= agent.get("parameters", {}).get("max_chars", 1500):
            continue
        parameters = {
            **agent.get("parameters", {}),
            "max_chars": limit,
        }
        if not (_extractive_quality(task, parameters, repository) or {}).get("passed"):
            continue
        samples = [task["id"]]
        used = {a["checksum"] for a in task["snapshot"]["inputs"]}
        for t in reversed(repository.list("tasks", task["project_id"])):
            if (
                t["status"] not in s.TERMINAL
                or t["request"]["mode"] != "local"
                or t["request"]["task_type"] != task["request"]["task_type"]
                or t["snapshot"]["agents"] != task["snapshot"]["agents"]
                or t["snapshot"]["workflow"]["nodes"] != nodes
            ):
                continue
            hashes = {a["checksum"] for a in t["snapshot"]["inputs"]}
            if hashes and not hashes & used and _extractive_quality(t, parameters, repository) is not None:
                samples.append(t["id"])
                used.update(hashes)
                if len(samples) == 10:
                    break
        if len(samples) < 2:
            continue
        proposal = {
            "project_id": task["project_id"],
            "name": "自动覆盖范围候选",
            "agent_id": agent_id,
            "instructions": agent["instructions"],
            "tool_ids": agent["tool_ids"],
            "parameters": parameters,
            "sample_task_ids": samples,
            "source_task_id": task["id"],
            "coverage_repair": {
                "reviewed_node_id": node_id,
                "failed_check": "min_length",
                "max_chars": limit,
            },
        }
        try:
            candidate = create(proposal, actor, repository)
            if candidate["status"] != "candidate":
                continue
            candidate = await evaluate(candidate["id"], actor, repository)
            if candidate["verdict"]["passed"]:
                promote(candidate["id"], actor, repository)
        except s.DomainError as exc:
            if exc.code not in {
                "input_changed",
                "input_unavailable",
                "revoked_input",
                "expired_input",
                "baseline_changed",
                "evaluation_changed",
                "invalid_state",
            }:
                raise
            continue
        count += 1
    # Roll back a promoted version after a real hard quality regression.
    for candidate in repository.list("improvements", status="promoted"):
        if candidate["policy"].get("scope") == replay.SCOPE or candidate.get("target_kind") == "workflow":
            continue
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
        for task in failures:
            if task["request"]["mode"] != "local" or task["request"]["task_type"] not in {
                "source_summary",
                "text_analysis",
            }:
                continue
            try:
                _validate_sample_scope(task, candidate["agent_id"])
            except s.DomainError:
                continue
            before = _extractive_quality(task, candidate["baseline"].get("parameters", {}), repository)
            after = _extractive_quality(task, candidate["candidate"].get("parameters", {}), repository)
            if before and before["passed"] and after and not after["passed"]:
                with repository.transaction() as tx:
                    current = s.required("improvements", candidate["id"], actor, True, tx)
                    agent = s.required("agents", candidate["agent_id"], actor, True, tx)
                    if (
                        current["status"] == "promoted"
                        and agent["current_version"] == current["candidate_version"]
                    ):
                        current["regression_evidence"] = {
                            "task_id": task["id"],
                            "baseline_passed": True,
                            "candidate_failed_checks": [issue["code"] for issue in after["issues"]],
                        }
                        tx.put("improvements", current)
                        rollback(current["id"], actor, tx)
                break
    return count


async def _maintain_cases(repository, actor):
    """Learn a bounded change from a failed review, including failures repaired before the task finished."""
    tasks = [
        t
        for t in repository.list("tasks")
        if t.get("request", {}).get("task_type") == "case_analysis"
        and t["request"].get("mode") == "local"
        and t["status"] in s.TERMINAL
    ]
    failures = [
        t for t in tasks if t.get("reason_code") == "quality_not_met" and t["status"] in {"failed", "partial"}
    ]
    count = 0
    # Watch the already-promoted version first; never roll back on an outage or unrelated bad input alone.
    for candidate in repository.list("improvements", status="promoted"):
        if candidate["policy"].get("scope") != replay.SCOPE:
            continue
        watched = 0
        for t in sorted(failures, key=lambda row: (row.get("finished_at") or row["updated_at"], row["id"])):
            if (
                t["project_id"] != candidate["project_id"]
                or t["created_at"] <= candidate["promoted_at"]
                or not any(
                    a["agent_id"] == candidate["agent_id"] and a["version"] == candidate["candidate_version"]
                    for a in t["snapshot"]["agents"]
                )
            ):
                continue
            key = [t.get("finished_at") or t["updated_at"], t["id"]]
            if key <= candidate.get("observed_through", []):
                continue
            if watched >= 2:
                break
            watched += 1
            evidence = replay.regression_evidence(candidate, t, repository)
            with repository.transaction() as tx:
                fresh = s.required("improvements", candidate["id"], actor, True, tx)
                if fresh["status"] != "promoted":
                    break
                if key <= fresh.get("observed_through", []):
                    continue
                fresh["observed_through"] = key
                fresh["observed_tasks"] = [*fresh.get("observed_tasks", []), t["id"]][-50:]
                if evidence:
                    fresh["regression_evidence"] = evidence
                tx.put("improvements", fresh)
                if evidence:
                    try:
                        rollback(fresh["id"], actor, tx)
                    except s.DomainError as exc:
                        if exc.code != "version_changed":
                            raise
                        s.audit(tx, fresh["project_id"], "improvement.rollback_obsolete", fresh["id"], actor)
                    break
    sources = [
        task
        for task in tasks
        if task.get("reason_code") == "quality_not_met"
        or (task["status"] == "succeeded" and task.get("revision", 0) > 0)
    ]
    for t in sources:
        if count >= 2:
            break
        if repository.list("improvements", source_task_id=t["id"]):
            continue
        code = t["request"]["case_id"]
        agent_id = replay.team.role_id(t["project_id"], "analyze", code)
        agent = repository.get("agents", agent_id)
        if not agent or agent["status"] != "published":
            continue
        try:
            target = replay.validate(t, agent_id)
            if target["version"] != agent["current_version"]:
                continue
            diagnosis = replay.source_diagnosis(t, repository)
            if not diagnosis or diagnosis["action"] == agent["parameters"].get("case_strategy"):
                continue
            action = diagnosis["action"]
            # Do not reintroduce a policy which was rolled back on this same agent and workflow baseline.
            if any(
                old.get("policy", {}).get("scope") == replay.SCOPE
                and old.get("agent_id") == agent_id
                and old.get("baseline_version") == agent["current_version"]
                and old.get("baseline_flow_hash") == s.digest(t["snapshot"]["workflow"])
                and old["candidate"]["parameters"].get("case_strategy") in {"adaptive", action}
                for old in repository.list("improvements", t["project_id"], status="rolled_back")
            ):
                continue
            selected = []
            used = set()
            for sample in [
                t,
                *sorted(
                    (row for row in tasks if row["id"] != t["id"]),
                    key=lambda row: row["created_at"],
                    reverse=True,
                ),
            ]:
                if (
                    sample["project_id"] != t["project_id"]
                    or sample["request"]["case_id"] != code
                    or sample["snapshot"]["workflow"] != t["snapshot"]["workflow"]
                ):
                    continue
                digests = {asset["checksum"] for asset in sample["snapshot"]["inputs"]}
                if not digests or used & digests:
                    continue
                try:
                    _validate_sample_scope(sample, agent_id)
                except s.DomainError as exc:
                    if exc.code != "unsupported_evaluation_scope":
                        raise
                    continue
                selected.append(sample["id"])
                used.update(digests)
                if len(selected) >= 5:
                    break
            if len(selected) < 2:
                continue
            cand = create(
                {
                    "project_id": t["project_id"],
                    "name": f"{code} · {diagnosis['label']}",
                    "agent_id": agent_id,
                    "instructions": agent["instructions"],
                    "tool_ids": agent["tool_ids"],
                    "parameters": {**agent["parameters"], "case_strategy": action},
                    "sample_task_ids": selected,
                    "source_task_id": t["id"],
                },
                actor,
                repository,
            )
            if cand["status"] == "candidate":
                cand = await evaluate(cand["id"], actor, repository)
            if cand["status"] == "evaluated" and cand["verdict"]["passed"]:
                promote(cand["id"], actor, repository)
            count += 1
        except s.DomainError as exc:
            # An expired/revoked source or a stale workflow is not grounds to alter a running configuration.
            if exc.code not in {
                "input_changed",
                "input_unavailable",
                "revoked_input",
                "expired_input",
                "baseline_changed",
                "unsupported_evaluation_scope",
                "unsupported_repair_strategy",
                "evaluation_changed",
                "quality_gate_failed",
            }:
                raise
    return count
