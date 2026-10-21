"""Scheduling and operational measurements use persisted, authorized evidence only."""

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from backend.app import auth
from backend.app.platform import contracts, service
from backend.app.platform.db import QUEUE_AGING_SECONDS, LeaseLost
from backend.tests import test_v2_platform as fixtures
from backend.tests import test_v2_postgres as postgres_fixtures
from backend.tests.test_v2_api_review import application
from backend.tests.test_v2_platform import ADMIN, OTHER, asset, project, task_body

db = fixtures.db
postgres = postgres_fixtures.postgres


def queued(repository, name, *, service_class="batch", age=0, **changes):
    created = datetime.now(UTC) - timedelta(seconds=age)
    return repository.put(
        "tasks",
        {
            "id": name,
            "project_id": "queue-fixture",
            "status": "accepted",
            "idempotency_key": name,
            "created_at": created.isoformat(),
            "request": {"mode": "local", "service_class": service_class},
            "snapshot": {
                "scheduling": {
                    "service_class": service_class,
                    "policy_version": "interactive-aging-v1",
                    "aging_seconds": QUEUE_AGING_SECONDS,
                    "aging_at": (created + timedelta(seconds=QUEUE_AGING_SECONDS)).isoformat(),
                }
            },
            **changes,
        },
    )


def assert_ordering(repository):
    queued(repository, "batch", age=15)
    queued(repository, "interactive", service_class="interactive", age=10)
    queued(repository, "aged-batch", age=QUEUE_AGING_SECONDS + 20)
    queued(repository, "recovery", lease_owner="old", lease_token=1, lease_until=time.time() - 5)
    queued(repository, "cancel", cancel_requested=True)
    queued(repository, "running", lease_owner="live", lease_token=1, lease_until=time.time() + 1000)
    claimed = [repository.claim("next-worker")["id"] for _ in range(5)]
    assert claimed == ["cancel", "recovery", "aged-batch", "interactive", "batch"]
    assert repository.claim("empty") is None
    with pytest.raises(LeaseLost):
        repository.fenced("recovery", "old", 1)
    assert repository.get("tasks", "recovery")["lease_token"] == 2


def test_sqlite_queue_class_age_cleanup_and_lease_fencing(db):
    assert_ordering(db)


def test_postgres_queue_class_age_cleanup_and_lease_fencing(postgres):
    assert_ordering(postgres)


def test_postgres_scoped_metric_projection(postgres):
    p = project(postgres)
    current = datetime.now(UTC)
    metric_task(
        postgres,
        p,
        "finished",
        current,
        seconds=12,
        snapshot={"inputs": [{"content": "PRIVATE_SOURCE_MUST_NOT_ENTER_METRICS"}]},
    )
    queued(postgres, "waiting", project_id=p["id"], service_class="interactive", age=10)
    metrics = service.operational_metrics(ADMIN, postgres, project_id=p["id"], current_time=current)
    assert metrics["latency"]["p95_seconds"] == 12
    assert metrics["queue"]["by_service_class"] == {"batch": 0, "interactive": 1}
    assert "PRIVATE_SOURCE" not in str(postgres.operational_tasks([p["id"]]))


def test_legacy_tasks_age_and_fifo_without_migration(db):
    queued(db, "legacy-old", age=120, snapshot={})
    queued(db, "new-interactive", service_class="interactive", age=2)
    queued(db, "legacy-young", age=5, snapshot={})
    assert [db.claim("worker")["id"] for _ in range(3)] == ["legacy-old", "new-interactive", "legacy-young"]


def test_persisted_policy_overrides_mutated_request_and_preserves_first_claim(db):
    queued(db, "batch", age=10)
    interactive = queued(db, "frozen-interactive", service_class="interactive", age=5)
    interactive["request"]["service_class"] = "batch"
    db.put("tasks", interactive)
    first = db.claim("worker-a")
    assert first["id"] == "frozen-interactive"
    first["lease_until"] = time.time() - 1
    db.put("tasks", first)
    recovered = db.claim("worker-b")
    assert recovered["first_claimed_at"] == first["first_claimed_at"]
    assert recovered["lease_token"] == 2


def test_parallel_claims_do_not_duplicate_prioritized_tasks(db):
    for index in range(8):
        queued(db, f"task-{index}", service_class="interactive" if index % 2 else "batch")
    with ThreadPoolExecutor(max_workers=8) as pool:
        claims = list(pool.map(lambda index: db.claim(f"worker-{index}"), range(8)))
    assert len({task["id"] for task in claims}) == 8


def test_intake_freezes_scheduling_and_legacy_idempotency(db):
    p = project(db)
    a = asset(db, p)
    body = task_body(p, a)
    task = service.create_task(body, ADMIN, db)
    policy = task["snapshot"]["scheduling"]
    assert policy["service_class"] == "batch"
    assert (
        service.timestamp(policy["aging_at"]) - service.timestamp(task["created_at"])
    ).total_seconds() == 60
    # A real old stored request lacks the new key; normalized replay stays idempotent.
    task["request"].pop("service_class")
    task["snapshot"].pop("scheduling")
    db.put("tasks", task)
    assert service.create_task(body, ADMIN, db)["id"] == task["id"]
    with pytest.raises(service.DomainError) as error:
        service.create_task({**body, "service_class": "interactive"}, ADMIN, db)
    assert error.value.code == "idempotency_conflict"
    other = service.create_task(
        {**body, "idempotency_key": "interactive", "service_class": "interactive"}, ADMIN, db
    )
    assert other["snapshot"]["scheduling"]["service_class"] == "interactive"


def metric_task(db, p, name, current, *, seconds=10, finish_age=5, status="succeeded", **changes):
    finished = current - timedelta(seconds=finish_age)
    return db.put(
        "tasks",
        {
            "id": name,
            "project_id": p["id"],
            "idempotency_key": name,
            "status": status,
            "request": {"mode": "local"},
            "created_at": (finished - timedelta(seconds=seconds)).isoformat(),
            "finished_at": finished.isoformat(),
            **changes,
        },
    )


def test_metrics_real_window_percentiles_rehearsal_and_unknown_timing(db):
    p = project(db)
    current = datetime.now(UTC)
    metric_task(db, p, "ten", current, seconds=10)
    metric_task(db, p, "twenty", current, seconds=20, status="failed", reason_code="quality_not_met")
    metric_task(db, p, "too-old", current, seconds=1000, finish_age=90000, status="failed", reason_code="old")
    metric_task(db, p, "rehearsal", current, seconds=500, request={"mode": "rehearsal"})
    metric_task(db, p, "unknown", current, finished_at=None)
    metric_task(db, p, "negative", current, seconds=-5)
    metrics = contracts.OperationalMetrics.model_validate(
        service.operational_metrics(ADMIN, db, current_time=current)
    )
    assert metrics.counts.total == 5
    assert metrics.excluded_rehearsal_count == 1
    assert metrics.latency.sample_count == 2
    assert metrics.latency.p50_seconds == 15
    assert metrics.latency.p95_seconds == 19.5
    assert metrics.latency.excluded_count == 2
    assert [reason.model_dump() for reason in metrics.failure_reasons] == [
        {"reason_code": "quality_not_met", "count": 1}
    ]
    with_rehearsal = service.operational_metrics(ADMIN, db, include_rehearsal=True, current_time=current)
    assert with_rehearsal["latency"]["sample_count"] == 3


def test_metrics_empty_is_null_and_unclaimed_cancel_has_actual_finish(db):
    p = project(db)
    metrics = service.operational_metrics(ADMIN, db)
    assert metrics["latency"]["sample_count"] == 0
    assert metrics["latency"]["p50_seconds"] is None
    assert metrics["queue"]["oldest_wait_seconds"] is None
    task = service.create_task(task_body(p, asset(db, p)), ADMIN, db)
    cancelled = service.cancel_task(task["id"], ADMIN, db)
    assert service.timestamp(cancelled["finished_at"]) >= service.timestamp(task["created_at"])
    assert service.operational_metrics(ADMIN, db)["latency"]["sample_count"] == 1


def test_queue_ages_and_current_counts_include_recovery_but_not_live_leases(db):
    p = project(db)
    current = datetime.now(UTC)
    queued(db, "batch", age=30, project_id=p["id"])
    queued(db, "interactive", service_class="interactive", age=10, project_id=p["id"])
    queued(
        db,
        "recovery",
        age=500,
        project_id=p["id"],
        lease_token=1,
        lease_owner="stale",
        lease_until=current.timestamp() - 20,
        status="running",
    )
    queued(
        db,
        "live",
        age=600,
        project_id=p["id"],
        lease_token=1,
        lease_owner="live",
        lease_until=current.timestamp() + 20,
        status="running",
    )
    metrics = service.operational_metrics(ADMIN, db, current_time=current)
    assert metrics["counts"] == {"total": 4, "queued": 3, "active": 1, "terminal": 0}
    assert metrics["queue"]["by_service_class"] == {"batch": 2, "interactive": 1}
    assert 29 <= metrics["queue"]["oldest_wait_seconds"] <= 30


def test_metric_project_permissions_do_not_leak_hidden_tasks_or_worker_topology(db):
    visible = project(db, OTHER)
    hidden = project(db)
    current = datetime.now(UTC)
    queued(
        db,
        "mine",
        project_id=visible["id"],
        lease_owner="my-stale-worker",
        lease_token=1,
        lease_until=current.timestamp() - 1,
    )
    metric_task(db, hidden, "secret", current, status="failed", reason_code="secret_failure")
    for worker_id, age, active_ids in [
        ("my-stale-worker", 100, ["mine"]),
        ("hidden-worker", 0, ["secret"]),
        ("idle", 0, []),
    ]:
        db.put(
            "workers",
            {
                "id": worker_id,
                "status": "running",
                "active_task_ids": active_ids,
                "hostname": "private-host",
                "heartbeat_at": (current - timedelta(seconds=age)).isoformat(),
            },
        )
    reviewer = {**OTHER, "role": "reviewer"}
    metrics = service.operational_metrics(reviewer, db, current_time=current)
    assert metrics["project_ids"] == [visible["id"]]
    assert metrics["counts"]["total"] == 1
    assert metrics["failure_reasons"] == []
    assert metrics["workers"] == {
        "scope": "project_associated",
        "total": 1,
        "online": 0,
        "stale": 1,
        "stopped": 0,
    }
    with pytest.raises(service.DomainError) as denied:
        service.operational_metrics(reviewer, db, project_id=hidden["id"])
    assert denied.value.status == 403
    assert service.operational_metrics(ADMIN, db)["workers"]["total"] == 3
    assert service.operational_metrics(ADMIN, db, project_id=hidden["id"])["workers"]["total"] == 0


def test_metrics_route_auth_scope_query_validation_and_response_model(db):
    p = project(db)
    app = application()
    with TestClient(app) as client:
        response = client.get("/api/v2/metrics", params={"project_id": p["id"]})
        assert response.status_code == 200
        assert response.json()["include_rehearsal"] is False
        assert response.json()["latency"]["definition"].startswith("受理至终态")
        assert response.json()["scheduling"]["preemptive"] is False
        assert client.get("/api/v2/metrics", params={"window_hours": 0}).status_code == 422
        assert client.get("/api/v2/metrics", params={"window_hours": 721}).status_code == 422
        app.dependency_overrides[auth.get_current_user] = lambda: OTHER
        assert client.get("/api/v2/metrics", params={"project_id": p["id"]}).status_code == 403
        empty = client.get("/api/v2/metrics").json()
        assert empty["project_ids"] == []
        assert empty["counts"]["total"] == 0
        assert empty["workers"]["total"] == 0
        app.dependency_overrides.clear()
        assert client.get("/api/v2/metrics").status_code == 401
