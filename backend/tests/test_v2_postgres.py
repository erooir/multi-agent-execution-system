"""Run against an actual PostgreSQL service in CI; never substitute SQLite here."""

import os
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from backend.app.platform import service
from backend.app.platform.db import Repository


@pytest.fixture
def postgres():
    url = os.environ.get("HANGZHI_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("Actual PostgreSQL service not configured on this host")
    if not (make_url(url).database or "").endswith("_test"):
        raise RuntimeError("PostgreSQL tests require an explicitly named *_test database")
    schema = "test_" + uuid.uuid4().hex
    admin = create_engine(url)
    with admin.begin() as conn:
        conn.execute(text(f"CREATE SCHEMA {schema}"))
    scoped = make_url(url).update_query_dict({"options": f"-csearch_path={schema}"})
    with ThreadPoolExecutor(max_workers=4) as pool:
        repositories = list(
            pool.map(lambda _: Repository(scoped.render_as_string(hide_password=False)), range(4))
        )
    repository, *extra = repositories
    for instance in extra:
        instance.engine.dispose()
    try:
        yield repository
    finally:
        repository.engine.dispose()
        with admin.begin() as conn:
            conn.execute(text(f"DROP SCHEMA {schema} CASCADE"))
        admin.dispose()


def test_postgres_concurrent_intake_and_skip_locked_claims(postgres):
    actor = {"id": "ci-synthetic", "role": "admin"}
    project = service.create_project(
        {"name": "PostgreSQL integration", "description": "Synthetic CI"}, actor, postgres
    )
    asset = service.create_asset(
        {
            "project_id": project["id"],
            "name": "fixture",
            "modality": "structured",
            "content": [{"value": 1}],
            "source_id": "synthetic:pg",
            "provenance": "Synthetic fixture",
        },
        actor,
        postgres,
    )
    request = {
        "project_id": project["id"],
        "name": "Queue test",
        "goal": "Normalize records",
        "task_type": "structured_transform",
        "input_refs": [asset["id"]],
        "idempotency_key": "same-request",
    }
    with ThreadPoolExecutor(max_workers=4) as pool:
        tasks = list(pool.map(lambda _: service.create_task(request, actor, postgres), range(4)))
    assert len({task["id"] for task in tasks}) == 1
    for index in range(3):
        service.create_task({**request, "idempotency_key": f"extra-{index}"}, actor, postgres)
    with ThreadPoolExecutor(max_workers=4) as pool:
        claims = list(pool.map(lambda index: postgres.claim(f"pg-worker-{index}"), range(4)))
    assert len({claim["id"] for claim in claims if claim}) == 4
    assert postgres.claim("pg-worker-extra") is None
    with postgres.transaction() as tx:
        event = tx.event(tasks[0]["id"], "integration.checked", {})
        assert event["sequence"] > 0
