"""Public-only semantic handoff boundaries. No calls to any model provider."""

import json
from copy import deepcopy

import pytest
from pydantic import ValidationError

from backend.app import config
from backend.app.model_gateway import model_gateway
from backend.app.platform import service as s
from backend.app.platform.db import Repository
from backend.app.platform.public_evidence import PublicEvidenceRequest, report, submit
from backend.app.platform.quality import combine_review, deterministic_quality
from backend.app.platform.scientific_terms import conflicts

USER = {"id": "analyst", "role": "operator"}


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    monkeypatch.delenv("HANGZHI_REQUIRE_DM", raising=False)
    monkeypatch.setenv("HANGZHI_OFFLINE", "false")
    monkeypatch.setattr(config, "get_api_key", lambda: "synthetic-offline-test-not-a-provider-key")
    monkeypatch.setattr(model_gateway, "budget", lambda: {"remaining_cny": 1})
    repo = Repository(f"sqlite:///{(tmp_path / 'evidence.sqlite3').as_posix()}")
    project = s.create_project({"name": "Public evidence fixture", "description": "synthetic"}, USER, repo)
    sources = [
        s.create_asset(
            {
                "name": mode,
                "project_id": project["id"],
                "source_id": mode,
                "source_version": "1",
                "modality": "text",
                "content": "Synthetic paragraph.",
                "provenance": "Synthetic fixture",
                "classification": mode,
            },
            USER,
            repo,
        )
        for mode in ("public", "local")
    ]
    body = {
        "project_id": project["id"],
        "input_refs": [sources[0]["id"]],
        "focus": "claim_check",
        "idempotency_key": "fixture",
        "public_egress_authorized": True,
        "max_cost_cny": 0.7,
    }
    yield repo, body, *sources
    repo.engine.dispose()


def _parent(repo, body, inputs):
    parent = repo.put(
        "tasks",
        {
            "project_id": body["project_id"],
            "status": "succeeded",
            "request": {
                "task_type": "case_analysis",
                "input_refs": [a["id"] for a in inputs],
                "goal": "never-export-parent-goal",
            },
            "snapshot": {"inputs": inputs},
        },
    )
    repo.put(
        "artifacts",
        {
            "task_id": parent["id"],
            "project_id": body["project_id"],
            "source_refs": [a["id"] for a in inputs],
            "content": "never-export-case-deliverable",
        },
    )
    return parent


def test_builtin_dependency_upgrade_preserves_old_versions_and_rejects_changed_instructions(
    fixture, monkeypatch
):
    from backend.app.platform import public_evidence

    repo, body, _, _ = fixture
    pid = body["project_id"]
    with repo.transaction() as tx:
        wid = public_evidence._workflow(tx, pid)
    aid = pid + "-public-evidence-agent"
    old_agent = deepcopy(repo.get("agent_versions", aid + ":1"))
    old_flow = deepcopy(repo.get("workflow_versions", wid + ":1"))
    freeze = public_evidence.freeze_bindings

    def changed_dependency(agent):
        frozen = deepcopy(freeze(agent))
        frozen["tools"][0]["checksum"] = "explicit-synthetic-upgraded-runtime"
        return frozen

    monkeypatch.setattr(public_evidence, "freeze_bindings", changed_dependency)
    with repo.transaction() as tx:
        assert public_evidence._workflow(tx, pid) == wid
    assert repo.get("agents", aid)["current_version"] == 2
    assert repo.get("workflows", wid)["current_version"] == 2
    assert repo.get("agent_versions", aid + ":1") == old_agent
    assert repo.get("workflow_versions", wid + ":1") == old_flow
    with repo.transaction() as tx:
        public_evidence._workflow(tx, pid)
    assert repo.get("agents", aid)["current_version"] == 2
    definition = repo.get("agents", aid)
    repo.put("agents", {**definition, "instructions": "unapproved replacement instruction"})
    with repo.transaction() as tx, pytest.raises(s.DomainError) as error:
        public_evidence._workflow(tx, pid)
    assert error.value.code == "semantic_profile_changed"


def test_public_handoff_pins_exact_sources_and_is_idempotent(fixture):
    repo, body, public, _ = fixture
    parent = _parent(repo, body, [public])
    request = PublicEvidenceRequest(**body, case_task_id=parent["id"])
    task = submit(request, USER, repo)
    assert submit(request, USER, repo)["id"] == task["id"]
    saved = repo.get("tasks", task["id"])
    assert saved["request"]["mode"] == "cloud" and saved["request"]["use_project_knowledge"] is False
    assert {a["id"] for a in saved["snapshot"]["inputs"]} == {public["id"]}
    assert saved["snapshot"]["agents"][0]["execution_strategy"] == "tool_agent"
    assert "never-export-" not in json.dumps(saved["request"]) + json.dumps(saved["snapshot"])
    assert saved["semantic_evidence"]["case_task_id"] == parent["id"]
    with pytest.raises(s.DomainError, match="幂等"):
        submit(request.model_copy(update={"case_task_id": None}), USER, repo)


def test_public_handoff_denies_local_partial_sources_revocation_and_offline(fixture, monkeypatch):
    repo, body, public, private = fixture
    parent = _parent(repo, body, [public, private])
    with pytest.raises(s.DomainError, match="公开子集"):
        submit(PublicEvidenceRequest(**body, case_task_id=parent["id"]), USER, repo)
    with pytest.raises(s.DomainError, match="外发"):
        submit(PublicEvidenceRequest(**(body | {"input_refs": [private["id"]]})), USER, repo)
    with pytest.raises(s.DomainError, match="项目"):
        submit(PublicEvidenceRequest(**body), {"id": "outsider", "role": "operator"}, repo)
    monkeypatch.setenv("HANGZHI_OFFLINE", "true")
    with pytest.raises(s.DomainError, match="离线"):
        submit(PublicEvidenceRequest(**body), USER, repo)
    assert len(repo.list("tasks")) == 1
    monkeypatch.setenv("HANGZHI_OFFLINE", "false")
    s.revoke_asset(public["id"], USER, repo)
    with pytest.raises(s.DomainError, match="撤回"):
        submit(PublicEvidenceRequest(**body), USER, repo)


def test_scientific_review_uses_only_public_sources_and_preserves_case_outcome(fixture):
    repo, body, public, _ = fixture
    parent = _parent(repo, body, [public])
    req = PublicEvidenceRequest(**(body | {"focus": "scientific_terms", "case_task_id": parent["id"]}))
    task = submit(req, USER, repo)
    frozen = repo.get("tasks", task["id"])
    assert (
        frozen["request"]["input_refs"] == [public["id"]] and not frozen["request"]["use_project_knowledge"]
    )
    assert frozen["semantic_evidence"]["focus"] == "scientific_terms" and task["id"] != parent["id"]
    for term in ("原文术语", "含义", "实测", "模拟", "保留英文"):
        assert term in frozen["request"]["goal"] or (term == "含义" and "释义" in frozen["request"]["goal"])
    assert submit(req, USER, repo)["id"] == task["id"] and repo.get("tasks", parent["id"]) == parent
    with pytest.raises(s.DomainError, match="幂等"):
        submit(req.model_copy(update={"focus": "comparison"}), USER, repo)


def test_public_report_rechecks_source_and_requires_real_checked_output(fixture):
    repo, body, public, _ = fixture
    task = submit(PublicEvidenceRequest(**body), USER, repo)
    with pytest.raises(s.DomainError, match="成功"):
        report(task["id"], USER, repo)
    repo.put("tasks", repo.get("tasks", task["id"]) | {"status": "succeeded"})
    with pytest.raises(s.DomainError, match="未找到"):
        report(task["id"], USER, repo)
    content = {
        "text": "Synthetic test rendering.",
        "citations": [
            {
                "kind": "source_span",
                "source_id": public["id"],
                "source_version": "1",
                "start": 0,
                "end": 9,
                "quote": "Synthetic",
            }
        ],
        "missing_information": [],
    }
    repo.put(
        "artifacts",
        {
            "project_id": body["project_id"],
            "task_id": task["id"],
            "type": "text_analysis",
            "content": content,
            "method": "tool_agent",
            "checksum": s.digest(content),
            "quality_status": "passed",
            "source_refs": [public["id"]],
        },
    )
    assert "字符 [0,9)：Synthetic" in report(task["id"], USER, repo)
    s.revoke_asset(public["id"], USER, repo)
    with pytest.raises(s.DomainError, match="撤回"):
        report(task["id"], USER, repo)


def test_explicit_term_contradictions_block_even_a_passing_model_and_preserve_scope():
    data = "Under dry conditions aerosol optical depth (AOD) was measured. SNR (signal to noise ratio) was also reported."
    asset = {"id": "record", "source_version": "edition-4", "content": data}
    text = "UV、AOD 均未在节选中给出全称。SNR was not defined."
    found = conflicts({"text": text}, [asset])
    assert {i["acronym"] for i in found} == {"AOD", "SNR"}
    assert all(data[i["start"] : i["end"]] == i["quote"] for i in found)
    assert not conflicts({"text": "AOD 是气溶胶光学厚度，节选未给出 UV 全称。SNR 未给出具体数值。"}, [asset])
    assert not conflicts({"text": "XYZ 未定义。"}, [asset])
    output = {
        "type": "text_analysis",
        "source_refs": ["record"],
        "method": "tool_agent",
        "content": {
            "text": text,
            "citations": [
                {
                    "kind": "source_span",
                    "source_id": "record",
                    "source_version": "edition-4",
                    "start": 0,
                    "end": 5,
                    "quote": data[:5],
                }
            ],
        },
    }
    quality = deterministic_quality({"task_type": "text_analysis"}, output, [asset], scientific_terms=True)
    assert not quality["passed"] and any(
        i["code"] == "scientific_source_definitions" and i["hard"] for i in quality["issues"]
    )
    assert not combine_review(quality, {"verdict": "passed"})["passed"]
    assert not any(
        i["code"] == "scientific_source_definitions"
        for i in deterministic_quality({"task_type": "text_analysis"}, output, [asset])["issues"]
    )


def test_historic_scientific_report_marks_sourced_contradiction_without_mutation(fixture):
    repo, body, _public, _private = fixture
    source = s.create_asset(
        {
            "name": "Public optics excerpt",
            "project_id": body["project_id"],
            "source_id": "paper",
            "source_version": "p7",
            "modality": "text",
            "provenance": "Synthetic scientific passage",
            "classification": "public",
            "content": "The aerosol optical depth (AOD) was measured.",
        },
        USER,
        repo,
    )
    task = submit(
        PublicEvidenceRequest(**(body | {"focus": "scientific_terms", "input_refs": [source["id"]]})),
        USER,
        repo,
    )
    historic = repo.put("tasks", repo.get("tasks", task["id"]) | {"status": "succeeded"})
    content = {
        "text": "AOD 未在原文中给出全称。",
        "missing_information": [],
        "citations": [
            {
                "kind": "source_span",
                "source_id": source["id"],
                "source_version": "p7",
                "start": 4,
                "end": 25,
                "quote": source["content"][4:25],
            }
        ],
    }
    artifact = repo.put(
        "artifacts",
        {
            "project_id": body["project_id"],
            "task_id": task["id"],
            "type": "text_analysis",
            "method": "tool_agent",
            "quality_status": "passed",
            "content": content,
            "checksum": s.digest(content),
            "source_refs": [source["id"]],
        },
    )
    downloaded = report(task["id"], USER, repo)
    assert (
        "确定性复核补记" in downloaded
        and "aerosol optical depth (AOD)" in downloaded
        and content["text"] in downloaded
    )
    detail = s.task_detail(task["id"], USER, repo)
    assert (
        detail["scientific_review_note"]["issues"][0]["acronym"] == "AOD" and detail["status"] == "succeeded"
    )
    assert repo.get("tasks", task["id"]) == historic and repo.get("artifacts", artifact["id"]) == artifact
    s.revoke_asset(source["id"], USER, repo)
    assert "scientific_review_note" not in s.task_detail(task["id"], USER, repo)
    with pytest.raises(s.DomainError, match="撤回"):
        report(task["id"], USER, repo)


@pytest.mark.parametrize(
    "extra",
    [
        {"public_egress_authorized": False},
        {"max_cost_cny": 0.81},
        {"goal": "unsafe free-form outbound text"},
        {"input_refs": []},
    ],
)
def test_semantic_request_needs_explicit_approved_payload(fixture, extra):
    with pytest.raises(ValidationError):
        PublicEvidenceRequest(**(fixture[1] | extra))
