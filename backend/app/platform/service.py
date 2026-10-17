"""Versioned assets, task intake, project policy and governed improvement."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .contracts import AgentInput, AssetInput, TaskInput, ToolParameters, WorkflowInput
from .db import ROOT, TERMINAL, now, repo

ASSET_DIR = ROOT / ".local" / "platform-assets"
TOOLS = {"structured_transform", "source_summary", "text_analysis", "image_inspect"}
CAPABILITIES = [
    {
        "id": "structured_transform",
        "name": "结构化数据整理",
        "modalities": ["structured", "text"],
        "available": True,
        "modes": ["local", "rehearsal"],
        "tool_id": "structured_transform",
        "description": "真实 JSON / CSV 校验、筛选、去重和排序",
    },
    {
        "id": "source_summary",
        "name": "来源文本整理",
        "modalities": ["text", "structured"],
        "available": True,
        "modes": ["local", "cloud", "rehearsal"],
        "tool_id": "source_summary",
        "description": "本地提取原文片段；云端摘要需公开数据授权",
    },
    {
        "id": "text_analysis",
        "name": "文本分析",
        "modalities": ["text"],
        "available": True,
        "modes": ["local", "cloud", "rehearsal"],
        "tool_id": "text_analysis",
        "description": "本地为提取式整理，语义分析需要网关模型",
    },
    {
        "id": "image_inspect",
        "name": "图像文件检查",
        "modalities": ["image"],
        "available": True,
        "modes": ["local", "rehearsal"],
        "tool_id": "image_inspect",
        "description": "解码、格式、尺寸和色彩模式，不等同于图像理解",
    },
    {
        "id": "audio_transcribe",
        "name": "音频转写",
        "modalities": ["audio"],
        "available": False,
        "modes": [],
        "description": "等待上游或本地转写服务的实际能力验收",
    },
    {
        "id": "video_analyze",
        "name": "视频理解",
        "modalities": ["video"],
        "available": False,
        "modes": [],
        "description": "尚未接入经过验证的视频模型",
    },
]


class DomainError(ValueError):
    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def fail(code, message, status=400):
    raise DomainError(code, message, status)


def digest(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            raise ValueError()
        return parsed.astimezone(UTC)
    except (AttributeError, TypeError, ValueError):
        fail("invalid_timestamp", "时间必须为含时区的 ISO8601 格式", 422)


def project_access(project_id, user, write=False, repository=None):
    repository = repository or repo
    project = repository.get("projects", project_id, for_update=write and repository.connection is not None)
    if not project:
        fail("not_found", "项目不存在", 404)
    if user.get("role") != "admin" and user["id"] not in project.get("members", []):
        fail("permission_denied", "没有此项目的访问权限", 403)
    if write and user.get("role") not in ("admin", "operator"):
        fail("permission_denied", "当前身份只允许读取", 403)
    return project


def required(kind, record_id, user, write=False, repository=None):
    repository = repository or repo
    record = repository.get(kind, record_id)
    if not record:
        fail("not_found", "记录不存在", 404)
    project_access(record["project_id"], user, write, repository)
    if write and repository.connection is not None:
        record = repository.get(kind, record_id, for_update=True)
    return record


def visible(kind, user, repository=None):
    repository = repository or repo
    projects = {
        p["id"]
        for p in repository.list("projects")
        if user["role"] == "admin" or user["id"] in p.get("members", [])
    }
    return [r for r in repository.list(kind) if r.get("project_id") in projects]


def audit(tx, project_id, action, entity_id, user, detail=None):
    return tx.put(
        "audits",
        {
            "project_id": project_id,
            "action": action,
            "entity_id": entity_id,
            "actor": user["id"],
            "detail": detail or {},
        },
    )


def create_project(body, user, repository=None):
    repository = repository or repo
    if user["role"] not in ("admin", "operator"):
        fail("permission_denied", "只读身份不能创建项目", 403)
    with repository.transaction() as tx:
        project = tx.put(
            "projects", {**body, "members": [user["id"]], "owner_id": user["id"], "status": "active"}
        )
        seed_templates(tx, project["id"])
        audit(tx, project["id"], "project.created", project["id"], user)
        return project


def seed_templates(tx, project_id):
    """Executable, published tool definitions; no demo tasks or fabricated metrics."""
    for capability in CAPABILITIES[:4]:
        agent_id = f"{project_id[:12]}-{capability['id']}"
        agent = AgentInput(
            project_id=project_id,
            name=capability["name"],
            description=capability["description"],
            capabilities=[capability["id"]],
            modalities=capability["modalities"],
            tool_ids=[capability["id"]],
            model_policy="cloud" if "cloud" in capability["modes"] else "local",
        ).model_dump()
        agent.update(id=agent_id, status="published", current_version=1, built_in=True)
        tx.put("agents", agent)
        tx.put(
            "agent_versions",
            {
                **agent,
                "id": f"{agent_id}:1",
                "definition_id": agent_id,
                "agent_id": agent_id,
                "version": 1,
                "checksum": digest(agent),
            },
        )
        workflow_id = f"{project_id[:12]}-flow-{capability['id']}"
        wf = {
            "id": workflow_id,
            "project_id": project_id,
            "name": capability["name"],
            "description": "可复用的单智能体流程",
            "task_type": capability["id"],
            "nodes": [
                {
                    "id": "process",
                    "label": capability["name"],
                    "agent_id": agent_id,
                    "agent_version": 1,
                    "depends_on": [],
                    "config": {},
                }
            ],
            "status": "published",
            "current_version": 1,
        }
        tx.put("workflows", wf)
        tx.put(
            "workflow_versions", {**wf, "id": f"{workflow_id}:1", "definition_id": workflow_id, "version": 1}
        )


def validate_agent(body, tx, agent_id=None):
    ToolParameters.model_validate(body.get("parameters", {}))
    if set(body["tool_ids"]) - TOOLS:
        fail("tool_not_allowed", "包含未登记工具")
    if body["structure"] == "atomic" and body["members"]:
        fail("invalid_composite", "元智能体不能包含成员")
    if body["structure"] == "composite" and not body["members"]:
        fail("invalid_composite", "复合智能体必须包含成员")

    def walk(ref, chain, depth):
        if depth > 4 or ref["agent_id"] in chain:
            fail("invalid_composite", "复合智能体存在循环或超过4层")
        member = tx.get("agent_versions", f"{ref['agent_id']}:{ref['version']}")
        definition = tx.get("agents", ref["agent_id"])
        if not member or not definition or definition["status"] != "published":
            fail("unpublished_dependency", "成员必须绑定已发布的可用版本")
        if member["project_id"] != body["project_id"]:
            fail("permission_denied", "复合成员不能跨项目", 403)
        if set(member.get("tool_ids", [])) - set(body["tool_ids"]):
            fail("permission_denied", "成员工具超出复合边界授权", 403)
        for child in member.get("members", []):
            walk(child, chain | {ref["agent_id"]}, depth + 1)

    for ref in body["members"]:
        walk(ref, {agent_id} if agent_id else set(), 1)


def save_agent(body, user, agent_id=None, repository=None):
    repository = repository or repo
    body = AgentInput.model_validate(body).model_dump()
    with repository.transaction() as tx:
        project_access(body["project_id"], user, True, tx)
        old = required("agents", agent_id, user, True, tx) if agent_id else {}
        if old.get("project_id", body["project_id"]) != body["project_id"]:
            fail("immutable_project", "不能移动智能体所属项目")
        if old.get("status") in ("retired", "destroyed"):
            fail("invalid_state", "退役或销毁的智能体不可编辑", 409)
        validate_agent(body, tx, agent_id)
        result = tx.put(
            "agents", {**old, **body, "status": "draft", "current_version": old.get("current_version", 0)}
        )
        audit(tx, body["project_id"], "agent.saved", result["id"], user)
        return result


def agent_action(agent_id, action, user, repository=None):
    repository = repository or repo
    with repository.transaction() as tx:
        agent = required("agents", agent_id, user, True, tx)
        status = agent["status"]
        if status == "destroyed":
            fail("invalid_state", "智能体已经销毁", 409)
        if action == "publish":
            if status == "retired":
                fail("invalid_state", "退役智能体不能发布", 409)
            validate_agent(agent, tx, agent_id)
            version = (
                max((v["version"] for v in tx.list("agent_versions", definition_id=agent_id)), default=0) + 1
            )
            agent.update(current_version=version, status="published")
            tx.put(
                "agent_versions",
                {
                    **agent,
                    "id": f"{agent_id}:{version}",
                    "definition_id": agent_id,
                    "agent_id": agent_id,
                    "version": version,
                    "checksum": digest(agent),
                },
            )
        elif action == "disable":
            if status != "published":
                fail("invalid_state", "只有已发布智能体可以停用", 409)
            agent["status"] = "disabled"
        elif action == "enable":
            if status != "disabled":
                fail("invalid_state", "只有停用智能体可以启用", 409)
            agent["status"] = "published"
        elif action == "retire":
            agent["status"] = "retired"
        elif action == "destroy":
            workflows = [
                w
                for w in tx.list("workflows", agent["project_id"])
                if any(n["agent_id"] == agent_id for n in w["nodes"])
            ]
            composites = [
                a
                for a in tx.list("agents", agent["project_id"])
                if a["status"] != "destroyed" and any(m["agent_id"] == agent_id for m in a.get("members", []))
            ]
            active = [
                t
                for t in tx.list("tasks", agent["project_id"])
                if t["status"] not in TERMINAL
                and any(
                    a.get("agent_id", a["id"]) == agent_id for a in t.get("snapshot", {}).get("agents", [])
                )
            ]
            if workflows or composites or active:
                fail("reference_protected", "仍被流程、复合定义或运行任务引用，不能销毁", 409)
            agent["status"] = "destroyed"
        else:
            fail("invalid_action", "未知生命周期操作")
        result = tx.put("agents", agent)
        audit(tx, agent["project_id"], f"agent.{action}", agent_id, user)
        return result


def validate_nodes(nodes, tx, project_id):
    ids = [n["id"] for n in nodes]
    if len(set(ids)) != len(ids):
        fail("invalid_graph", "节点ID重复")
    completed = set()
    while len(completed) < len(nodes):
        ready = [n for n in nodes if n["id"] not in completed and set(n["depends_on"]) <= completed]
        if not ready:
            fail("invalid_graph", "流程存在循环或缺失依赖")
        for node in ready:
            agent = tx.get("agents", node["agent_id"])
            if not agent or agent["project_id"] != project_id or agent["status"] != "published":
                fail("unpublished_dependency", "流程节点需要同项目已发布智能体")
            allowed = {
                "operation",
                "select_fields",
                "filter",
                "sort_by",
                "descending",
                "deduplicate",
                "max_chars",
            }
            config = node.get("config", {})
            ToolParameters.model_validate(config)
            if set(config) - allowed:
                fail("invalid_config", "节点配置含未授权字段")
            if config.get("operation", "normalize") not in {
                "normalize",
                "select",
                "deduplicate",
                "sort",
                "filter",
            }:
                fail("invalid_config", "未登记的结构化操作")
            node["agent_version"] = agent["current_version"]
            completed.add(node["id"])


def save_workflow(body, user, workflow_id=None, repository=None):
    repository = repository or repo
    body = WorkflowInput.model_validate(body).model_dump()
    with repository.transaction() as tx:
        project_access(body["project_id"], user, True, tx)
        old = required("workflows", workflow_id, user, True, tx) if workflow_id else {}
        if old and old["project_id"] != body["project_id"]:
            fail("immutable_project", "不能移动流程所属项目")
        validate_nodes(body["nodes"], tx, body["project_id"])
        result = tx.put(
            "workflows", {**old, **body, "status": "draft", "current_version": old.get("current_version", 0)}
        )
        audit(tx, body["project_id"], "workflow.saved", result["id"], user)
        return result


def publish_workflow(workflow_id, user, repository=None):
    repository = repository or repo
    with repository.transaction() as tx:
        workflow = required("workflows", workflow_id, user, True, tx)
        validate_nodes(workflow["nodes"], tx, workflow["project_id"])
        next_version = (
            max((v["version"] for v in tx.list("workflow_versions", definition_id=workflow_id)), default=0)
            + 1
        )
        workflow.update(status="published", current_version=next_version)
        tx.put(
            "workflow_versions",
            {
                **workflow,
                "id": f"{workflow_id}:{workflow['current_version']}",
                "definition_id": workflow_id,
                "version": workflow["current_version"],
            },
        )
        tx.put("workflows", workflow)
        audit(tx, workflow["project_id"], "workflow.published", workflow_id, user)
        return workflow


def create_asset(body, user, repository=None, file_data=None):
    repository = repository or repo
    body = AssetInput.model_validate(body).model_dump()
    project_access(body["project_id"], user, True, repository)
    serialized = (
        json.dumps(
            body["content"], ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
        if not isinstance(body["content"], str)
        else body["content"].encode()
    )
    if len(serialized) > 2_000_000:
        fail("input_too_large", "内联内容上限2MB", 413)
    if body["modality"] in ("image", "audio", "video") and not file_data:
        fail("file_required", "媒体数据需通过文件上传登记", 422)
    expires = datetime.now(UTC) + timedelta(days=body["retention_days"])
    if body.get("expires_at"):
        expires = min(expires, timestamp(body["expires_at"]))
    if expires <= datetime.now(UTC):
        fail("expired_input", "不能登记已过期数据")
    body.update(
        id=uuid.uuid4().hex,
        status="active",
        expires_at=expires.isoformat(),
        received_at=now(),
        checksum=hashlib.sha256(file_data or serialized).hexdigest(),
        bytes=len(file_data or serialized),
    )
    if file_data:
        ASSET_DIR.mkdir(parents=True, exist_ok=True)
        target = ASSET_DIR / body["id"]
        target.write_bytes(file_data)
        body["content_ref"] = str(target)
    with repository.transaction() as tx:
        asset = tx.put("data_assets", body)
        audit(tx, body["project_id"], "data.registered", asset["id"], user)
    return asset


def usable_asset(asset):
    if asset["status"] != "active":
        fail("revoked_input", "来源数据已撤回", 409)
    if timestamp(asset["expires_at"]) <= datetime.now(UTC):
        fail("expired_input", "来源数据已过期", 409)


def verify_frozen_asset(frozen, current):
    if not current:
        fail("missing_input", "冻结来源不存在", 409)
    usable_asset(current)
    keys = (
        "id",
        "project_id",
        "checksum",
        "source_id",
        "source_version",
        "media_type",
        "classification",
        "content_ref",
    )
    if any(frozen.get(key) != current.get(key) for key in keys):
        fail("input_changed", "来源版本、权限或完整性与冻结快照不一致", 409)
    if digest(frozen.get("content")) != digest(current.get("content")):
        fail("input_changed", "登记来源内容与冻结快照不一致", 409)
    if frozen.get("content_ref"):
        path = Path(frozen["content_ref"]).resolve()
        if (
            not path.is_relative_to(ASSET_DIR.resolve())
            or not path.is_file()
            or path.stat().st_size > 20_000_000
        ):
            fail("input_unavailable", "冻结文件不存在或不在受控存储范围内", 409)
        raw = path.read_bytes()
        actual = hashlib.sha256(raw).hexdigest()
        if frozen.get("modality") in {"text", "structured"} and frozen.get("content") is not None:
            try:
                parsed = raw.decode("utf-8-sig")
                if frozen.get("media_type") == "application/json":
                    parsed = json.loads(parsed)
                if parsed != frozen["content"]:
                    fail("input_changed", "已解析内容与冻结文件不一致", 409)
            except (UnicodeDecodeError, json.JSONDecodeError):
                fail("input_changed", "冻结文件已无法按登记格式解析", 409)
    else:
        content = frozen.get("content")
        actual = hashlib.sha256(content.encode()).hexdigest() if isinstance(content, str) else digest(content)
    if actual != frozen.get("checksum"):
        fail("input_changed", "冻结来源内容校验失败", 409)
    return current


def revoke_asset(asset_id, user, repository=None):
    repository = repository or repo
    with repository.transaction() as tx:
        asset = required("data_assets", asset_id, user, True, tx)
        asset.update(status="revoked", revoked_at=now())
        tx.put("data_assets", asset)
        for memory in tx.list("memories", asset["project_id"]):
            if asset_id in memory.get("source_refs", []):
                tx.put("memories", {**memory, "status": "revoked"})
        audit(tx, asset["project_id"], "data.revoked", asset_id, user)
        return asset


def _freeze_agents(tx, refs, project_id):
    result = {}

    def freeze(agent_id, version, depth=0):
        if depth > 4:
            fail("invalid_composite", "组合超过最大深度")
        key = f"{agent_id}:{version}"
        if key in result:
            return
        definition = tx.get("agents", agent_id)
        agent = tx.get("agent_versions", key)
        if (
            not definition
            or definition["status"] != "published"
            or not agent
            or agent["project_id"] != project_id
        ):
            fail("unpublished_dependency", "流程引用智能体当前不可用", 409)
        result[key] = {**agent, "id": agent_id, "agent_id": agent_id, "version": version}
        for member in agent.get("members", []):
            freeze(member["agent_id"], member["version"], depth + 1)

    for ref in refs:
        freeze(ref["agent_id"], ref["agent_version"])
    return list(result.values())


def create_task(body, user, repository=None):
    repository = repository or repo
    body = TaskInput.model_validate(body).model_dump()
    capability = next(item for item in CAPABILITIES if item["id"] == body["task_type"])
    if capability["available"] and body["mode"] not in capability["modes"]:
        fail("unsupported_mode", "能力目录未授权此任务的执行模式", 422)
    if not capability["available"] and body["mode"] == "cloud":
        fail("unsupported_mode", "未经验证的能力不能调用云模型", 422)
    with repository.transaction() as tx:
        project_access(body["project_id"], user, True, tx)
        previous = tx.list("tasks", body["project_id"], idempotency_key=body["idempotency_key"])
        if previous:
            if previous[0].get("request_hash") != digest(body):
                fail("idempotency_conflict", "同一幂等键已有不同任务请求", 409)
            return previous[0]
        inputs = []
        for asset_id in body["input_refs"]:
            asset = required("data_assets", asset_id, user, repository=tx)
            if asset["project_id"] != body["project_id"]:
                fail("permission_denied", "任务不能引用其他项目数据", 403)
            usable_asset(asset)
            if body["mode"] == "cloud" and asset["classification"] != "public":
                fail("data_egress_blocked", "云任务仅能处理已明确授权外发的公开资料", 403)
            inputs.append(asset)
        if body["mode"] == "cloud" and body["max_cost_cny"] <= 0:
            fail("invalid_budget", "云任务必须指定正数费用上限")
        if body["mode"] == "cloud" and os.environ.get("HANGZHI_OFFLINE", "false").lower() == "true":
            fail("offline_policy", "离线部署禁止云模型调用", 403)
        workflow = None
        if body.get("workflow_id"):
            definition = required("workflows", body["workflow_id"], user, repository=tx)
            if (
                definition["project_id"] != body["project_id"]
                or definition["status"] != "published"
                or definition["task_type"] != body["task_type"]
            ):
                fail("workflow_unavailable", "流程未发布、跨项目或与任务类型不匹配")
            workflow = tx.get("workflow_versions", f"{definition['id']}:{definition['current_version']}")
        if not workflow:
            candidates = [
                a
                for a in tx.list("agents", body["project_id"])
                if a["status"] == "published" and body["task_type"] in a["capabilities"]
            ]
            candidates.sort(
                key=lambda a: (a["purpose"] != "specialized", a["structure"] != "atomic", a["id"])
            )
            if candidates:
                a = candidates[0]
                workflow = {
                    "id": "task-scoped",
                    "name": "按能力匹配",
                    "version": 1,
                    "task_type": body["task_type"],
                    "nodes": [
                        {
                            "id": "process",
                            "label": a["name"],
                            "agent_id": a["id"],
                            "agent_version": a["current_version"],
                            "depends_on": [],
                            "config": {},
                        }
                    ],
                }
            else:
                # The worker records a clear unsupported terminal result; never invent an agent.
                workflow = {"id": "unsupported", "name": "能力检查", "nodes": []}
        agents = _freeze_agents(tx, workflow["nodes"], body["project_id"])
        task_id = uuid.uuid4().hex
        task = tx.put(
            "tasks",
            {
                "id": task_id,
                "project_id": body["project_id"],
                "name": body["name"],
                "status": "accepted",
                "reason_code": None,
                "request": body,
                "request_hash": digest(body),
                "idempotency_key": body["idempotency_key"],
                "snapshot": {"agents": agents, "workflow": workflow, "inputs": inputs},
                "attempt": 0,
                "revision": 0,
                "trace_id": task_id,
                "created_by": user["id"],
                "deadline_at": (datetime.now(UTC) + timedelta(seconds=body["deadline_seconds"])).isoformat(),
                "result": None,
            },
        )
        tx.event(task_id, "task.accepted", {"mode": body["mode"], "input_count": len(inputs)})
        audit(tx, body["project_id"], "task.accepted", task_id, user)
        return task


def cancel_task(task_id, user, repository=None):
    repository = repository or repo
    with repository.transaction() as tx:
        task = required("tasks", task_id, user, True, tx)
        if task["status"] in TERMINAL:
            return task
        task["cancel_requested"] = True
        # A worker owns any in-flight cleanup. Unclaimed tasks can terminate immediately.
        if not task.get("lease_owner"):
            task.update(
                status="cancelled",
                reason_code="cancelled_by_user",
                result={
                    "task_id": task_id,
                    "status": "cancelled",
                    "reason_code": "cancelled_by_user",
                    "artifacts": [],
                    "quality_verdict": None,
                    "warnings": [],
                    "lineage": [],
                    "usage": {},
                    "version_refs": [],
                },
            )
        task = tx.put("tasks", task)
        tx.event(task_id, "task.cancel_requested", {})
        return task


def safe_asset(record):
    return {k: v for k, v in record.items() if k not in ("content", "content_ref")}


def safe_task(record):
    return {k: v for k, v in record.items() if k not in ("snapshot", "request_hash", "in_flight")}


def unavailable_sources(source_refs, project_id, repository=None):
    repository = repository or repo
    unavailable = []
    for source_id in source_refs:
        asset = repository.get("data_assets", source_id)
        if not asset or asset.get("project_id") != project_id:
            unavailable.append(source_id)
            continue
        try:
            usable_asset(asset)
        except DomainError:
            unavailable.append(source_id)
    return unavailable


def redact_derived(record, blocked_sources):
    """Keep provenance/audit without returning withdrawn source text or model quotes."""
    output = copy.deepcopy(record)
    if not blocked_sources:
        return output
    output["content_withheld"] = True
    output["withheld_reason"] = "source_revoked_expired_or_unavailable"
    output["unavailable_source_refs"] = list(blocked_sources)
    for key in ("content", "output", "model_review", "quality_verdict", "samples"):
        if key in output:
            output[key] = None
    # Warnings and node errors may include provider-derived text; keep reason code.
    if "warnings" in output:
        output["warnings"] = ["来源已撤回或过期，派生产物内容不再提供"]
    return output


def task_detail(task_id, user, repository=None):
    repository = repository or repo
    task = required("tasks", task_id, user, repository=repository)
    result = safe_task(task)
    snapshot = task["snapshot"]
    result["snapshot"] = {**snapshot, "inputs": [safe_asset(a) for a in snapshot["inputs"]]}
    for kind in ("nodes", "instances", "artifacts", "events"):
        result[kind] = repository.list(kind, task["project_id"], task_id=task_id)
    blocked = unavailable_sources(
        [asset["id"] for asset in snapshot["inputs"]], task["project_id"], repository
    )
    if blocked:
        result["nodes"] = [redact_derived(node, blocked) for node in result["nodes"]]
        result["artifacts"] = [redact_derived(artifact, blocked) for artifact in result["artifacts"]]
        if result.get("result"):
            result["result"] = redact_derived(result["result"], blocked)
        result["quality_verdict"] = None
        result["content_withheld"] = True
    return result
