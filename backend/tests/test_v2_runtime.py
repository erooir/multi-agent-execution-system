"""Real Agno/SQL task execution with synthetic inputs and isolated billing.

Only tests named cloud use an explicit gateway double; no paid API is contacted.
"""

import asyncio
import hashlib
import json
import threading
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from PIL import Image

from backend.app.model_gateway import BudgetLedger
from backend.app.platform import runtime
from backend.app.platform.db import Repository
from backend.app.platform.quality import deterministic_quality
from backend.app.platform.worker import run_worker


@pytest.fixture
def repository(tmp_path, monkeypatch):
    repository = Repository(f"sqlite:///{(tmp_path / 'platform.sqlite3').as_posix()}")
    ledger = BudgetLedger(tmp_path / "test-budget.sqlite3")
    monkeypatch.setattr(runtime, "model_gateway", SimpleNamespace(ledger=ledger))
    return repository


def seed(
    repository,
    *,
    task_id="task-1",
    task_type="structured_transform",
    mode="local",
    content=None,
    quality=None,
    config=None,
    **request_overrides,
):
    content = [{"name": " alpha ", "value": 2}, {"name": "beta", "value": 1}] if content is None else content
    asset = repository.put(
        "data_assets",
        {
            "id": "input-1",
            "project_id": "p",
            "status": "active",
            "name": "Synthetic fixture",
            "modality": "structured" if task_type == "structured_transform" else "text",
            "media_type": "application/json" if task_type == "structured_transform" else "text/plain",
            "content": content,
            "classification": "public",
            "checksum": hashlib.sha256(
                (content if isinstance(content, str) else runtime.canonical(content)).encode()
            ).hexdigest(),
            "source_id": "public-fixture",
            "source_version": "1",
            "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        },
    )
    agent = {
        "id": "agent-1",
        "agent_id": "agent-1",
        "version": 1,
        "name": "Fixture tool",
        "structure": "atomic",
        "tool_ids": [task_type],
        "capabilities": [task_type],
        "model_policy": "cloud" if mode == "cloud" else "local",
    }
    request = {
        "name": "Synthetic task",
        "goal": "Organize the provided public records",
        "task_type": task_type,
        "mode": mode,
        "max_cost_cny": 1 if mode == "cloud" else 0,
        "deadline_seconds": 120,
        "max_revisions": 0,
        "allow_partial": False,
        "quality": quality or {},
        **request_overrides,
    }
    task = repository.put(
        "tasks",
        {
            "id": task_id,
            "project_id": "p",
            "status": "accepted",
            "request": request,
            "idempotency_key": task_id,
            "attempt": 0,
            "revision": 0,
            "snapshot": {
                "agents": [agent],
                "inputs": [asset],
                "workflow": {
                    "nodes": [
                        {
                            "id": "n1",
                            "label": "Process",
                            "agent_id": agent["id"],
                            "agent_version": 1,
                            "depends_on": [],
                            "config": config or {},
                        }
                    ]
                },
            },
        },
    )
    return task


async def execute(repository, worker="test-worker"):
    task = repository.claim(worker)
    assert task is not None
    return await runtime.process_claim(repository, task, worker)


def test_real_json_csv_and_exact_excerpts():
    inputs = [
        {
            "id": "csv",
            "modality": "structured",
            "media_type": "text/csv",
            "content": "name,value\n A ,2\nA,2\nB,1\n",
        }
    ]
    result = runtime.execute_local(
        "structured_transform", inputs, {"deduplicate": True, "select_fields": ["name"]}
    )
    assert result["content"]["records"] == [{"name": "A"}, {"name": "B"}]
    assert result["metrics"]["input_records"] == 3
    sources = [
        {
            "id": "source",
            "modality": "text",
            "content": "  A real source. Another sentence.",
            "source_version": "v2",
        }
    ]
    result = runtime.execute_local("source_summary", sources)
    assert result["method"] == "extractive"
    assert result["content"]["fragments"][0]["start"] == 2
    assert deterministic_quality({"task_type": "source_summary"}, result, sources)["passed"]
    result["content"]["fragments"][0]["text"] = "invented"
    assert deterministic_quality({"task_type": "source_summary"}, result, sources)["hard_failures"]


def test_image_inspection_decodes_real_file(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "ASSET_ROOT", tmp_path)
    path = tmp_path / "sample.png"
    Image.new("RGB", (13, 7), color="purple").save(path)
    data = path.read_bytes()
    asset = {
        "id": "image",
        "modality": "image",
        "content_ref": str(path),
        "checksum": hashlib.sha256(data).hexdigest(),
    }
    output = runtime.execute_local("image_inspect", [asset])
    assert output["content"]["images"][0]["width"] == 13
    assert output["content"]["images"][0]["height"] == 7
    assert output["content"]["images"][0]["decoded"] is True
    path.write_bytes(b"not an image")
    with pytest.raises(runtime.TaskFailure, match="完整性"):
        runtime.execute_local("image_inspect", [asset])


async def test_agno_workflow_writes_real_results_and_isolates_context(repository):
    seed(repository)
    finished = await execute(repository)
    assert finished["status"] == "succeeded"
    assert finished["lease_owner"] is None
    assert finished["result"]["usage"]["request_count"] == 0
    artifact = repository.list("artifacts", task_id="task-1")[0]
    assert artifact["content"]["records"][0]["name"] == "alpha"
    events = repository.list("events", task_id="task-1")
    assert {event["type"] for event in events} >= {
        "task.planning",
        "node.completed",
        "quality.checked",
        "task.succeeded",
    }
    assert all("alpha" not in json.dumps(event["payload"]) for event in events)
    first = repository.list("instances", task_id="task-1")[0]
    seed(repository, task_id="task-2")
    await execute(repository)
    second = repository.list("instances", task_id="task-2")[0]
    assert first["context_id"] != second["context_id"]
    assert first["status"] == second["status"] == "released"


@pytest.mark.parametrize("allow_partial,status", [(False, "failed"), (True, "partial")])
async def test_quality_revision_is_bounded_and_honest(repository, allow_partial, status):
    seed(repository, quality={"required_fields": ["missing"]}, max_revisions=2, allow_partial=allow_partial)
    finished = await execute(repository)
    assert finished["status"] == status
    assert finished["reason_code"] == "quality_not_met"
    assert finished["revision"] == 2
    assert len(repository.list("nodes", task_id="task-1")) == 3
    assert bool(finished["result"]["artifacts"]) is allow_partial


@pytest.mark.parametrize(
    "change,reason",
    [
        ("revoke", "input_revoked"),
        ("expire", "input_expired"),
        ("cancel", "cancelled"),
        ("deadline", "deadline_exceeded"),
    ],
)
async def test_preflight_honest_terminal_reasons(repository, change, reason):
    task = seed(repository)
    asset = repository.get("data_assets", "input-1")
    if change == "revoke":
        repository.put("data_assets", {**asset, "status": "revoked"})
    elif change == "expire":
        repository.put("data_assets", {**asset, "expires_at": "2020-01-01T00:00:00+00:00"})
    elif change == "cancel":
        repository.put("tasks", {**task, "cancel_requested": True})
    else:
        repository.put("tasks", {**task, "deadline_at": "2020-01-01T00:00:00+00:00"})
    finished = await execute(repository)
    assert finished["reason_code"] == reason
    assert finished["status"] == ("cancelled" if change == "cancel" else "failed")
    assert repository.list("artifacts") == []


async def test_unsupported_media_never_fabricates_output(repository):
    seed(repository, task_type="audio_transcribe", content="This is not a transcription")
    finished = await execute(repository)
    assert finished["status"] == "failed"
    assert finished["reason_code"] == "unsupported_capability"


async def test_stale_worker_cannot_publish(repository):
    seed(repository)
    first = repository.claim("old", lease_seconds=-1)
    new = repository.claim("new")
    stale = await runtime.process_claim(repository, first, "old")
    assert stale["lease_owner"] == "new"
    assert not repository.list("artifacts")
    assert (await runtime.process_claim(repository, new, "new"))["status"] == "succeeded"


async def test_uncertain_external_dispatch_never_replayed(repository):
    task = seed(repository, mode="cloud", task_type="source_summary", content="A public source")
    repository.put(
        "tasks", {**task, "in_flight": {"purpose": "v2_execution", "started_at": "2026-01-01T00:00:00+00:00"}}
    )
    reservation = runtime.model_gateway.ledger.reserve(0.1, "v2_execution", task["id"])
    runtime.model_gateway.ledger.uncertain(reservation)
    finished = await execute(repository)
    assert finished["reason_code"] == "execution_uncertain"
    assert finished["result"]["usage"]["reserved_cny"] == 0.1
    assert finished["result"]["usage"]["uncertain_count"] == 1


async def test_cloud_executes_separate_review_and_aggregates_ledger(repository, monkeypatch):
    seed(repository, mode="cloud", task_type="source_summary", content="A public source")
    calls = []

    async def complete(prompt, **kwargs):
        calls.append(kwargs["purpose"])
        request_id = runtime.model_gateway.ledger.reserve(0.1, kwargs["purpose"], kwargs["run_id"])
        runtime.model_gateway.ledger.settle(request_id, {"prompt_tokens": 100, "completion_tokens": 50})
        content = (
            {
                "text": "Summary based on a public source",
                "citations": [{"source_id": "input-1", "quote": "A public source"}],
            }
            if kwargs["purpose"] == "v2_execution"
            else {"verdict": "passed", "issues": [], "evidence_refs": ["input-1"], "suggested_changes": []}
        )
        return {"text": json.dumps(content), "model": "explicit-test-double", "request_id": request_id}

    monkeypatch.setattr(runtime.model_gateway, "complete", complete, raising=False)
    finished = await execute(repository)
    assert finished["status"] == "succeeded"
    assert calls == ["v2_execution", "v2_independent_review"]
    assert finished["result"]["usage"]["request_count"] == 2
    assert finished["result"]["usage"]["input_tokens"] == 200
    assert finished["in_flight"] is None


async def test_worker_once_records_heartbeat_and_stops(repository):
    seed(repository)
    await run_worker(repository, worker_id="worker-once", once=True)
    assert repository.get("tasks", "task-1")["status"] == "succeeded"
    worker = repository.get("workers", "worker-once")
    assert worker["status"] == "stopped"
    assert worker["heartbeat_at"]
    assert worker["active_task_ids"] == []


async def test_completed_local_checkpoint_reused_after_takeover(repository, monkeypatch):
    task = seed(repository)
    output = runtime.execute_local("structured_transform", task["snapshot"]["inputs"])
    repository.put(
        "nodes",
        {
            "id": "task-1:0:n1",
            "project_id": "p",
            "task_id": "task-1",
            "node_id": "n1",
            "status": "succeeded",
            "output": output,
        },
    )

    def must_not_replay(*args, **kwargs):
        raise AssertionError("Completed node replayed")

    monkeypatch.setattr(runtime, "execute_local", must_not_replay)
    finished = await execute(repository)
    assert finished["status"] == "succeeded"
    assert repository.list("artifacts")[0]["content"] == output["content"]


async def test_mid_call_takeover_fences_old_result(repository, monkeypatch):
    seed(repository)
    started, release = threading.Event(), threading.Event()
    real = runtime.execute_local
    calls = 0

    def blocking_tool(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            started.set()
            assert release.wait(10)
        return real(*args, **kwargs)

    monkeypatch.setattr(runtime, "execute_local", blocking_tool)
    first_claim = repository.claim("old")
    first = asyncio.create_task(runtime.process_claim(repository, first_claim, "old"))
    assert await asyncio.to_thread(started.wait, 10)
    current = repository.get("tasks", "task-1")
    repository.put("tasks", {**current, "lease_until": 0})
    second_claim = repository.claim("new")
    finished = await runtime.process_claim(repository, second_claim, "new")
    assert finished["status"] == "succeeded"
    release.set()
    assert (await first)["lease_token"] == second_claim["lease_token"]
    assert len(repository.list("artifacts")) == 1
    instances = repository.list("instances", task_id="task-1")
    assert sorted(instance["status"] for instance in instances) == ["abandoned", "released"]


async def test_worker_shutdown_retains_recoverable_claim(repository, monkeypatch):
    seed(repository, mode="cloud", task_type="source_summary", content="A public source")
    started = asyncio.Event()

    async def pending_model(*args, **kwargs):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(runtime.model_gateway, "complete", pending_model, raising=False)
    claim = repository.claim("worker")
    running = asyncio.create_task(runtime.process_claim(repository, claim, "worker"))
    await asyncio.wait_for(started.wait(), 5)
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    stored = repository.get("tasks", "task-1")
    assert stored["status"] == "running"
    assert stored["in_flight"]["purpose"] == "v2_execution"
    repository.put("tasks", {**stored, "lease_until": 0})
    recovered = await runtime.process_claim(repository, repository.claim("replacement"), "replacement")
    assert recovered["reason_code"] == "execution_uncertain"


async def test_no_ledger_allows_local_tools_but_fails_cloud_closed(repository, monkeypatch):
    class MissingLedger:
        @property
        def ledger(self):
            raise RuntimeError("ledger_missing: historical budget unavailable")

    monkeypatch.setattr(runtime, "model_gateway", MissingLedger())
    seed(repository)
    local = await execute(repository)
    assert local["status"] == "succeeded"
    assert local["result"]["usage"]["accounting_available"] is False
    assert local["result"]["usage"]["request_count"] == 0
    seed(repository, task_id="cloud", mode="cloud", task_type="source_summary", content="Public data")
    cloud = await execute(repository)
    assert cloud["status"] == "failed"
    assert cloud["reason_code"] == "ledger_unavailable"
    assert cloud["result"]["usage"]["cost_cny"] is None


async def test_revoke_at_publish_is_checked_inside_transaction(repository, monkeypatch):
    seed(repository)
    publish = runtime._publish

    async def revoke_then_publish(step_input):
        asset = repository.get("data_assets", "input-1")
        repository.put("data_assets", {**asset, "status": "revoked"})
        return await publish(step_input)

    monkeypatch.setattr(runtime, "_publish", revoke_then_publish)
    finished = await execute(repository)
    assert finished["reason_code"] == "input_revoked"
    assert not repository.list("artifacts")


async def test_structured_dag_passes_real_output_to_next_node(repository):
    task = seed(repository, config={"filter": {"value": 2}})
    task["snapshot"]["workflow"]["nodes"].append(
        {
            "id": "n2",
            "label": "Select",
            "agent_id": "agent-1",
            "agent_version": 1,
            "depends_on": ["n1"],
            "config": {"select_fields": ["name"]},
        }
    )
    repository.put("tasks", task)
    finished = await execute(repository)
    assert finished["status"] == "succeeded"
    outputs = repository.list("nodes", task_id="task-1")
    assert outputs[-1]["output"]["content"]["records"] == [{"name": "alpha"}]


async def test_schedule_duplicate_workers_coalesce_and_recheck_principal(repository, monkeypatch):
    from backend.app.platform import service, worker

    actor = {"id": "operator", "role": "operator", "enabled": True}
    monkeypatch.setattr(worker, "store", SimpleNamespace(get=lambda kind, record_id: actor))
    project = service.create_project(
        {"name": "Schedule test", "description": "Synthetic only"}, actor, repository
    )
    asset = repository.put(
        "data_assets",
        {
            "id": "scheduled-input",
            "project_id": project["id"],
            "status": "active",
            "name": "Fixture",
            "modality": "structured",
            "media_type": "application/json",
            "content": [{"value": 1}],
            "classification": "local",
            "source_id": "synthetic",
            "source_version": "1",
            "checksum": hashlib.sha256(runtime.canonical([{"value": 1}]).encode()).hexdigest(),
            "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        },
    )
    due = datetime(2026, 1, 1, tzinfo=UTC).timestamp()
    schedule = repository.put(
        "schedules",
        {
            "id": "schedule",
            "project_id": project["id"],
            "status": "enabled",
            "created_by": "operator",
            "interval_seconds": 60,
            "next_run_at": datetime.fromtimestamp(due, UTC).isoformat(),
            "task_template": {
                "project_id": project["id"],
                "name": "Periodic data",
                "goal": "Normalize records",
                "task_type": "structured_transform",
                "input_refs": [asset["id"]],
                "idempotency_key": "template",
            },
        },
    )
    counts = await asyncio.gather(
        *[asyncio.to_thread(worker.schedule_due, repository, current_time=due + 181) for _ in range(2)]
    )
    assert sum(counts) == 1
    assert len(repository.list("tasks")) == 1
    stored = repository.get("schedules", schedule["id"])
    assert stored["missed_intervals"] == 3
    assert runtime.timestamp(stored["next_run_at"]) == due + 240
    assert (await execute(repository))["status"] == "succeeded"
    actor["enabled"] = False
    assert worker.schedule_due(repository, current_time=due + 241) == 0
    assert repository.get("schedules", schedule["id"])["last_error"] == "schedule_principal_unavailable"
    assert len(repository.list("tasks")) == 1


async def test_rehearsal_output_cannot_be_mistaken_for_inference(repository):
    seed(repository, mode="rehearsal", task_type="source_summary", content="Public text")
    finished = await execute(repository)
    artifact = repository.list("artifacts")[0]
    assert finished["status"] == "succeeded"
    assert finished["result"]["mode"] == "rehearsal"
    assert artifact["rehearsal"] is True
    assert any("不代表离线模型推理" in warning for warning in artifact["warnings"])


async def test_hard_fabricated_citation_cannot_be_approved_or_published(repository, monkeypatch):
    seed(repository, mode="cloud", task_type="source_summary", content="Public source", allow_partial=True)
    calls = []

    async def fabricated(prompt, **kwargs):
        calls.append(kwargs["purpose"])
        return {
            "text": json.dumps(
                {
                    "text": "Plausible but ungrounded",
                    "citations": [{"source_id": "input-1", "quote": "invented evidence"}],
                }
            )
        }

    monkeypatch.setattr(runtime.model_gateway, "complete", fabricated, raising=False)
    finished = await execute(repository)
    assert finished["status"] == "failed"
    assert finished["result"]["quality_verdict"]["hard_failures"][0]["code"] == "grounded_citations"
    assert calls == ["v2_execution"]
    assert not repository.list("artifacts")


async def test_cancellation_does_not_release_a_running_local_tool_early(repository, monkeypatch):
    seed(repository)
    started, release = threading.Event(), threading.Event()
    real = runtime.execute_local

    def blocking_tool(*args, **kwargs):
        started.set()
        assert release.wait(10)
        return real(*args, **kwargs)

    monkeypatch.setattr(runtime, "execute_local", blocking_tool)
    running = asyncio.create_task(runtime.process_claim(repository, repository.claim("worker"), "worker"))
    assert await asyncio.to_thread(started.wait, 10)
    task = repository.get("tasks", "task-1")
    repository.put("tasks", {**task, "cancel_requested": True})
    running.cancel()
    await asyncio.sleep(0.05)
    assert repository.list("instances")[0]["status"] == "running"
    assert not running.done()
    release.set()
    finished = await running
    assert finished["status"] == "cancelled"
    assert repository.list("instances")[0]["status"] == "released"
