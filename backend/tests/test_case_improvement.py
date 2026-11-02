"""Full-team replay, immutable binding, deployment opt-in and unseen-input gates."""

from copy import deepcopy

import pytest

from backend.app.casework import team
from backend.app.casework.reference_batches import registry_path, signed_entry
from backend.app.platform import improvement, runtime, service
from backend.app.platform.db import Repository
from scripts.register_reference_batches import install

USER = {"id": "case-test", "role": "admin"}


@pytest.fixture
def context(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "HANGZHI_REFERENCE_BATCHES_FILE", str(tmp_path / "operator" / "reference-batches.json")
    )
    db = Repository(f"sqlite:///{(tmp_path / 'case.sqlite3').as_posix()}")
    monkeypatch.setattr(service, "repo", db)
    monkeypatch.setattr(improvement, "repo", db)
    project = service.create_project({"name": "Synthetic case improvement"}, USER, db)
    team.install(project["id"], USER, db)
    yield db, project["id"]
    db.engine.dispose()


async def terminal(db, pid, mark, code="T06", revisions=0, malformed=False, exact=False, scope="reference"):
    if malformed:
        rows = [{"note": "Unusable document, no original clause"}]
    else:
        rows = [
            {"side": "before", "clause_id": "1", "topic": mark, "clause_text": f"Keep the record {mark}."},
            {
                "side": "after",
                "clause_id": "3",
                "topic": mark,
                "clause_text": f"Keep the record {mark} and source version.",
            },
        ]
    if exact:
        rows[1]["match_id"] = "1"
    # An operator explicitly registers this synthetic fixture in the isolated test registry.
    # The registry changes again for the holdout; replay must use each task's frozen entry.
    entry = signed_entry(
        {
            "project_id": pid,
            "case_id": code,
            "version": service.digest(rows)[:24],
            "sources": [{"sha256": service.digest(rows), "source_version": "1"}],
        }
    )
    if scope == "reference":
        install(registry_path(), [entry], replace=True)
    asset = service.create_asset(
        {
            "project_id": pid,
            "name": "Case source " + mark,
            "modality": "structured",
            "media_type": "application/json",
            "source_id": mark,
            "source_version": "1",
            "provenance": "synthetic",
            "classification": "local",
            "content": rows,
        },
        USER,
        db,
    )

    task = service.create_task(
        {
            "project_id": pid,
            "name": "Case improvement",
            "task_type": "case_analysis",
            "goal": "Compare versions",
            "case_id": code,
            "case_scope": scope,
            "input_refs": [asset["id"]],
            "idempotency_key": "eval-" + asset["id"],
            "mode": "local",
            "max_revisions": revisions,
            "allow_partial": True,
        },
        USER,
        db,
    )
    claim = db.claim("case-eval")
    assert claim["id"] == task["id"]
    return await runtime.process_claim(db, claim, "case-eval")


def candidate(db, pid, jobs, **changes):
    agent = db.get("agents", team.role_id(pid, "analyze", "T06"))
    body = {
        "project_id": pid,
        "agent_id": agent["id"],
        "name": "Improve matching",
        "instructions": agent["instructions"],
        "tool_ids": agent["tool_ids"],
        "parameters": {"case_strategy": "adaptive"},
        "sample_task_ids": [j["id"] for j in jobs],
    }
    return improvement.create({**body, **changes}, USER, db)


async def test_case_team_measured_promotion_and_reversible_binding(context):
    db, pid = context
    one = await terminal(db, pid, "A")
    two = await terminal(db, pid, "B")
    old_flow = deepcopy(one["snapshot"]["workflow"])
    assert one["status"] in {"partial", "failed"}
    c = candidate(db, pid, [one, two])
    out = await improvement.evaluate(c["id"], USER, db)
    assert (
        out["verdict"]["passed"]
        and out["verdict"]["baseline_passed"] == 0
        and out["verdict"]["candidate_passed"] == 2
    )
    evaluation = db.get("evaluations", out["evaluation_id"])
    assert evaluation["method"] == "frozen_case_team_replay"
    assert [s["split"] for s in evaluation["samples"]] == ["development", "holdout"]
    promoted = improvement.promote(c["id"], USER, db)
    assert promoted["candidate_flow_version"] == 2
    assert (
        db.get("workflow_versions", old_flow["id"]) == old_flow
        and db.get("tasks", one["id"])["snapshot"]["workflow"] == old_flow
    )
    fresh = await terminal(db, pid, "C")
    assert (
        fresh["snapshot"]["workflow"]["version"] == 2
        and fresh["snapshot"]["workflow"]["nodes"][1]["agent_version"] == 2
    )
    assert fresh["status"] == "succeeded"
    rolled = improvement.rollback(c["id"], USER, db)
    assert (
        rolled["status"] == "rolled_back"
        and db.get("workflows", old_flow["definition_id"])["current_version"] == 1
    )
    assert db.get("workflow_versions", old_flow["definition_id"] + ":2") is not None


async def test_case_strategy_holdout_content_and_permissions(context):
    db, pid = context
    one = await terminal(db, pid, "same")
    clone = await terminal(db, pid, "same")
    independent = await terminal(db, pid, "different", malformed=True)
    with pytest.raises(service.DomainError, match="互不重叠"):
        candidate(db, pid, [one, clone])
    with pytest.raises(service.DomainError, match="角色"):
        candidate(db, pid, [one, independent], parameters={"case_scope": "B01"})
    with pytest.raises(service.DomainError, match="指令"):
        candidate(db, pid, [one, independent], instructions="replace policy")
    c = candidate(db, pid, [one, independent])
    r = await improvement.evaluate(c["id"], USER, db)
    assert r["status"] == "rejected" and not r["verdict"]["holdout_passed"]
    with pytest.raises(service.DomainError, match="晋升"):
        improvement.promote(c["id"], USER, db)


async def test_case_automatic_opt_in_and_protection_against_later_publication(context, monkeypatch):
    db, pid = context
    one = await terminal(db, pid, "first")
    await terminal(db, pid, "second")
    sample = await terminal(db, pid, "small specimen", scope="sample")
    monkeypatch.delenv("HANGZHI_AUTO_IMPROVE", raising=False)
    assert await improvement.maintain(db) == 0 and not db.list("improvements")
    monkeypatch.setenv("HANGZHI_AUTO_IMPROVE", "true")
    n = await improvement.maintain(db)
    assert n >= 1
    promoted = db.list("improvements", status="promoted")[0]
    assert sample["id"] not in promoted["sample_task_ids"]
    assert promoted["policy"]["scope"] == "local_case_team"
    await terminal(db, pid, "missing", malformed=True)
    await improvement.maintain(db)
    assert db.get("improvements", promoted["id"])["status"] == "promoted"
    wf = service.publish_workflow(promoted["workflow_id"], USER, db)
    assert wf["current_version"] == 3
    with pytest.raises(service.DomainError, match="后续发布"):
        improvement.rollback(promoted["id"], USER, db)
    assert (
        db.get("workflows", wf["id"])["current_version"] == 3
        and db.get("tasks", one["id"])["snapshot"]["workflow"]["version"] == 1
    )


async def test_auto_rollback_requires_replayed_degradation(context, monkeypatch):
    from backend.app.casework import engine

    db, pid = context
    await terminal(db, pid, "one")
    await terminal(db, pid, "two")
    monkeypatch.setenv("HANGZHI_AUTO_IMPROVE", "true")
    await improvement.maintain(db)
    promoted = db.list("improvements", status="promoted")[0]
    original = engine.execute

    def faulty(stage, profile, inputs, prev, read_bytes, feedback, revision, checkpoint=None):
        result = original(stage, profile, inputs, prev, read_bytes, feedback, revision, checkpoint)
        # Isolated fault injection: corrupt an adaptive-only product after release; never bypass independent review.
        if stage == "analyze" and feedback and any(a["source_id"] == "unseen" for a in inputs):
            result["content"]["products"]["clause_map"][0]["before"]["evidence_ids"] = ["not-a-real-citation"]
        return result

    monkeypatch.setattr(engine, "execute", faulty)
    failed = await terminal(db, pid, "unseen", exact=True)
    assert failed["status"] in {"failed", "partial"} and failed["reason_code"] == "quality_not_met"
    await improvement.maintain(db)
    result = db.get("improvements", promoted["id"])
    assert result["status"] == "rolled_back"
    assert result["regression_evidence"]["task_id"] == failed["id"]
    assert result["regression_evidence"]["baseline_quality"]["passed"]
    assert not result["regression_evidence"]["candidate_quality"]["passed"]
    assert db.get("workflows", result["workflow_id"])["current_version"] == 1
    await improvement.maintain(db)
    assert not db.list("improvements", status="promoted")
