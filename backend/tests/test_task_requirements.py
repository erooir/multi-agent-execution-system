"""Data requirements are a real paid-boundary phase, released before customer input."""

import json

import pytest

from backend.app.platform import runtime, task_cases
from backend.app.platform.task_case_contracts import InlineSubmissionInput
from backend.tests import test_v2_platform as fixtures
from backend.tests.test_task_cases import REQUESTER, create, setup

db = fixtures.db


def requirement_response(*, invalid=False):
    return {
        "summary": "计算经营净额需要收入与退款明细",
        "needs_data": True,
        "requirements": [
            {
                "id": "sales",
                "title": "经营流水",
                "required": True,
                "description": "提供日期、收入和退款，以便核算每日净额",
                "formats": "JSON" if invalid else ["JSON", "CSV"],
                "fields": ["date", "revenue", "refund"],
            }
        ],
        "assumptions": ["收入和退款使用同一币种"],
    }


@pytest.mark.parametrize("corrected", [True, False])
async def test_requirement_contract_correction_is_bounded_audited_and_shares_budget(
    db, monkeypatch, corrected
):
    p = setup(db, monkeypatch)
    calls = []
    first_raw = json.dumps(requirement_response(invalid=True), ensure_ascii=False)

    async def complete(prompt, *, purpose, run_id, **kwargs):
        task = runtime.context().task
        assert task["id"] == run_id
        assert task["task_case_phase"] == "requirements"
        if calls:
            assert purpose == "task_case_requirements_correction"
            payload = json.loads(prompt)
            assert payload["rejected_response"] == first_raw
            assert any(
                error["loc"] == ["requirements", 0, "formats"] and error["type"] == "list_type"
                for error in payload["validation_errors"]
            )
        else:
            assert purpose == "task_case_requirements"
        charge = runtime.model_gateway.ledger.reserve(
            0.03, purpose, run_id, task_limit=task["request"]["max_cost_cny"]
        )
        runtime.model_gateway.ledger.settle(charge, {"prompt_tokens": 1000, "completion_tokens": 100})
        calls.append({"purpose": purpose, "run_id": run_id, "request_id": charge})
        return {
            "text": json.dumps(
                requirement_response(invalid=not (len(calls) == 2 and corrected)), ensure_ascii=False
            ),
            "model": "mock-requirements-model",
            "request_id": charge,
        }

    monkeypatch.setattr(runtime.model_gateway, "complete", complete)
    case = create(db, p, goal="计算门店每日净营业额", public_egress_authorized=True)
    finished = await runtime.process_claim(db, db.claim("requirement-correction"), "requirement-correction")
    detail = task_cases.get(case["id"], REQUESTER, db)
    assert [call["purpose"] for call in calls] == [
        "task_case_requirements",
        "task_case_requirements_correction",
    ]
    assert {call["run_id"] for call in calls} == {finished["id"]}
    assert len(detail["runs"]) == 1 and finished["lease_owner"] is None
    assert not db.claim("requirement-correction")
    assert not db.list("agent_candidates", p["id"])
    assert detail["usage"]["request_count"] == 2 and detail["usage"]["cost_cny"] > 0
    assert detail["usage"]["reserved_cny"] == 0
    assert detail["usage"]["total_cny"] == pytest.approx(finished["result"]["usage"]["total_cny"])
    rejected = [plan for plan in detail["plans"] if plan["status"] == "rejected"]
    assert len(rejected) == (1 if corrected else 2)
    for attempt, record in enumerate(rejected, 1):
        assert record["phase"] == "requirements" and record["attempt"] == attempt
        assert record["model"] == "mock-requirements-model"
        assert record["request_id"] == calls[attempt - 1]["request_id"]
        assert record["raw_response"] == first_raw and record["validation_errors"]
        with pytest.raises(ValueError, match="immutable"):
            db.put("plan_versions", {**record, "raw_response": "overwritten"})
    for event in db.list("events", p["id"], task_id=finished["id"]):
        assert "raw_response" not in event.get("payload", {})
        assert first_raw not in json.dumps(event, ensure_ascii=False)
    if corrected:
        assert finished["status"] == "succeeded" and detail["status"] == "awaiting_data"
        final_plan = next(plan for plan in detail["plans"] if plan["status"] == "frozen")
        assert final_plan["rejected_attempts"] == [rejected[0]["id"]]
        assert final_plan["request_id"] == calls[1]["request_id"]
        node = db.list("nodes", p["id"], task_id=finished["id"])[0]
        assert node["output"]["rejected_attempts"] == final_plan["rejected_attempts"]
        assert detail["requirements"][0]["formats"] == ["JSON", "CSV"]
    else:
        assert finished["status"] == detail["status"] == "failed"
        assert finished["reason_code"] == "model_output_invalid"
        assert not detail.get("requirements_analysis") and not finished["result"]["artifacts"]
        assert all(run["task_case_phase"] == "requirements" for run in db.list("tasks", p["id"]))


async def test_model_data_contract_and_batch_resume_preserve_all_budget_charges(db, monkeypatch):
    p = setup(db, monkeypatch)
    called = []

    async def complete(prompt, *, purpose, **kwargs):
        called.append(purpose)
        assert purpose == "task_case_requirements"
        run = runtime.context().task
        charge = runtime.model_gateway.ledger.reserve(
            0.03, purpose, run["id"], task_limit=run["request"]["max_cost_cny"]
        )
        runtime.model_gateway.ledger.settle(charge, {"prompt_tokens": 1000, "completion_tokens": 100})
        return {
            "text": json.dumps(
                {
                    "summary": "计算营业净额需营业额与退款流水，并统一日期口径",
                    "needs_data": True,
                    "requirements": [
                        {
                            "id": "sales",
                            "title": "营业流水",
                            "required": True,
                            "description": "逐日收入和退款，以便计算净额",
                            "formats": ["JSON", "CSV", "XLSX"],
                            "fields": ["date", "revenue", "refund"],
                            "time_scope": "2023年12月",
                            "spatial_scope": "指定门店",
                            "unit_scope": "人民币元",
                        }
                    ],
                    "assumptions": ["净额为收入减退款"],
                },
                ensure_ascii=False,
            ),
            "model": "mock-model",
            "request_id": charge,
        }

    monkeypatch.setattr(runtime.model_gateway, "complete", complete)
    first = create(db, p, goal="请计算2023年12月门店每日净营业额", public_egress_authorized=True)
    assert first["status"] == "analyzing_requirements"
    req_run = await runtime.process_claim(db, db.claim("requirements-test"), "requirements-test")
    assert req_run["status"] == "succeeded" and req_run["lease_owner"] is None
    waiting = task_cases.get(first["id"], REQUESTER, db)
    assert waiting["status"] == "awaiting_data" and waiting["task_id"] is None
    assert waiting["requirements"][0]["time_scope"] == "2023年12月"
    assert waiting["requirements"][0]["fields"] == ["date", "revenue", "refund"]
    assert waiting["requirements_analysis"]["task_id"] == req_run["id"]
    assert not db.claim("requirements-test") and not db.list("agent_candidates", p["id"])
    assert called == ["task_case_requirements"]
    spent = waiting["usage"]["cost_cny"]
    assert spent > 0 and waiting["usage"]["request_count"] == 1
    unresolved = runtime.model_gateway.ledger.reserve(
        0.02, "explicit-test-uncertain", req_run["id"], task_limit=2
    )
    runtime.model_gateway.ledger.uncertain(unresolved)
    waiting = task_cases.get(first["id"], REQUESTER, db)
    assert waiting["usage"]["uncertain_count"] == 1 and waiting["trace"]["models"][0]["reserved_cny"] == 0.02
    supplied = task_cases.ingest_inline(
        first["id"],
        InlineSubmissionInput(
            name="sales.json",
            content=[{"date": "2023-12-01", "revenue": 100, "refund": 5}],
            source_id="public-synthetic",
            provenance="unit fixture",
            classification="public",
            requirement_id="sales",
            idempotency_key="data",
        ),
        REQUESTER,
        db,
    )
    assert supplied["status"] == "ready" and len(supplied["submissions"]) == 1
    started = task_cases.start(first["id"], REQUESTER, db)
    current = db.get("tasks", started["task_id"])
    assert current["request"]["max_cost_cny"] == pytest.approx(2 - spent - 0.02)
    assert len(started["runs"]) == 2 and started["usage"]["request_count"] == 2
    # Customer retries an accepted batch after execution has been queued: no new data/run.
    again = task_cases.ingest_inline(
        first["id"],
        InlineSubmissionInput(
            name="sales.json",
            content=[{"date": "2023-12-01", "revenue": 100, "refund": 5}],
            source_id="public-synthetic",
            provenance="unit fixture",
            classification="public",
            requirement_id="sales",
            idempotency_key="data",
        ),
        REQUESTER,
        db,
    )
    assert len(again["submissions"]) == 1 and len(again["runs"]) == 2
