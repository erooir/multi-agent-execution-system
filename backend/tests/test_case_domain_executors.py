import ast
import json
from collections import defaultdict

import httpx
import pytest

from backend.app import config
from backend.app import model_gateway as gateway_module
from backend.app.casework.executors.registry import ANALYZERS, binding
from backend.app.casework.team import catalog, install
from backend.app.model_gateway import MODEL_ID
from backend.app.platform import runtime, service
from backend.tests import test_v2_platform as fixtures
from backend.tests.test_casework_documents import samples as document_samples
from backend.tests.test_casework_geo_samples import media_samples
from backend.tests.test_casework_geo_samples import samples as geo_samples
from backend.tests.test_v2_platform import ADMIN, asset, project, run, task_body

db = fixtures.db


def _artifact(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            try:
                value = ast.literal_eval(value)
            except (ValueError, SyntaxError):
                return None
    if isinstance(value, dict):
        candidate = value.get("artifact")
        if isinstance(candidate, dict) and {"tool_call_id", "result_checksum"} <= set(candidate):
            return candidate
        for item in value.values():
            found = _artifact(item)
            if found:
                return found
    if isinstance(value, list):
        for item in value:
            found = _artifact(item)
            if found:
                return found
    return None


def _decision(stage, artifact):
    base = {"artifact": artifact, "assessment": "已核对主工具产物和来源覆盖。", "evidence_ids": []}
    if stage == "prepare":
        return {**base, "source_gaps": []}
    if stage == "analyze":
        return {**base, "products_patch": {}, "gaps": []}
    return {**base, "additional_checks": [], "gaps": []}


def _patch_provider(monkeypatch, rounds, choose=None, decide=None):
    async def provider(request):
        payload = json.loads(request.content)
        rounds.append(payload)
        names = [
            item["function"]["name"] for item in payload.get("tools", []) if item.get("type") == "function"
        ]
        main = next((name for name in names if name.startswith("case_")), None)
        assert main, names
        stage = main.rsplit("_", 1)[-1]
        prior = [
            item for item in rounds if any(tool["function"]["name"] == main for tool in item.get("tools", []))
        ]
        tool_messages = [item for item in payload["messages"] if item.get("role") == "tool"]
        if not tool_messages:
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": f"fixture-{main}-{len(prior)}",
                        "type": "function",
                        "function": {"name": main, "arguments": "{}"},
                    }
                ],
            }
            finish = "tool_calls"
        else:
            selected = choose(stage, main, tool_messages) if choose else None
            if selected:
                name, arguments = selected
                message = {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": f"fixture-{name}-{len(rounds)}",
                            "type": "function",
                            "function": {"name": name, "arguments": json.dumps(arguments)},
                        }
                    ],
                }
                finish = "tool_calls"
            else:
                reference = next(
                    (found for item in reversed(tool_messages) if (found := _artifact(item["content"]))),
                    None,
                )
                assert reference, tool_messages[-1]
                decision = _decision(stage, reference)
                if decide:
                    decision = decide(stage, decision)
                message = {
                    "role": "assistant",
                    "content": json.dumps(decision, ensure_ascii=False),
                }
                finish = "stop"
        return httpx.Response(
            200,
            json={
                "id": f"fixture-response-{len(rounds)}",
                "object": "chat.completion",
                "created": 1,
                "model": MODEL_ID,
                "choices": [{"index": 0, "message": message, "finish_reason": finish}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20},
            },
        )

    original = gateway_module.MeteredTransport
    monkeypatch.setattr(config, "get_api_key", lambda: "explicit-synthetic-fixture")
    monkeypatch.setattr(gateway_module, "get_api_key", lambda: "explicit-synthetic-fixture")
    monkeypatch.setattr(gateway_module, "model_gateway", runtime.model_gateway)
    monkeypatch.setattr(
        gateway_module,
        "MeteredTransport",
        lambda *args, **kwargs: original(*args, **kwargs, inner=httpx.MockTransport(provider)),
    )


def _inputs(db, project, code, tmp_path, monkeypatch):
    monkeypatch.setattr(service, "ASSET_DIR", tmp_path / "assets")
    monkeypatch.setattr(runtime, "ASSET_ROOT", tmp_path / "assets")
    refs = []
    if code.startswith("G"):
        ids = {}
        for label, data in media_samples(code).items():
            item = service.create_asset(
                {
                    "project_id": project["id"],
                    "source_id": label,
                    "name": label,
                    "modality": "image",
                    "media_type": "image/png",
                    "provenance": "public synthetic pixels",
                    "classification": "public",
                    "content": None,
                },
                ADMIN,
                db,
                file_data=data,
            )
            ids[label] = item["id"]
            refs.append(item["id"])
        groups = {"known": geo_samples(code, ids)}
    else:
        groups = defaultdict(list)
        for row in document_samples(code):
            fields = dict(row["fields"])
            if row["text"]:
                fields["text"] = row["text"]
            groups[row["source_id"]].append(fields)
    last = None
    for label, records in groups.items():
        last = asset(
            db,
            project,
            {"records": records},
            source_id=label,
            classification="public",
        )
        refs.append(last["id"])
    return last, refs


def test_static_executor_bindings_cover_every_role_without_new_assets(db):
    p = project(db)
    assert install(p["id"], ADMIN, db)["created"]
    managed = [item for item in db.list("agents", p["id"]) if item.get("case_role")]
    flows = [item for item in db.list("workflows", p["id"]) if item["task_type"] == "case_analysis"]
    assert len(managed) == 32
    assert len(flows) == 30
    assert set(ANALYZERS) == set(catalog())
    for agent in managed:
        stage = agent["parameters"]["case_stage"]
        code = agent["parameters"].get("case_scope")
        assert agent["model_policy"] == "cloud"
        assert agent["execution_strategy"] == "tool_agent"
        assert agent["executor_bindings"] == binding(stage, code)
        assert set(agent["executor_bindings"]) == {"local", "cloud"}
    assert install(p["id"], ADMIN, db, upgrade=True)["updated"] == []
    assert len([item for item in db.list("agents", p["id"]) if item.get("case_role")]) == 32
    assert len([item for item in db.list("workflows", p["id"]) if item["task_type"] == "case_analysis"]) == 30


@pytest.mark.parametrize("code", sorted(catalog()))
async def test_cloud_agno_loop_preserves_three_node_contracts(db, tmp_path, monkeypatch, code):
    p = project(db)
    install(p["id"], ADMIN, db)
    last, refs = _inputs(db, p, code, tmp_path, monkeypatch)
    rounds = []
    _patch_provider(monkeypatch, rounds)
    request = task_body(
        p,
        last,
        task_type="case_analysis",
        case_id=code,
        case_scope="sample",
        input_refs=refs,
        mode="cloud",
        max_cost_cny=2,
        max_revisions=0,
        allow_partial=False,
    )
    final = await run(db, request)
    assert final["status"] == "succeeded", (
        code,
        final.get("reason_code"),
        [
            (item["capability_id"], item["status"], item["error_code"], item["summary"])
            for item in final.get("tool_calls", [])
        ],
        [item["payload"] for item in db.list("events", task_id=final["id"]) if item["payload"].get("detail")],
    )
    nodes = sorted(db.list("nodes", p["id"], task_id=final["id"]), key=lambda item: item["node_id"])
    assert [item["node_id"] for item in nodes] == ["analyze", "prepare", "review"]
    assert [item["output"]["type"] for item in nodes] == [
        "case_candidate",
        "case_records",
        "case_report",
    ]
    assert all(item["output"]["method"] == "domain_tool_agent" for item in nodes)
    assert all(item["output"]["source_refs"] == refs for item in nodes)
    assert all(item["output"]["content"]["tool_findings"] == [] for item in nodes)
    prepared = next(item["output"]["content"] for item in nodes if item["node_id"] == "prepare")
    candidate = next(item["output"]["content"] for item in nodes if item["node_id"] == "analyze")
    report = next(item["output"]["content"] for item in nodes if item["node_id"] == "review")
    assert isinstance(prepared["records"], list) and isinstance(prepared["gaps"], list)
    assert set(candidate) >= {"products", "evidence", "gaps", "tool_findings"}
    assert set(candidate["products"]) == set(catalog()[code]["outputs"])
    assert set(report) >= {
        "case_id",
        "scope",
        "title",
        "products",
        "evidence",
        "gaps",
        "tool_findings",
        "review",
        "reading_aids",
        "reading_notes",
    }
    assert not report["gaps"] and all(item["passed"] for item in report["review"])
    assert len(rounds) == 6
    assert final["model_request_count"] == 6
    assert final["result"]["usage"]["request_count"] == 6
    main_calls = [item for item in final["tool_calls"] if item["capability_id"].startswith("case.executor.")]
    assert len(main_calls) == 3
    for call in main_calls:
        checkpoint = final["tool_checkpoints"][call["id"]]
        assert checkpoint["result"]["type"] in {"case_records", "case_candidate", "case_report"}
        assert call["result_checksum"]
        assert call["summary"]["full_result_in_checkpoint"] is True
        assert call["instance_id"]
    for payload in rounds:
        assert len(json.dumps(payload, ensure_ascii=False).encode()) < 200_000
        if any(item.get("role") == "tool" for item in payload["messages"]):
            assert '"records": [' not in json.dumps(payload["messages"][-1])


async def test_model_selects_supplementary_tool_and_result_reaches_review(db, tmp_path, monkeypatch):
    p = project(db)
    install(p["id"], ADMIN, db)
    last, refs = _inputs(db, p, "B03", tmp_path, monkeypatch)
    rounds = []

    def choose(stage, main, tool_messages):
        if stage == "analyze" and _artifact(tool_messages[-1]["content"]):
            return "audit_keys", {"source_id": refs[0], "key_fields": ["image_id"]}
        return None

    _patch_provider(monkeypatch, rounds, choose)
    final = await run(
        db,
        task_body(
            p,
            last,
            task_type="case_analysis",
            case_id="B03",
            case_scope="sample",
            input_refs=refs,
            mode="cloud",
            max_cost_cny=2,
            max_revisions=0,
            allow_partial=False,
        ),
    )
    assert final["status"] == "succeeded", final.get("reason_code")
    assert len(rounds) == 7
    candidate = db.get("nodes", f"{final['id']}:0:analyze")["output"]["content"]
    assert len(candidate["tool_findings"]) == 1
    finding = candidate["tool_findings"][0]
    assert finding["tool_id"] == "table.audit_keys"
    assert finding["status"] == "completed"
    assert finding["stage"] == "analyze"
    report = db.get("nodes", f"{final['id']}:0:review")["output"]["content"]
    assert report["tool_findings"][-1]["tool_call_id"] == finding["tool_call_id"]
    assert all(item["passed"] for item in report["review"])


async def test_forged_artifact_reference_uses_bounded_recovery(db, tmp_path, monkeypatch):
    p = project(db)
    install(p["id"], ADMIN, db)
    last, refs = _inputs(db, p, "B01", tmp_path, monkeypatch)
    rounds = []
    invalid = [True]

    def decide(stage, decision):
        if stage == "prepare" and invalid:
            invalid.pop()
            decision["artifact"]["result_checksum"] = "0" * 64
        return decision

    _patch_provider(monkeypatch, rounds, decide=decide)
    final = await run(
        db,
        task_body(
            p,
            last,
            task_type="case_analysis",
            case_id="B01",
            case_scope="sample",
            input_refs=refs,
            mode="cloud",
            max_cost_cny=2,
            max_revisions=1,
            allow_partial=False,
        ),
    )
    assert final["status"] == "succeeded", final.get("reason_code")
    assert final["revision"] == 1
    assert len(rounds) == 8
    recovery = next(
        item for item in final["repair_history"] if item.get("entry_kind") == "technical_recovery"
    )
    assert recovery["reason_code"] == "model_output_invalid"
    failed = db.get("nodes", f"{final['id']}:0:prepare")
    assert failed["status"] == "failed"
    assert db.get("nodes", f"{final['id']}:1:prepare")["status"] == "succeeded"
    assert db.get("nodes", f"{final['id']}:0:prepare")["output"] is None


def test_model_review_cannot_override_deterministic_hard_failure():
    from types import SimpleNamespace

    from backend.app.casework.executors.base import ReviewDecision
    from backend.app.casework.executors.review import IndependentReviewExecutor

    output = {
        "content": {
            "evidence": [{"id": "E1"}],
            "gaps": ["原证据位置无效"],
            "review": [
                {"name": "evidence_locations", "passed": False, "message": "原证据位置无效", "hard": True}
            ],
        },
        "metrics": {"gap_count": 1},
    }
    decision = ReviewDecision.model_validate(
        {
            "artifact": {"tool_call_id": "real-tool", "result_checksum": "a" * 64},
            "assessment": "模型建议通过",
            "additional_checks": [
                {
                    "name": "model_self_check",
                    "passed": True,
                    "message": "模型自检通过",
                    "evidence_ids": ["E1"],
                }
            ],
            "gaps": [],
            "evidence_ids": ["E1"],
        }
    )
    result = IndependentReviewExecutor().apply_decision(
        output, decision, None, SimpleNamespace(stage="review")
    )
    assert result["content"]["review"] == [
        {"name": "evidence_locations", "passed": False, "message": "原证据位置无效", "hard": True}
    ]
    assert result["content"]["gaps"] == ["原证据位置无效"]


def test_semantic_patch_preserves_nested_product_shape():
    from backend.app.casework.executors.base import same_product_shape

    original = {"items": [{"text": "原文", "evidence_ids": ["E1"], "score": 1}], "optional": None}
    assert same_product_shape(
        original,
        {"items": [{"text": "重组", "evidence_ids": ["E1"], "score": 2}], "optional": None},
    )
    assert not same_product_shape(
        original,
        {"items": [{"text": "重组", "evidence_ids": "E1", "score": 2}], "optional": None},
    )
    assert not same_product_shape(
        original,
        {"items": [{"text": "重组", "evidence_ids": ["E1"], "score": 2}], "optional": "新增类型"},
    )


def test_cloud_case_still_rejects_unapproved_source(db, tmp_path, monkeypatch):
    p = project(db)
    install(p["id"], ADMIN, db)
    last, refs = _inputs(db, p, "B01", tmp_path, monkeypatch)
    private = asset(db, p, {"records": []}, source_id="unapproved", classification="local")
    with pytest.raises(service.DomainError) as error:
        service.create_task(
            task_body(
                p,
                last,
                task_type="case_analysis",
                case_id="B01",
                case_scope="sample",
                input_refs=[*refs, private["id"]],
                mode="cloud",
                max_cost_cny=1,
            ),
            ADMIN,
            db,
        )
    assert error.value.code == "data_egress_blocked"
    assert db.list("tasks", p["id"]) == []


def test_managed_executor_implementation_upgrade_appends_version(db):
    from backend.app.casework.team import role_id

    p = project(db)
    install(p["id"], ADMIN, db)
    aid = role_id(p["id"], "analyze", "B01")
    agent = db.get("agents", aid)
    agent["executor_bindings"]["cloud"]["implementation"] = "0" * 64
    db.put("agents", agent)
    result = install(p["id"], ADMIN, db, upgrade=True, case_id="B01")
    assert aid in result["updated"]
    upgraded = db.get("agents", aid)
    assert upgraded["current_version"] == 2
    assert upgraded["executor_bindings"] == binding("analyze", "B01")
    assert db.get("agent_versions", f"{aid}:1")["executor_bindings"] == binding("analyze", "B01")
    upgraded["executor_bindings"]["cloud"]["id"] = "custom.executor"
    db.put("agents", upgraded)
    with pytest.raises(service.DomainError) as error:
        install(p["id"], ADMIN, db, upgrade=True, case_id="B01")
    assert error.value.code == "asset_conflict"


def test_model_suggested_gaps_do_not_change_authoritative_prepare_gaps():
    from types import SimpleNamespace

    from backend.app.casework.executors.base import PrepareDecision
    from backend.app.casework.executors.prepare import SourcePreparationExecutor

    decision = PrepareDecision.model_validate(
        {
            "artifact": {"tool_call_id": "real-tool", "result_checksum": "a" * 64},
            "assessment": "模型建议",
            "source_gaps": ["模型自行增加的标准"],
            "evidence_ids": ["frozen-source"],
        }
    )
    output = {"content": {"records": [], "gaps": ["工具解析缺口"]}, "metrics": {}}
    context = SimpleNamespace(stage="prepare", execution=SimpleNamespace(inputs=[{"id": "frozen-source"}]))
    result = SourcePreparationExecutor().apply_decision(output, decision, None, context)
    assert result["content"]["gaps"] == ["工具解析缺口"]
    decision.evidence_ids.append("forged-record")
    with pytest.raises(ValueError, match="prepare_evidence_not_allowed"):
        SourcePreparationExecutor().apply_decision(output, decision, None, context)


def test_builtin_tool_runtime_upgrade_is_explicit_and_versioned(db):
    from backend.app.casework.team import role_id
    from backend.app.platform.tool_registry import freeze_bindings

    p = project(db)
    install(p["id"], ADMIN, db)
    aid = role_id(p["id"], "analyze", "B01")
    agent = db.get("agents", aid)
    agent["runtime_bindings"]["tools"][0]["checksum"] = "0" * 64
    db.put("agents", agent)
    result = install(p["id"], ADMIN, db, upgrade=True, case_id="B01")
    assert aid in result["updated"]
    upgraded = db.get("agents", aid)
    assert upgraded["current_version"] == 2
    assert upgraded["runtime_bindings"] == freeze_bindings(upgraded)
    assert upgraded["runtime_bindings"]["tools"][0]["checksum"] != "0" * 64
    upgraded["runtime_bindings"]["tools"][0]["id"] = "custom.tool"
    db.put("agents", upgraded)
    with pytest.raises(service.DomainError) as error:
        install(p["id"], ADMIN, db, upgrade=True, case_id="B01")
    assert error.value.code == "tool_version_changed"
