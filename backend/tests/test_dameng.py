"""Dialect checks and a separately gated test against a real disposable DM8 schema."""

import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.engine import make_url
from sqlalchemy.schema import CreateTable

from backend.app.identity_records import DMIdentityStore
from backend.app.platform import service
from backend.app.platform.dameng import database_url, identity_metadata, json_scalar
from backend.app.platform.db import LeaseLost, Repository
from backend.app.platform.db_schema import CAPABILITY_TABLES, SCHEMA_VERSION, schema, tables
from scripts.dm_database import prepare_schema


def test_required_dm_never_falls_back_or_uses_system_account(monkeypatch):
    monkeypatch.setenv("HANGZHI_REQUIRE_DM", "true")
    monkeypatch.delenv("HANGZHI_DATABASE_URL", raising=False)
    for url in (None, "sqlite:///tmp.db", "postgresql://example/test"):
        with pytest.raises(RuntimeError, match="达梦"):
            database_url(url, Path("legacy.sqlite3"))
    for url in (
        "dm://SYSDBA@localhost/SYSDBA",
        "dm://ordinary@localhost/SOMEONE_ELSE",
        "dm://ordinary@localhost",
    ):
        with pytest.raises(ValueError):
            database_url(url, Path("."))


def test_dm8_terminal_task_deletion_retains_history(real_dm):
    from backend.tests.test_v2_platform import ADMIN, asset, task_body

    project = service.create_project({"name": "Synthetic deletion integration"}, ADMIN, real_dm)
    data = asset(real_dm, project)
    task = service.create_task(task_body(project, data), ADMIN, real_dm)
    service.cancel_task(task["id"], ADMIN, real_dm)
    service.delete_task(task["id"], ADMIN, real_dm)
    service.delete_task(task["id"], ADMIN, real_dm)
    stored = real_dm.get("tasks", task["id"])
    assert stored["status"] == "cancelled" and stored["deleted_at"]
    assert stored["snapshot"] == task["snapshot"]
    assert len(real_dm.list("events", task_id=task["id"], type="task.deleted")) == 1


def test_real_dm_dialect_compiles_clob_scalar_paths_and_fenced_locks():
    pytest.importorskip("dmSQLAlchemy")
    dialect = make_url("dm+dmPython://").get_dialect()()
    engine = SimpleNamespace(dialect=dialect)
    table = tables["tasks"]
    ddl = str(CreateTable(tables["events"]).compile(dialect=dialect)).upper()
    assert "CLOB" in ddl and "IDENTITY(1, 1)" in ddl
    q = select(
        table.c.id, json_scalar(engine, table.c.payload, "snapshot", "scheduling", "service_class")
    ).with_for_update(skip_locked=True)
    sql = str(q.compile(dialect=dialect)).upper()
    assert "JSON_VALUE(TASKS.PAYLOAD, '$.SNAPSHOT.SCHEDULING.SERVICE_CLASS')" in sql
    assert "FOR UPDATE SKIP LOCKED" in sql
    assert "JSON_VALUE(JSON_VALUE" not in sql


@pytest.fixture
def real_dm():
    raw = os.environ.get("HANGZHI_TEST_DM_URL")
    if not raw:
        pytest.skip("Actual DM8 test schema not configured")
    url = database_url(raw, Path("."))
    if url.get_backend_name() != "dm" or not url.database.upper().endswith("_TEST"):
        pytest.fail(
            "DM integration requires a dedicated *_TEST schema under its own non-administrative account"
        )
    admin = create_engine(url, hide_parameters=True)
    try:
        prepare_schema(admin)
        with admin.begin() as connection:
            for table in [*tables.values(), *identity_metadata.tables.values()]:
                if table.name != "hz_identity_guard":
                    connection.execute(table.delete())
    finally:
        admin.dispose()
    repository = Repository(raw)
    try:
        if any(
            t["status"] not in {"succeeded", "failed", "partial", "cancelled"}
            for t in repository.list("tasks")
        ):
            pytest.fail("The DM8 test schema contains unfinished tasks; refusing to claim them")
        yield repository
    finally:
        repository.engine.dispose()


def test_dm8_real_intake_claim_recovery_projection_and_identity(real_dm, tmp_path, monkeypatch):
    repository = real_dm
    actor = {"id": "synthetic-dm-user", "role": "admin"}
    project = service.create_project(
        {"name": "DM integration", "description": "synthetic"}, actor, repository
    )
    project_id = project["id"]
    account = "dm_test_" + uuid.uuid4().hex[:12]
    identities = DMIdentityStore(repository.engine, tmp_path)
    task_template = {
        "project_id": project_id,
        "name": "Compute",
        "goal": "Compute synthetic table",
        "task_type": "structured_transform",
        "idempotency_key": "same",
        "input_refs": [],
    }
    try:
        identities.save("users", {"id": account, "role": "operator", "enabled": True})
        assert identities.get("users", account)["role"] == "operator"
        multilingual = "平方 km² · Ελληνικά · источник · 🛫"
        value = multilingual * 1800
        asset = service.create_asset(
            {
                "project_id": project_id,
                "name": "Synthetic large JSON",
                "modality": "structured",
                "content": [{"text": value}],
                "source_id": "DM:safe",
                "provenance": "synthetic",
            },
            actor,
            repository,
        )
        assert repository.get("data_assets", asset["id"])["content"][0]["text"] == value
        table = tables["data_assets"]
        with repository.engine.connect() as connection:
            assert connection.connection.driver_connection.local_code == 1
            assert (
                connection.execute(
                    select(json_scalar(repository.engine, table.c.payload, "provenance")).where(
                        table.c.id == asset["id"]
                    )
                ).scalar_one()
                == asset["provenance"]
            )
            # Unlike the large whole CLOB, this scalar is returned as a native SQL string.
            marker = repository.put("nodes", {"project_id": project_id, "alert": {"code": multilingual}})
            node_table = tables["nodes"]
            scalar = json_scalar(repository.engine, node_table.c.payload, "alert", "code")
            assert (
                connection.execute(select(scalar).where(node_table.c.id == marker["id"])).scalar_one()
                == multilingual
            )
        _assert_filtered_rows(repository, project_id, monkeypatch)
        task_template["input_refs"] = [asset["id"]]
        with ThreadPoolExecutor(max_workers=4) as pool:
            tasks = list(pool.map(lambda _: service.create_task(task_template, actor, repository), range(4)))
        assert len({t["id"] for t in tasks}) == 1
        for i in range(3):
            service.create_task(
                {**task_template, "idempotency_key": f"new-{i}", "service_class": "interactive"},
                actor,
                repository,
            )
        metrics = service.operational_metrics(actor, repository, project_id=project_id)
        assert metrics["counts"]["queued"] == 4 and metrics["queue"]["by_service_class"] == {
            "batch": 1,
            "interactive": 3,
        }
        with ThreadPoolExecutor(max_workers=4) as pool:
            claims = list(pool.map(lambda i: repository.claim(f"dm-worker-{i}"), range(4)))
        assert len({c["id"] for c in claims if c}) == 4 and repository.claim("extra") is None
        old = claims[0]
        with repository.transaction() as tx:
            row = tx.get("tasks", old["id"], for_update=True)
            tx.put("tasks", {**row, "lease_until": time.time() - 5})
        new = repository.claim("recovery")
        assert new["id"] == old["id"] and new["lease_token"] > old["lease_token"]
        with pytest.raises(LeaseLost):
            repository.heartbeat(old["id"], old["lease_owner"], old["lease_token"])
        history = repository.list("events", project_id, task_id=old["id"])
        assert any(e["type"] == "lease.recovered" for e in history)
        assert all(e["sequence"] > 0 for e in history)
    finally:
        identities.delete("users", account)
        # Test owns exactly this synthetic project; never drop schema, truncate or erase other runs.
        with repository.engine.begin() as conn:
            for kind, table in tables.items():
                conn.execute(
                    table.delete().where(
                        (table.c.id if kind == "projects" else table.c.project_id) == project_id
                    )
                )


def test_dm8_real_transfer_preserves_cursor_versions_and_rolls_back(real_dm, tmp_path, monkeypatch):
    from backend.app.storage import Store
    from scripts.dm_business_migration import import_business
    from scripts.dm_database import import_identity

    monkeypatch.delenv("HANGZHI_REQUIRE_DM", raising=False)
    source = tmp_path / "history.sqlite3"
    old = Repository(f"sqlite:///{source.as_posix()}")
    user = {"id": "dm-transfer", "role": "admin"}
    project = service.create_project({"name": "Transfer", "description": "synthetic"}, user, old)
    pid = project["id"]
    identity_file = tmp_path / "old-identity.sqlite3"
    identity_source = Store(identity_file)
    historic_user = identity_source.save(
        "users", {"id": "unique-test-account", "role": "admin", "password_hash": "synthetic-preserved-hash"}
    )
    old_task = old.put(
        "tasks",
        {
            "id": "dm-transfer-" + uuid.uuid4().hex,
            "project_id": pid,
            "status": "succeeded",
            "snapshot": {"immutable": "版本"},
            "idempotency_key": "frozen",
        },
    )
    first = old.event(old_task["id"], "discard.temporary", {})
    old.delete("events", first["id"])
    event = old.event(old_task["id"], "synthetic.completed", {})
    ref = old.put(
        "data_assets",
        {"id": "bad-ref", "project_id": pid, "content_ref": str(tmp_path / "missing"), "checksum": "0"},
    )
    try:
        with pytest.raises(ValueError, match="媒体原件"):
            import_business(real_dm.engine, source, tmp_path)
        assert real_dm.get("projects", pid) is None
        old.delete("data_assets", ref["id"])
        counts = import_business(real_dm.engine, source, tmp_path)
        assert counts["events"] == 1 and counts["agent_versions"] > 0
        assert real_dm.get("events", event["id"]) == event
        assert real_dm.get("tasks", old_task["id"]) == old_task
        imported_event = real_dm.event(old_task["id"], "synthetic.continuation", {})
        assert imported_event["sequence"] > event["sequence"]
        with pytest.raises(ValueError, match="非空"):
            import_business(real_dm.engine, source, tmp_path)
        assert import_identity(real_dm.engine, identity_file) == {"users": 1}
        account = DMIdentityStore(real_dm.engine, tmp_path)
        assert account.get("users", historic_user["id"]) == historic_user
        with pytest.raises(ValueError, match="为空"):
            import_identity(real_dm.engine, identity_file)
    finally:
        DMIdentityStore(real_dm.engine, tmp_path).delete("users", historic_user["id"])
        with real_dm.engine.begin() as conn:
            for kind, table in tables.items():
                conn.execute(
                    table.delete().where((table.c.id if kind == "projects" else table.c.project_id) == pid)
                )
        old.engine.dispose()


def _assert_filtered_rows(repository, project_id, monkeypatch):
    selected = repository.put(
        "nodes", {"project_id": project_id, "task_id": "task-a", "agent_id": "expert-a", "tags": ["keep"]}
    )
    repository.put(
        "nodes",
        {"project_id": project_id, "task_id": "task-b", "agent_id": "expert-a", "output": "unrelated"},
    )
    repository.put(
        "nodes",
        {"project_id": project_id, "task_id": "task-a", "agent_id": "expert-b", "output": "unrelated"},
    )
    hydrated = []
    original = Repository._record

    def record(row):
        hydrated.append(row._mapping["id"])
        return original(row)

    with monkeypatch.context() as m:
        m.setattr(Repository, "_record", staticmethod(record))
        assert repository.list("nodes", project_id, task_id="task-a", agent_id="expert-a", tags=["keep"]) == [
            selected
        ]
    # The exact SQL result is already scoped before JSON payloads enter Python;
    # non-string complex fields still use the original full-value comparison.
    assert hydrated == [selected["id"]]


def test_sqlite_filters_hydrate_only_matching_task_and_agent(tmp_path, monkeypatch):
    monkeypatch.delenv("HANGZHI_REQUIRE_DM", raising=False)
    db = Repository(f"sqlite:///{(tmp_path / 'scoped.sqlite3').as_posix()}")
    try:
        _assert_filtered_rows(db, "project-granted", monkeypatch)
    finally:
        db.engine.dispose()


def test_real_dm_v1_to_v2_upgrade_is_additive_and_idempotent(real_dm):
    repository = real_dm
    actor = {"id": "synthetic-dm-user", "role": "admin"}
    project = service.create_project(
        {"name": "DM v2 migration", "description": "synthetic"}, actor, repository
    )
    before = (len(repository.list("projects")), len(repository.list("tasks")))
    with repository.engine.begin() as connection:
        for name in sorted(CAPABILITY_TABLES):
            tables[name].drop(connection, checkfirst=True)
        connection.execute(schema.update().values(version=1))
    engine = create_engine(repository.engine.url, hide_parameters=True)
    try:
        prepare_schema(engine)
        prepare_schema(engine)
    finally:
        engine.dispose()
    with repository.engine.connect() as connection:
        assert connection.execute(schema.select()).scalars().all() == [SCHEMA_VERSION]
    assert (len(repository.list("projects")), len(repository.list("tasks"))) == before
    assert repository.get("projects", project["id"]) == project
