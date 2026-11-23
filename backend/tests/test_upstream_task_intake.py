"""Integration checks for connector-scoped, unattended intake with synthetic inputs."""

import json

import httpx
import pytest

from backend.app.casework.intake_plan import plan_question
from backend.app.platform import knowledge, runtime, service, task_cases, task_intake
from backend.app.platform.contracts import FILE_INDEX_MEDIA_TYPE, TaskInput
from backend.tests import test_v2_platform as fixtures
from backend.tests.test_task_cases import REQUESTER, create, setup
from backend.tests.test_task_requirements import requirement_response
from backend.tests.test_v2_platform import ADMIN, asset, project, task_body

db = fixtures.db


async def awaiting_sales(db, p, monkeypatch, **kwargs):
    async def requirements(prompt, **kw):
        assert kw["purpose"] == "task_case_requirements"
        return {"text": json.dumps(requirement_response()), "model": "fixture", "request_id": "fixture"}

    monkeypatch.setattr(runtime.model_gateway, "complete", requirements)
    row = create(db, p, goal="计算营业净额", public_egress_authorized=True, **kwargs)
    finished = await runtime.process_claim(db, db.claim("requirements"), "requirements")
    assert finished["status"] == "succeeded"
    return row


async def test_auto_catalog_binding_honors_version_scope_and_egress(db, monkeypatch):
    p = setup(db, monkeypatch)
    source = asset(db, p, name="门店经营流水", source_id="s1")
    asset(db, p, name="经营流水原件索引", source_id="s1", media_type=FILE_INDEX_MEDIA_TYPE)
    asset(db, p, name="不可外发", source_id="s1", classification="local")
    asset(db, project(db), name="其他项目", source_id="s1")
    private = asset(db, p, name="其他任务", source_id="s1")
    db.put("data_assets", {**private, "task_case_id": "other"})
    row = await awaiting_sales(db, p, monkeypatch, source_ids=["s1"])
    assert await task_intake.retrieve_one(db)
    current = task_cases.get(row["id"], REQUESTER, db)
    assert current["input_refs"] == [source["id"]]
    run = db.get("tasks", current["task_id"])
    assert run["snapshot"]["inputs"][0]["checksum"] == source["checksum"]
    assert current["intake"]["status"] == "ready" and "claim" not in current["intake"]
    assert not await task_intake.retrieve_one(db)


async def test_archival_metadata_is_searchable_but_cannot_supply_task_evidence(db, monkeypatch):
    p = setup(db, monkeypatch)
    index = asset(
        db,
        p,
        content={"filename": "sample-archive.zip"},
        source_id="sample-archive",
        media_type=FILE_INDEX_MEDIA_TYPE,
    )
    hits = await knowledge.search({"project_id": p["id"], "query": "sample-archive.zip"}, ADMIN)
    assert [hit["id"] for hit in hits] == [index["id"]]
    assert service.safe_asset(index)["is_file_index"] is True
    body = task_body(p, index, goal="Summarize sample-archive")
    selection = TaskInput.model_validate({k: v for k, v in body.items() if k != "input_refs"})
    assert task_intake.management_inputs(selection, ADMIN, db)["input_refs"] == []
    assert plan_question(body["goal"], p["id"], [index])["input_refs"] == []
    for preflight in (True, False):
        with pytest.raises(service.DomainError) as denied:
            service.create_task(body, ADMIN, db, preflight=preflight)
        assert denied.value.code == "unsupported_input"
    row = create(db, p, mode="local", goal="提取 sample-archive 数据")
    with pytest.raises(service.DomainError) as denied:
        task_cases.submit(row["id"], {"input_refs": [index["id"]], "idempotency_key": "ref"}, REQUESTER, db)
    assert denied.value.code == "unsupported_input"
    assert task_cases.get(row["id"], REQUESTER, db)["input_refs"] == []


@pytest.mark.parametrize("variant", ["valid", "mismatched_project", "private", "disabled"])
async def test_configured_http_source_contract_and_missing_inputs(db, monkeypatch, variant):
    p = setup(db, monkeypatch)
    row = await awaiting_sales(db, p, monkeypatch)
    monkeypatch.setenv("HANGZHI_KNOWLEDGE_HOSTS", "127.0.0.1")
    connector = knowledge.create_connector(
        {
            "project_id": p["id"],
            "name": "Synthetic catalog",
            "kind": "http_knowledge",
            "base_url": "http://127.0.0.1:9999/knowledge",
            "automatic_intake": variant != "disabled",
            "allow_public_results": True,
        },
        ADMIN,
    )
    requests = []

    def upstream(request):
        requests.append(json.loads(request.content))
        assert str(request.url) == "http://127.0.0.1:9999/knowledge/search"
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": "record1",
                        "text": '[{"revenue":10,"refund":1}]',
                        "media_type": "application/json",
                        "source_id": "sales",
                        "source_version": "2026.1",
                        "provenance": "synthetic only",
                        "project_id": "other" if variant == "mismatched_project" else p["id"],
                        "classification": "local" if variant == "private" else "public",
                    }
                ]
            },
        )

    real = httpx.AsyncClient
    monkeypatch.setattr(
        knowledge.httpx, "AsyncClient", lambda **kw: real(**kw, transport=httpx.MockTransport(upstream))
    )
    assert await task_intake.retrieve_one(db)
    detail = task_cases.get(row["id"], REQUESTER, db)
    if variant == "valid":
        assert requests[0]["project_id"] == p["id"]
        assert detail["task_id"] and detail["intake"]["connector_ids"] == [connector["id"]]
        fetched = db.get("data_assets", detail["input_refs"][0])
        assert fetched["task_case_id"] == row["id"] and fetched["source_version"] == "2026.1"
        assert fetched["content"][0]["refund"] == 1
    else:
        assert bool(requests) == (variant != "disabled")
        assert detail["status"] == "awaiting_data" and detail["input_refs"] == []
        assert not db.claim("no_execution")


async def test_revoked_membership_prevents_query_and_start(db, monkeypatch):
    p = setup(db, monkeypatch)
    row = await awaiting_sales(db, p, monkeypatch)
    with db.transaction() as tx:
        project_record = tx.get("projects", p["id"], for_update=True)
        tx.put("projects", {**project_record, "members": []})
    assert await task_intake.retrieve_one(db)
    assert db.get("task_cases", row["id"])["reason_code"] == "intake_access_revoked"
    assert not db.claim("no_dispatch")


async def test_real_loopback_search_to_frozen_input(db, monkeypatch):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    p = setup(db, monkeypatch)
    row = await awaiting_sales(db, p, monkeypatch)
    observed = []

    class Source(BaseHTTPRequestHandler):
        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            observed.append((self.path, request["project_id"]))
            payload = json.dumps(
                {
                    "items": [
                        {
                            "id": "sale-row",
                            "project_id": p["id"],
                            "source_id": "november-sales",
                            "source_version": "v1",
                            "provenance": "synthetic-loopback",
                            "text": "date,revenue,refund\n11-01,100,2",
                            "media_type": "text/csv",
                            "classification": "public",
                        }
                    ]
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Source) as server:
        listener = threading.Thread(target=server.serve_forever, daemon=True)
        listener.start()
        try:
            monkeypatch.setenv("HANGZHI_KNOWLEDGE_HOSTS", "127.0.0.1")
            knowledge.create_connector(
                {
                    "project_id": p["id"],
                    "name": "验收接口",
                    "kind": "http_knowledge",
                    "base_url": f"http://127.0.0.1:{server.server_port}",
                    "automatic_intake": True,
                    "allow_public_results": True,
                },
                ADMIN,
            )
            await task_intake.retrieve_one(db)
        finally:
            server.shutdown()
            listener.join(timeout=3)
    assert observed == [("/search", p["id"])]
    detail = task_cases.get(row["id"], REQUESTER, db)
    snapshot = db.get("tasks", detail["task_id"])["snapshot"]["inputs"][0]
    assert snapshot["source_id"] == "november-sales" and snapshot["source_version"] == "v1"
    assert snapshot["media_type"] == "text/csv" and snapshot["checksum"]


def test_scoped_push_preserves_binary_source_identity_and_excludes_private_input(db, monkeypatch):
    from fastapi.testclient import TestClient

    from backend.app import auth
    from backend.tests.test_v2_api_review import application

    p = setup(db, monkeypatch)
    row = create(db, p, source_ids=["meter-alpha"])
    app = application()
    app.dependency_overrides[auth.get_current_user] = lambda: REQUESTER
    with TestClient(app) as client:
        url = "/api/v2/task-cases/" + row["id"]
        token = client.post(url + "/ingest-tokens", json={}).json()["token"]
        headers = {"Authorization": "Bearer " + token}
        data = {
            "source_id": "meter-beta",
            "provenance": "test agent",
            "source_version": "r1",
            "classification": "public",
            "idempotency_key": "rows",
        }
        files = {"file": ("samples.csv", b"month,value\n01,1\n", "text/csv")}
        assert client.post(url + "/ingest/upload", files=files, data=data, headers=headers).status_code == 403
        assert (
            client.post(
                url + "/ingest/upload",
                files=files,
                data={**data, "source_id": "meter-alpha"},
                headers=headers,
            ).status_code
            == 201
        )
        source = db.get("data_assets", db.get("task_cases", row["id"])["input_refs"][0])
        assert (source["source_id"], source["source_version"], source["provenance"]) == (
            "meter-alpha",
            "r1",
            "test agent",
        )
        outcome = client.get(url + "/outcome").json()
        assert set(outcome) == {"id", "status", "missing", "result", "content_withheld"}


async def test_all_named_sources_required_catalog_latest_then_pinned(db, monkeypatch):
    from backend.app.platform import service

    p = setup(db, monkeypatch)
    asset(db, p, source_id="sales", source_version="legacy")
    newest = asset(db, p, source_id="sales", source_version="2")
    row = await awaiting_sales(db, p, monkeypatch, source_ids=["sales", "refunds"])
    await task_intake.retrieve_one(db)
    mid = task_cases.get(row["id"], REQUESTER, db)
    assert mid["status"] == "awaiting_data" and mid["input_refs"] == [newest["id"]]
    assert any("refunds" in gap for gap in mid["missing"])
    with pytest.raises(service.DomainError) as denied:
        task_cases.start(row["id"], REQUESTER, db)
    assert denied.value.code == "insufficient_data"
    asset(db, p, source_id="sales", source_version="late")
    refunds = asset(db, p, source_id="refunds", source_version="r2")
    claim = db.get("task_cases", row["id"])
    db.put("task_cases", {**claim, "intake": {**claim["intake"], "next_at": None}})
    await task_intake.retrieve_one(db)
    final = task_cases.get(row["id"], REQUESTER, db)
    assert set(final["input_refs"]) == {newest["id"], refunds["id"]}
    assert db.get("tasks", final["task_id"])["status"] == "accepted"


async def test_connector_rotation_and_preferred_version_per_source(db, monkeypatch):
    p = setup(db, monkeypatch)
    row = await awaiting_sales(db, p, monkeypatch, source_ids=["sales"])
    seen = []
    for i in range(4):
        db.put(
            "connectors",
            {
                "id": f"c{i}",
                "project_id": p["id"],
                "name": f"connector {i}",
                "kind": "http_knowledge",
                "automatic_intake": True,
                "allow_public_results": True,
                "base_url": "http://127.0.0.1",
            },
        )

    async def search(connector, body, repo, **kw):
        seen.append(connector["id"])
        if connector["id"] != "c3":
            return []
        item = {
            "source_id": "sales",
            "source_version": "preferred",
            "provenance": "unit",
            "classification": "public",
            "id": "1",
            "text": "[1]",
            "media_type": "application/json",
        }
        return [item, {**item, "id": "2", "source_version": "older", "text": "[0]"}]

    monkeypatch.setattr(knowledge, "connector_results", search)
    await task_intake.retrieve_one(db)
    assert seen == ["c0", "c1", "c2"]
    case = db.get("task_cases", row["id"])
    assert case["status"] == "awaiting_data"
    db.put("task_cases", {**case, "intake": {**case["intake"], "next_at": None}})
    await task_intake.retrieve_one(db)
    case = db.get("task_cases", row["id"])
    assert seen[3] == "c3" and len(case["input_refs"]) == 1
    assert db.get("data_assets", case["input_refs"][0])["source_version"] == "preferred"


def test_changed_key_cannot_duplicate_or_mutate_received_version(db, monkeypatch):
    from backend.app.platform import service
    from backend.app.platform.task_case_contracts import InlineSubmissionInput

    p = setup(db, monkeypatch)
    row = create(db, p, source_ids=["sales", "refunds"])
    body = InlineSubmissionInput(
        name="sales",
        source_id="sales",
        source_version="v1",
        content=[1],
        classification="public",
        provenance="synthetic",
        idempotency_key="first",
    )
    first = task_cases.ingest_inline(row["id"], body, REQUESTER, db)
    replay = task_cases.ingest_inline(row["id"], body, REQUESTER, db)
    assert first["input_refs"] == replay["input_refs"]
    for change, error in (({}, "duplicate_source_version"), ({"content": [2]}, "source_version_conflict")):
        with pytest.raises(service.DomainError) as duplicate:
            task_cases.ingest_inline(
                row["id"], body.model_copy(update={**change, "idempotency_key": "second"}), REQUESTER, db
            )
        assert duplicate.value.code == error
    updated = task_cases.ingest_inline(
        row["id"],
        body.model_copy(update={"content": [2], "source_version": "v2", "idempotency_key": "third"}),
        REQUESTER,
        db,
    )
    assert len(updated["input_refs"]) == 1 and updated["input_refs"] != first["input_refs"]
    assert len(updated["submissions"]) == 2 and updated["status"] == "awaiting_data"
    assert len(db.list("data_assets", p["id"])) == 2


async def test_conflicting_catalog_version_cannot_start(db, monkeypatch):
    p = setup(db, monkeypatch)
    asset(db, p, source_id="sales", source_version="v1", content=[1])
    asset(db, p, source_id="sales", source_version="v1", content=[2])
    row = await awaiting_sales(db, p, monkeypatch, source_ids=["sales"])
    assert await task_intake.retrieve_one(db)
    detail = task_cases.get(row["id"], REQUESTER, db)
    assert detail["status"] == "awaiting_data" and detail["input_refs"] == []
    assert "source_version_conflict" in detail["intake"]["errors"]


async def test_explicit_sources_override_no_data_model_plan(db, monkeypatch):
    p = setup(db, monkeypatch)

    async def requirements(prompt, **kw):
        return {
            "text": json.dumps(
                {"summary": "无需额外资料", "needs_data": False, "requirements": [], "assumptions": []}
            ),
            "model": "synthetic",
            "request_id": "no-data-plan",
        }

    monkeypatch.setattr(runtime.model_gateway, "complete", requirements)
    row = create(
        db, p, goal="归纳数据质量的原则", public_egress_authorized=True, source_ids=["edition-a", "edition-b"]
    )
    completed = await runtime.process_claim(db, db.claim("req"), "req")
    assert completed["status"] == "succeeded"
    case = task_cases.get(row["id"], REQUESTER, db)
    assert case["status"] == "awaiting_data" and not case["task_id"]
    assert any("edition-b" in gap for gap in case["missing"])
    assert not db.claim("execution")


@pytest.mark.parametrize("unavailable", ["revoked", "expired", "deleted"])
def test_withheld_case_history_detail_outcome_without_execution(db, monkeypatch, unavailable):
    from fastapi.testclient import TestClient

    from backend.app import auth
    from backend.app.platform.task_case_contracts import InlineSubmissionInput
    from backend.tests.test_v2_api_review import application

    p = setup(db, monkeypatch)
    case = create(db, p)
    marker = "test_source_field_only"
    received = task_cases.ingest_inline(
        case["id"],
        InlineSubmissionInput(
            name=marker,
            content=[{marker: 1}],
            source_id="rows",
            provenance=marker,
            idempotency_key="1",
            classification="public",
        ),
        REQUESTER,
        db,
    )
    asset_id = received["input_refs"][0]
    assert not received["task_id"] and marker in json.dumps(received["profiles"])
    row = db.get("task_cases", case["id"])
    db.put("task_cases", {**row, "missing": [marker], "requirements_analysis": {"summary": marker}})
    source = db.get("data_assets", asset_id)
    if unavailable == "deleted":
        db.delete("data_assets", asset_id)
    else:
        db.put(
            "data_assets",
            {
                **source,
                **(
                    {"status": "revoked"}
                    if unavailable == "revoked"
                    else {"expires_at": "2000-01-01T00:00:00+00:00"}
                ),
            },
        )
    app = application()
    app.dependency_overrides[auth.get_current_user] = lambda: REQUESTER
    with TestClient(app) as client:
        base = "/api/v2/task-cases"
        detail, summary, outcome = (
            client.get(base + "/" + case["id"]).json(),
            client.get(base, params={"project_id": p["id"]}).json()[0],
            client.get(base + "/" + case["id"] + "/outcome").json(),
        )
        assert "task_case_total" == detail["usage"]["scope"]
        for response in (detail, summary, outcome):
            assert response["content_withheld"] and response["id"] == case["id"] and response["status"]
            assert response["missing"] and marker not in json.dumps(response)
        assert (
            detail["profiles"] == detail["available_assets"] == detail["plans"] == detail["submissions"] == []
        )
        assert not summary["requirements"] and not summary["requirements_analysis"]
        assert not detail["result"] and not outcome["result"]


async def test_unavailable_historical_snapshot_blocks_newer_run_trace(db, monkeypatch):
    from fastapi.testclient import TestClient

    from backend.app import auth
    from backend.tests.test_v2_api_review import application

    p = setup(db, monkeypatch)
    source = asset(db, p, source_id="sales", content=[{"revenue": 20, "refund": 1}])
    case = await awaiting_sales(db, p, monkeypatch, source_ids=["sales"])
    await task_intake.retrieve_one(db)
    current = db.get("task_cases", case["id"])
    history_id, run_id = current["task_ids"]
    assert not db.get("tasks", history_id)["snapshot"]["inputs"]
    assert db.get("tasks", run_id)["snapshot"]["inputs"][0]["id"] == source["id"]
    # A past submitted version may exist only in a frozen run after a later restart.
    db.put("task_cases", {**current, "task_id": history_id, "input_refs": [], "missing": ["private_marker"]})
    for item in db.list("data_submissions", p["id"], task_case_id=case["id"]):
        db.delete("data_submissions", item["id"])
    db.put("data_assets", {**source, "status": "revoked"})
    app = application()
    app.dependency_overrides[auth.get_current_user] = lambda: REQUESTER
    with TestClient(app) as client:
        base = "/api/v2/task-cases/" + case["id"]
        for url in (base, base + "/outcome"):
            response = client.get(url).json()
            assert response["content_withheld"] and "private_marker" not in json.dumps(response)
        assert client.get(base + "/runs/" + history_id).status_code == 409
        assert client.get(base + "/result/download").status_code == 409
