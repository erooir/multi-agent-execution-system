"""Synthetic checks for the v2 capability/schema adaptation; no provider or DM calls."""

import sqlite3

import pytest
from fastapi.testclient import TestClient

from backend.app import auth
from backend.app.platform import api, capability_service, service
from backend.app.platform.contracts import (
    AgentInput,
    ImageUnderstandingResult,
    OcrResult,
    TaskInput,
    ToolParameters,
)
from backend.app.platform.db import Repository
from backend.tests.conftest import skill_zip
from backend.tests.test_v2_api_review import application

ADMIN = {"id": "admin", "role": "admin", "username": "admin"}


def test_schema_v1_upgrade_preserves_rows_and_freezes_capability_versions(tmp_path, monkeypatch):
    monkeypatch.delenv("HANGZHI_REQUIRE_DM", raising=False)
    path = tmp_path / "migration.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE schema_version(version INTEGER PRIMARY KEY)")
        connection.execute("INSERT INTO schema_version VALUES(1)")
        connection.execute("CREATE TABLE projects(id TEXT PRIMARY KEY, payload JSON NOT NULL)")
        connection.execute("INSERT INTO projects VALUES('preserved', '{}')")

    repository = Repository(f"sqlite:///{path.as_posix()}")
    try:
        with sqlite3.connect(path) as connection:
            assert connection.execute("SELECT version FROM schema_version").fetchone()[0] == 2
            assert connection.execute("SELECT id FROM projects").fetchone()[0] == "preserved"
            names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master")}
        assert {"questions", "capability_definitions", "capability_versions"} <= names
        version = repository.put(
            "capability_versions",
            {
                "id": "definition:1",
                "definition_id": "definition",
                "project_id": "preserved",
                "version": 1,
                "spec": {"synthetic": True},
            },
        )
        with pytest.raises(ValueError, match="immutable"):
            repository.put("capability_versions", {**version, "spec": {"synthetic": False}})
        with pytest.raises(ValueError, match="cannot be deleted"):
            repository.delete("capability_versions", version["id"])
    finally:
        repository.engine.dispose()


def test_multimodal_and_capability_contracts_keep_cloud_validation():
    for task_type, mode in (("image_ocr", "local"), ("image_understand", "cloud")):
        task = TaskInput(
            project_id="p",
            name="Synthetic image",
            goal="Inspect a synthetic image",
            task_type=task_type,
            mode=mode,
            idempotency_key=f"same-{task_type}",
            max_cost_cny=1 if mode == "cloud" else 0,
        )
        assert task.task_type == task_type
    with pytest.raises(ValueError, match="用例分析任务"):
        TaskInput(
            project_id="p",
            name="Invalid case binding",
            goal="Reject a case id on OCR",
            task_type="image_ocr",
            case_id="B01",
            idempotency_key="invalid-case",
        )
    agent = AgentInput(project_id="p", name="Vision", capabilities=["image_ocr", "image_understand"])
    assert agent.capabilities == ["image_ocr", "image_understand"]
    assert ToolParameters(min_confidence=0.5).min_confidence == 0.5
    ocr = OcrResult.model_validate(
        {
            "images": [
                {
                    "source_id": "synthetic",
                    "source_version": "1",
                    "text": "42",
                    "blocks": [{"text": "42", "confidence": 0.9, "bbox": [[0, 0], [1, 1]]}],
                    "no_text": False,
                }
            ]
        }
    )
    assert ocr.images[0].blocks[0].confidence == 0.9
    vision = ImageUnderstandingResult.model_validate(
        {
            "text": "Synthetic result",
            "evidence": [{"source_id": "synthetic", "source_version": "1", "bbox": None}],
        }
    )
    assert vision.missing_information == []


@pytest.mark.parametrize(
    ("facade_name", "implementation_name", "args", "kwargs", "expected_args"),
    [
        ("import_skill", "import_skill", (b"package", "p", ADMIN), {}, (b"package", "p", ADMIN, None)),
        (
            "create_http_tool",
            "create_http_tool",
            ({"name": "http"}, ADMIN),
            {},
            ({"name": "http"}, ADMIN, None),
        ),
        (
            "create_native_toolkit",
            "create_native_toolkit",
            ({"name": "native"}, ADMIN),
            {},
            ({"name": "native"}, ADMIN, None),
        ),
        (
            "create_mcp_server",
            "create_mcp_server",
            ({"name": "mcp"}, ADMIN),
            {},
            ({"name": "mcp"}, ADMIN, None),
        ),
        (
            "publish_capability",
            "publish",
            ("definition", ADMIN),
            {"tool_bindings": {"tool": "mapped"}},
            ("definition", ADMIN, None, {"tool": "mapped"}),
        ),
        (
            "capability_action",
            "action",
            ("definition", "disable", ADMIN),
            {},
            ("definition", "disable", ADMIN, None),
        ),
    ],
)
def test_capability_facade_delegates_with_default_repository(
    monkeypatch, facade_name, implementation_name, args, kwargs, expected_args
):
    default_repository = object()
    result = object()
    calls = []
    monkeypatch.setattr(service, "repo", default_repository)

    def delegate(*received):
        calls.append(received)
        return result

    monkeypatch.setattr(capability_service, implementation_name, delegate)
    assert getattr(service, facade_name)(*args, **kwargs) is result
    assert calls == [tuple(default_repository if item is None else item for item in expected_args)]


@pytest.mark.asyncio
async def test_capability_facade_delegates_mcp_discovery(monkeypatch):
    default_repository = object()
    result = object()
    calls = []
    monkeypatch.setattr(service, "repo", default_repository)

    async def delegate(*received):
        calls.append(received)
        return result

    monkeypatch.setattr(capability_service, "discover_mcp", delegate)
    assert await service.discover_mcp("definition", ADMIN) is result
    assert calls == [("definition", ADMIN, default_repository)]


def test_capability_service_lifecycle_catalog_and_permissions(tmp_path, monkeypatch):
    monkeypatch.delenv("HANGZHI_REQUIRE_DM", raising=False)
    repository = Repository(f"sqlite:///{(tmp_path / 'capabilities.sqlite3').as_posix()}")
    try:
        repository.put("projects", {"id": "p", "members": [], "status": "active"})
        definition = service.import_skill(skill_zip(), "p", ADMIN, repository)
        assert definition["upstream_version"] == "1.2.0"
        assert "draft_spec" not in service.capability_view(definition)
        published = service.publish_capability(
            definition["id"], ADMIN, repository, {"list_sources": "data.list_sources"}
        )
        assert published["current_version"] == 1
        versions = service.capability_versions(definition["id"], ADMIN, repository)
        assert [item["version"] for item in versions] == [1]
        catalog = service.capability_catalog("p", ADMIN, repository)
        assert any(item["id"] == definition["id"] for item in catalog["definitions"])
        assert any(item["id"] == definition["id"] for item in catalog["skills"])
        assert (
            service.capability_action(definition["id"], "disable", ADMIN, repository)["status"] == "disabled"
        )
        assert (
            service.capability_action(definition["id"], "enable", ADMIN, repository)["status"] == "published"
        )
        assert service.capability_action(definition["id"], "revoke", ADMIN, repository)["status"] == "revoked"
        with pytest.raises(service.DomainError, match="不能发布"):
            service.publish_capability(definition["id"], ADMIN, repository)
        stranger = {"id": "stranger", "role": "reviewer"}
        with pytest.raises(service.DomainError) as error:
            service.capability_definitions("p", stranger, repository)
        assert error.value.status == 403

        agent = repository.put(
            "agents",
            {
                "id": "referencing-agent",
                "project_id": "p",
                "status": "published",
                "tool_refs": [{"tool_id": definition["id"], "version": 1}],
                "skill_refs": [],
            },
        )

        def unexpected_outer_list(*args, **kwargs):
            raise AssertionError("destroy reference checks must use the transaction")

        monkeypatch.setattr(repository, "list", unexpected_outer_list)
        with pytest.raises(service.DomainError) as error:
            service.capability_action(definition["id"], "destroy", ADMIN, repository)
        assert error.value.code == "reference_protected"
        agent["status"] = "destroyed"
        repository.put("agents", agent)
        assert (
            service.capability_action(definition["id"], "destroy", ADMIN, repository)["status"] == "destroyed"
        )
    finally:
        repository.engine.dispose()


def test_capability_manage_api_import_publish_and_role_boundary(tmp_path, monkeypatch):
    monkeypatch.delenv("HANGZHI_REQUIRE_DM", raising=False)
    repository = Repository(f"sqlite:///{(tmp_path / 'capability-api.sqlite3').as_posix()}")
    try:
        repository.put("projects", {"id": "p", "members": [], "status": "active"})
        monkeypatch.setattr(api, "repo", repository)
        monkeypatch.setattr(service, "repo", repository)
        app = application()
        with TestClient(app) as client:
            response = client.post(
                "/api/v2/capability-definitions/skills/import",
                data={"project_id": "p"},
                files={"file": ("skill.zip", skill_zip(), "application/zip")},
            )
            assert response.status_code == 201, response.text
            definition = response.json()
            assert "draft_spec" not in definition
            response = client.post(
                f"/api/v2/capability-definitions/{definition['id']}/publish",
                json={"tool_bindings": {"list_sources": "data.list_sources"}},
            )
            assert response.status_code == 200, response.text
            assert (
                client.get(f"/api/v2/capability-definitions/{definition['id']}/versions").json()[0]["version"]
                == 1
            )
            assert client.get("/api/v2/capability-definitions", params={"project_id": "p"}).json()
            app.dependency_overrides[auth.get_current_user] = lambda: {
                "id": "requester",
                "role": "requester",
            }
            assert client.get("/api/v2/capability-definitions", params={"project_id": "p"}).status_code == 403
    finally:
        repository.engine.dispose()
