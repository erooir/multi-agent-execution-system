"""Real local Agno execution: directed messages, delegation and isolation."""

import json
from copy import deepcopy

import pytest

from backend.app.platform import agent_context, runtime
from backend.tests.test_v2_runtime import execute, seed
from backend.tests.test_v2_runtime import repository as runtime_repository

repository = runtime_repository


def dag(repository):
    task = seed(repository)
    first = task["snapshot"]["workflow"]["nodes"][0]
    task["snapshot"]["workflow"]["nodes"] = [
        first,
        {**first, "id": "unrelated"},
        {**first, "id": "consumer", "depends_on": ["n1"], "config": {"select_fields": ["name"]}},
    ]
    return repository.put("tasks", task)


async def test_directed_context_contains_only_declared_dependency(repository):
    dag(repository)
    assert (await execute(repository))["status"] == "succeeded"
    instances = {i["node_id"]: i for i in repository.list("instances")}
    inbox = instances["consumer"]["context"]["inbox"]
    assert len(inbox) == 1
    assert inbox[0]["source_node_id"] == "task-1:0:n1"
    assert inbox[0]["sender_instance_id"] == instances["n1"]["id"]
    assert inbox[0]["sender_context_id"] != instances["consumer"]["context_id"]
    assert not instances["unrelated"]["context"]["inbox"]
    assert "alpha" not in json.dumps(instances)
    events = repository.list("events")
    messages = [e for e in events if e["type"] == "agent.message_delivered"]
    assert len(messages) == 1
    assert messages[0]["payload"]["recipient_instance_id"] == instances["consumer"]["id"]
    assert all("alpha" not in json.dumps(e["payload"]) for e in events)


async def test_composite_delegates_and_receives_results_with_frozen_child_identity(repository):
    task = seed(repository)
    child = task["snapshot"]["agents"][0]
    parent = {
        **child,
        "id": "parent",
        "agent_id": "parent",
        "structure": "composite",
        "members": [{"agent_id": "agent-1", "version": 1}] * 2,
    }
    task["snapshot"]["agents"].append(parent)
    task["snapshot"]["workflow"]["nodes"][0]["agent_id"] = "parent"
    repository.put("tasks", task)
    assert (await execute(repository))["status"] == "succeeded"
    instances = repository.list("instances")
    coordinator = next(i for i in instances if i["kind"] == "coordinator")
    children = [i for i in instances if i["kind"] == "atomic"]
    assert len(children) == 2
    assert all(i["parent_instance_id"] == coordinator["id"] for i in children)
    assert len(coordinator["result_messages"]) == 2
    assert all(i["status"] == "released" for i in instances)
    nodes = repository.list("nodes")
    assert all(n["agent_id"] == "agent-1" for n in nodes)
    assert len({i["context_id"] for i in instances}) == 3
    assert {e["type"] for e in repository.list("events")} >= {"agent.delegated", "agent.result_returned"}


async def test_message_rejects_foreign_task_and_mutated_output(repository):
    dag(repository)
    await execute(repository)
    seed(repository, task_id="task-2")
    claim = repository.claim("holder")
    execution = runtime.Execution(repository, claim, "holder", claim["lease_token"])
    with pytest.raises(runtime.TaskFailure, match="本任务"):
        agent_context.envelope(execution, repository, "task-1:0:n1")
    own = repository.get("tasks", "task-1")
    execution.task = own
    consumer = next(i for i in repository.list("instances", task_id="task-1") if i["node_id"] == "consumer")
    execution.active_context = consumer["context"]
    output = deepcopy(repository.get("nodes", "task-1:0:n1")["output"])
    output["content"]["records"][0]["name"] = "forged"
    with pytest.raises(runtime.TaskFailure, match="改变"):
        agent_context.validate_messages(execution, [output])


async def test_coordinator_does_not_claim_completion_when_second_member_fails(repository, monkeypatch):
    task = seed(repository)
    child = task["snapshot"]["agents"][0]
    task["snapshot"]["agents"].append(
        {
            **child,
            "id": "parent",
            "agent_id": "parent",
            "structure": "composite",
            "members": [{"agent_id": "agent-1", "version": 1}] * 2,
        }
    )
    task["snapshot"]["workflow"]["nodes"][0]["agent_id"] = "parent"
    repository.put("tasks", task)
    real, calls = runtime.execute_local, 0

    def fail_second(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise runtime.TaskFailure("fixture_failure", "Synthetic failure")
        return real(*args, **kwargs)

    monkeypatch.setattr(runtime, "execute_local", fail_second)
    assert (await execute(repository))["status"] == "failed"
    parent = next(i for i in repository.list("instances") if i["kind"] == "coordinator")
    assert parent["release_reason"] == "interrupted"
    assert len(parent["result_messages"]) == 1


async def test_context_bound_is_explicit_and_does_not_truncate(repository, monkeypatch):
    dag(repository)
    await execute(repository)
    task = repository.get("tasks", "task-1")
    execution = runtime.Execution(repository, task, "unused", 1)
    instance = next(i for i in repository.list("instances") if i["node_id"] == "consumer")
    execution.active_context = instance["context"]
    previous = [repository.get("nodes", "task-1:0:n1")["output"]]
    view = agent_context.model_view(execution, previous)
    assert view["dependency_outputs"] == previous
    monkeypatch.setattr(agent_context, "MAX_MODEL_CONTEXT_BYTES", 10)
    with pytest.raises(runtime.TaskFailure) as exc:
        agent_context.model_view(execution, previous)
    assert exc.value.code == "context_limit_exceeded"


async def test_cloud_prompts_receive_sender_envelope_but_reviewer_has_independent_context(
    repository, monkeypatch
):
    task = seed(repository, mode="cloud", task_type="source_summary", content="Public fixture source")
    node = task["snapshot"]["workflow"]["nodes"][0]
    task["snapshot"]["workflow"]["nodes"].append({**node, "id": "n2", "depends_on": ["n1"]})
    repository.put("tasks", task)
    prompts = []

    async def complete(prompt, **kwargs):
        prompts.append((kwargs["purpose"], json.loads(prompt)))
        result = (
            {
                "text": "Public fixture source",
                "citations": [{"source_id": "input-1", "quote": "Public fixture source"}],
            }
            if kwargs["purpose"] == "v2_execution"
            else {"verdict": "passed", "issues": [], "evidence_refs": ["input-1"], "suggested_changes": []}
        )
        return {"text": json.dumps(result), "model": "explicit-test-double"}

    monkeypatch.setattr(runtime.model_gateway, "complete", complete, raising=False)
    assert (await execute(repository))["status"] == "succeeded"
    execution_prompts = [p for purpose, p in prompts if purpose == "v2_execution"]
    first, second = [p["agent_exchange"] for p in execution_prompts]
    assert first["context"]["context_id"] != second["context"]["context_id"]
    assert second["context"]["inbox"][0]["sender_context_id"] == first["context"]["context_id"]
    assert second["dependency_outputs"][0]["content"]["text"] == "Public fixture source"
    assert all("agent_exchange" not in p for purpose, p in prompts if purpose == "v2_independent_review")


async def test_revisions_have_fresh_context_and_recovery_references_original_output(repository):
    seed(repository, quality={"required_fields": ["missing"]}, max_revisions=1)
    assert (await execute(repository))["status"] == "failed"
    instances = repository.list("instances")
    assert len({i["context_id"] for i in instances}) == 2
    assert {i["context"]["revision"] for i in instances} == {0, 1}
    assert all(
        i["context"]["history_policy"] == "fresh_instance_with_explicit_dependencies" for i in instances
    )
