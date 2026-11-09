"""Observe transaction lock requests on real task and improvement paths.

SQLite serializes writers itself; these assertions guard the row-lock order
requested by the same repository operations on DM/PostgreSQL.
"""

from copy import deepcopy

import pytest

from backend.app.platform import improvement, runtime, service, workflow_improvement
from backend.app.platform.db import Repository
from backend.tests import test_v2_platform as platform
from backend.tests.test_task_plugins import plugin

db = platform.db
USER = platform.ADMIN


def trace_locks(monkeypatch):
    locks, seen = [], set()
    original = Repository.get

    def get(self, kind, record_id, *, for_update=False):
        key = (kind, record_id)
        if self.connection is not None and for_update and key not in seen:
            seen.add(key)
            locks.append(key)
        return original(self, kind, record_id, for_update=for_update)

    monkeypatch.setattr(Repository, "get", get)
    return locks


def dependency_locks(locks):
    return [item for item in locks if item[0] in {"data_assets", "capability_definitions"}]


def expected_dependencies(snapshots):
    return [
        *(
            ("data_assets", identifier)
            for identifier in sorted({asset["id"] for snapshot in snapshots for asset in snapshot["inputs"]})
        ),
        *(
            ("capability_definitions", identifier)
            for identifier in sorted({snapshot["task_plugin"]["id"] for snapshot in snapshots})
        ),
    ]


def test_runtime_locks_sources_before_plugin_without_reordering_inputs(db, monkeypatch):
    project = platform.project(db)
    definition = plugin(db, project)
    assets = [platform.asset(db, project, [{"value": mark}]) for mark in ("A", "B")]
    input_ids = sorted((a["id"] for a in assets), reverse=True)
    task = service.create_task(
        platform.task_body(project, assets[0], input_refs=input_ids, plugin_id=definition["id"]), USER, db
    )
    claim = db.claim("lock-order-worker")
    execution = runtime.Execution(db, claim, "lock-order-worker", claim["lease_token"])
    locks = trace_locks(monkeypatch)
    with db.transaction() as tx:
        execution.check(tx)
    assert dependency_locks(locks) == expected_dependencies([task["snapshot"]])
    assert [a["id"] for a in execution.inputs] == input_ids


async def samples(db):
    project = platform.project(db)
    agent = service.save_agent(
        {
            "project_id": project["id"],
            "name": "Synthetic local lock-order agent",
            "capabilities": ["source_summary"],
            "modalities": ["text"],
            "tool_ids": ["source_summary"],
            "parameters": {"max_chars": 100},
        },
        USER,
        repository=db,
    )
    agent = service.agent_action(agent["id"], "publish", USER, db)
    flow = service.save_workflow(
        {
            "project_id": project["id"],
            "name": "Synthetic lock-order flow",
            "task_type": "source_summary",
            "nodes": [
                {"id": "extract", "label": "extract", "agent_id": agent["id"], "depends_on": [], "config": {}}
            ],
        },
        USER,
        repository=db,
    )
    flow = service.publish_workflow(flow["id"], USER, db)
    definitions = [plugin(db, project, "source_digest") for _ in range(2)]
    tasks = []
    for mark, definition in zip(("A", "B"), definitions):
        asset = platform.asset(db, project, mark * 400, modality="text", media_type="text/plain")
        task = await platform.run(
            db,
            platform.task_body(
                project,
                asset,
                workflow_id=flow["id"],
                task_type="source_summary",
                mode="local",
                plugin_id=definition["id"],
                idempotency_key=asset["id"],
                max_revisions=0,
                quality={"min_length": 300},
            ),
        )
        assert task["status"] == "failed"
        tasks.append(task)
    tasks.sort(key=lambda task: task["snapshot"]["inputs"][0]["id"], reverse=True)
    return project, agent, flow, tasks


@pytest.mark.parametrize("kind", ["agent", "workflow"])
async def test_batch_replay_and_promotion_use_one_dependency_lock_order(db, monkeypatch, kind):
    project, agent, flow, tasks = await samples(db)
    sample_ids = [task["id"] for task in tasks]
    if kind == "agent":
        record = improvement.create(
            {
                "project_id": project["id"],
                "name": "Synthetic agent candidate",
                "agent_id": agent["id"],
                "instructions": agent["instructions"],
                "tool_ids": agent["tool_ids"],
                "parameters": {"max_chars": 300},
                "sample_task_ids": sample_ids,
            },
            USER,
            db,
        )
        verify = improvement._verify_sources
        target = ("agents", agent["id"])
    else:
        nodes = deepcopy(flow["nodes"])
        nodes[0].pop("agent_version")
        nodes[0]["config"]["max_chars"] = 300
        record = workflow_improvement.create(
            {
                "project_id": project["id"],
                "name": "Synthetic workflow candidate",
                "workflow_id": flow["id"],
                "nodes": nodes,
                "sample_task_ids": sample_ids,
            },
            USER,
            db,
        )
        verify = workflow_improvement._verify
        target = ("workflows", flow["id"])
    original_samples = deepcopy(record["samples"])
    with monkeypatch.context() as checks:
        locks = trace_locks(checks)
        with db.transaction() as tx:
            verify(record, tx)
        assert dependency_locks(locks) == expected_dependencies([t["snapshot"] for t in tasks])
        assert record["samples"] == original_samples
    evaluated = await improvement.evaluate(record["id"], USER, db)
    assert evaluated["verdict"]["passed"]
    with monkeypatch.context() as checks:
        locks = trace_locks(checks)
        promoted = improvement.promote(record["id"], USER, db)
        assert promoted["status"] == "promoted"
        assert dependency_locks(locks) == expected_dependencies([t["snapshot"] for t in tasks])
        assert locks.index(("projects", project["id"])) < locks.index(("improvements", record["id"]))
        assert locks.index(target) < locks.index(dependency_locks(locks)[0])
    assert db.get("improvements", record["id"])["samples"] == original_samples
