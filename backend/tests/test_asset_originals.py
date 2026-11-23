import hashlib
import os
import subprocess
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from backend.app import auth
from backend.app.platform import asset_originals as originals
from backend.app.platform import knowledge, service
from backend.app.platform.contracts import FILE_INDEX_MEDIA_TYPE
from backend.tests import test_v2_platform as fixtures
from backend.tests.test_v2_api_review import application
from backend.tests.test_v2_platform import ADMIN, OTHER, asset, project

db = fixtures.db


@pytest.fixture
def source(db, tmp_path, monkeypatch):
    monkeypatch.setattr(originals, "ROOT", tmp_path / "deployment")
    row = asset(db, project(db))
    payload = b"sample,2025\n" * 150_000
    checksum = hashlib.sha256(payload).hexdigest()
    metadata = {"filename": "原始记录.csv", "sha256": checksum, "bytes": len(payload)}
    path = originals.original_path(row["project_id"], checksum)
    path.parent.mkdir(parents=True)
    path.write_bytes(payload)
    return row, metadata, path


def test_idempotent_attachment_and_authorized_binary_download(db, source):
    row, meta, path = source
    app = application()
    url = "/api/v2/data-assets/" + row["id"] + "/original"
    with TestClient(app) as client:
        assert client.get(url).status_code == 404
        with pytest.raises(service.DomainError) as denied:
            originals.attach_original(row["id"], meta, OTHER, db)
        assert denied.value.status == 403
        for _ in range(2):
            assert originals.attach_original(row["id"], meta, ADMIN, db)["original_file"] == meta
        assert len(db.list("audits", row["project_id"], action="data.original_attached")) == 1
        with pytest.raises(service.DomainError) as conflict:
            originals.attach_original(row["id"], {**meta, "filename": "other.csv"}, ADMIN, db)
        assert conflict.value.code == "original_conflict"
        response = client.get(url)
        assert response.status_code == 200
        assert hashlib.sha256(response.content).hexdigest() == meta["sha256"]
        assert int(response.headers["content-length"]) == path.stat().st_size
        assert response.headers["content-type"] == "application/octet-stream"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["content-disposition"] == "attachment; filename*=UTF-8''" + quote(
            meta["filename"]
        )
        app.dependency_overrides[auth.get_current_user] = lambda: OTHER
        assert client.get(url).status_code == 403
        member = {**ADMIN, "role": "reviewer"}
        app.dependency_overrides[auth.get_current_user] = lambda: member
        assert client.get(url).status_code == 200
        with pytest.raises(service.DomainError) as denied:
            originals.attach_original(row["id"], meta, member, db)
        assert denied.value.status == 403
        app.dependency_overrides[auth.get_current_user] = lambda: {**member, "role": "requester"}
        assert client.get(url).status_code == 403


@pytest.mark.parametrize(
    "change, expected",
    [
        ({"status": "revoked"}, 409),
        ({"expires_at": "2000-01-01T00:00:00+00:00"}, 409),
        ({"task_case_id": "case-1"}, 403),
    ],
)
def test_forbidden_assets_do_not_expose_originals(db, source, change, expected):
    row, meta, _ = source
    db.put("data_assets", {**row, **change, "original_file": meta})
    with TestClient(application()) as client:
        assert client.get("/api/v2/data-assets/" + row["id"] + "/original").status_code == expected


async def test_search_attached_original_matches_content_then_display_metadata(db, source):
    row, metadata, _ = source
    originals.attach_original(row["id"], metadata, ADMIN, db)
    query = {"project_id": row["project_id"]}
    (body_hit,) = await knowledge.search({**query, "query": "ALPHA"}, ADMIN)
    assert "field" not in body_hit["location"] and "Alpha" in body_hit["text"]
    (name_hit,) = await knowledge.search({**query, "query": row["name"].upper()}, ADMIN)
    assert name_hit["location"]["field"] == "metadata"
    stored = db.get("data_assets", row["id"])
    stored = db.put("data_assets", {**stored, "content": None})
    for value in (metadata["filename"], row["source_id"], row["name"]):
        (hit,) = await knowledge.search({**query, "query": value.swapcase()}, ADMIN)
        assert hit["id"] == row["id"] and hit["location"]["field"] == "metadata"
        assert value in hit["text"]
    db.put("data_assets", {**stored, "media_type": FILE_INDEX_MEDIA_TYPE, "content": {"title": "index-only"}})
    (index_hit,) = await knowledge.search({**query, "query": "index-only"}, ADMIN)
    assert index_hit["location"]["field"] == "metadata"


@pytest.mark.parametrize(
    "hidden", [{"task_case_id": "other-case"}, {"status": "revoked"}, {"expires_at": "2000-01-01T00:00:00Z"}]
)
async def test_original_search_respects_project_scope_and_source_availability(db, source, hidden):
    row, metadata, _ = source
    originals.attach_original(row["id"], metadata, ADMIN, db)
    stored = db.get("data_assets", row["id"])
    query = {"project_id": row["project_id"], "query": metadata["filename"]}
    with pytest.raises(service.DomainError) as denied:
        await knowledge.search(query, OTHER)
    assert denied.value.status == 403
    assert await knowledge.search({**query, "project_id": project(db)["id"]}, ADMIN) == []
    db.put("data_assets", {**stored, **hidden})
    assert await knowledge.search(query, ADMIN) == []


def test_missing_and_corrupt_storage_fail_before_serving_bytes(db, source):
    row, meta, path = source
    originals.attach_original(row["id"], meta, ADMIN, db)
    with TestClient(application()) as client:
        url = "/api/v2/data-assets/" + row["id"] + "/original"
        with path.open("r+b") as stream:
            stream.write(b"X")
        response = client.get(url)
        assert response.status_code == 409 and response.json()["detail"]["code"] == "original_changed"
        path.unlink()
        assert client.get(url).status_code == 404


def test_rejects_unsafe_storage_identifiers_and_attachment_names(db, source):
    row, meta, _ = source
    for pid, digest in [("../other", meta["sha256"]), (row["project_id"], "../other")]:
        with pytest.raises(service.DomainError) as invalid:
            originals.original_path(pid, digest)
        assert invalid.value.code == "invalid_storage_ref"
    for name in ["../doc.pdf", r"folder\doc.pdf", "header\r\nvalue", "NUL.pdf"]:
        with pytest.raises(service.DomainError) as invalid:
            originals.attach_original(row["id"], {**meta, "filename": name}, ADMIN, db)
        assert invalid.value.code == "invalid_original"


def test_project_directory_link_is_rejected(db, source):
    row, meta, path = source
    parent = path.parent
    other = parent.with_name("other")
    parent.rename(other)
    if os.name == "nt":
        subprocess.run(
            ["cmd", "/d", "/c", "mklink", "/J", str(parent), str(other)], check=True, capture_output=True
        )
    else:
        parent.symlink_to(other, target_is_directory=True)
    with pytest.raises(service.DomainError) as invalid:
        originals.attach_original(row["id"], meta, ADMIN, db)
    assert invalid.value.code == "invalid_storage_ref"
