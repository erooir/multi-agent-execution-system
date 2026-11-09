"""Directed, task-local agent communication backed by immutable node outputs.

Envelopes contain references only. The execution engine supplies the allowed
predecessors; model text cannot address another task or grant permissions.
"""

import hashlib
import json
import uuid

from .db import now

POLICY = "directed-context-v1"
MAX_MODEL_CONTEXT_BYTES = 65536


def checksum(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _fail(message):
    from .runtime import TaskFailure

    raise TaskFailure("context_invalid", message)


def envelope(execution, repository, row_id):
    node = repository.get("nodes", row_id)
    if (
        not node
        or node.get("task_id") != execution.task["id"]
        or node.get("project_id") != execution.task["project_id"]
        or node.get("status") != "succeeded"
        or not isinstance(node.get("output"), dict)
    ):
        _fail("消息必须引用本任务已完成的节点产物")
    source_refs = node["output"].get("source_refs", [])
    if set(source_refs) - {asset["id"] for asset in execution.inputs}:
        _fail("消息包含未授权的来源")
    sender = repository.get("instances", node.get("instance_id", ""))
    if sender and (sender["task_id"], sender["project_id"]) != (
        execution.task["id"],
        execution.task["project_id"],
    ):
        _fail("消息发送者不属于当前任务")
    return {
        "kind": "result",
        "source_node_id": row_id,
        "sender_instance_id": node.get("instance_id"),
        "sender_context_id": (sender or {}).get("context_id"),
        "agent_id": node.get("agent_id"),
        "agent_version": node.get("agent_version"),
        "attempt": node.get("attempt"),
        "checksum": checksum(node["output"]),
        "source_refs": source_refs,
        "reused_from": node.get("reused_from"),
    }


def start(execution, tx, instance, upstream_refs, revision, parent_instance_id=None, kind="atomic"):
    execution.check(tx)
    parent = tx.get("instances", parent_instance_id) if parent_instance_id else None
    if parent_instance_id and (
        not parent
        or parent["task_id"] != execution.task["id"]
        or parent["project_id"] != execution.task["project_id"]
        or parent["status"] != "running"
    ):
        _fail("委派方必须是本任务仍在运行的复合智能体")
    messages = [envelope(execution, tx, ref) for ref in upstream_refs]
    context = {
        "policy_version": POLICY,
        "scope": "task_instance",
        "revision": revision,
        "lease_token": execution.token,
        "input_refs": [asset["id"] for asset in execution.inputs],
        "inbox": messages,
        "model_context_max_bytes": MAX_MODEL_CONTEXT_BYTES,
        "history_policy": "fresh_instance_with_explicit_dependencies",
    }
    instance.update(
        context_id=uuid.uuid4().hex,
        context=context,
        parent_instance_id=parent_instance_id,
        kind=kind,
    )
    tx.put("instances", instance)
    tx.event(
        execution.task["id"],
        "agent.context_created",
        {"context_id": instance["context_id"], "policy_version": POLICY, "revision": revision},
        node_id=instance["node_id"],
        instance_id=instance["id"],
    )
    if parent:
        tx.event(
            execution.task["id"],
            "agent.delegated",
            {
                "sender_instance_id": parent["id"],
                "recipient_instance_id": instance["id"],
                "sender_context_id": parent["context_id"],
                "recipient_context_id": instance["context_id"],
            },
            node_id=instance["node_id"],
            instance_id=instance["id"],
        )
    for message in messages:
        tx.event(
            execution.task["id"],
            "agent.message_delivered",
            {
                **message,
                "recipient_instance_id": instance["id"],
                "recipient_context_id": instance["context_id"],
            },
            node_id=instance["node_id"],
            instance_id=instance["id"],
        )
    return instance


def reply(execution, tx, parent_id, row_id):
    execution.check(tx)
    parent = tx.get("instances", parent_id)
    message = envelope(execution, tx, row_id)
    if not parent or parent["task_id"] != execution.task["id"] or parent["status"] != "running":
        _fail("委派结果接收者已失效")
    parent["result_messages"] = [*parent.get("result_messages", []), message]
    tx.put("instances", parent)
    tx.event(
        execution.task["id"],
        "agent.result_returned",
        {**message, "recipient_instance_id": parent_id, "recipient_context_id": parent["context_id"]},
        node_id=parent["node_id"],
        instance_id=parent_id,
    )


def model_view(execution, previous):
    """No silent truncation: oversized exchange ends with an explicit reason."""
    from .runtime import TaskFailure

    validate_messages(execution, previous)
    view = {"policy_version": POLICY, "context": execution.active_context, "dependency_outputs": previous}
    size = len(json.dumps(view, ensure_ascii=False, separators=(",", ":")).encode())
    if size > MAX_MODEL_CONTEXT_BYTES:
        raise TaskFailure("context_limit_exceeded", "智能体依赖上下文超过64KiB，完整产物保留但不截断推断")
    return view


def validate_messages(execution, previous):
    messages = (execution.active_context or {}).get("inbox", [])
    if len(messages) != len(previous):
        _fail("上下文消息与依赖产物数量不一致")
    for message, output in zip(messages, previous, strict=True):
        current = envelope(execution, execution.repository, message["source_node_id"])
        if current["checksum"] != message["checksum"] or checksum(output) != message["checksum"]:
            _fail("上下文依赖产物已改变，不能继续执行")


def release(execution, instance_id, reason="completed"):
    with execution.repository.transaction() as tx:
        tx.fenced(execution.task["id"], execution.worker_id, execution.token)
        instance = tx.get("instances", instance_id)
        if instance and instance["status"] == "running":
            instance.update(status="released", released_at=now(), release_reason=reason)
            tx.put("instances", instance)
