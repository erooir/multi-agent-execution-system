"""Reference coverage is controlled by an operator, never by task or asset metadata."""

import hashlib
import json
from copy import deepcopy

import pytest

from backend.app.casework import reference_batches as batches
from backend.app.casework import team
from backend.app.platform import runtime, service
from backend.tests import test_v2_platform as fixtures
from backend.tests.test_casework_geo_samples import samples
from backend.tests.test_v2_platform import ADMIN, asset, project, run, task_body
from scripts.register_reference_batches import custom_registration, install, registrations

db = fixtures.db


def batch(pid, content, version="fixed-v1", source_version="1", conditions=()):
    return batches.signed_entry(
        {
            "project_id": pid,
            "case_id": "G07",
            "version": version,
            "sources": [{"sha256": service.digest(content), "source_version": source_version}],
            "conditions": list(conditions),
        }
    )


@pytest.fixture
def registry(tmp_path, monkeypatch):
    path = tmp_path / "operator" / "reference-batches.json"
    monkeypatch.setenv("HANGZHI_REFERENCE_BATCHES_FILE", str(path))
    return path


async def test_reduced_g07_cannot_claim_reference_but_sample_passes(db, registry):
    p = project(db)
    team.install(p["id"], ADMIN, db)
    content = {"records": samples("G07")}
    a = asset(
        db,
        p,
        content,
        source_version="1",
        provenance="Operator-approved complete reference batch (untrusted claim).",
    )
    request = task_body(p, a, task_type="case_analysis", case_id="G07", max_revisions=0, allow_partial=True)
    sample = await run(db, {**request, "case_scope": "sample"})
    assert sample["status"] == "succeeded" and "reference_batch" not in sample["snapshot"]["case_profile"]
    preview = service.create_task(
        {**request, "case_scope": "reference", "idempotency_key": "before"}, ADMIN, db, preflight=True
    )
    assert any("未登记" in msg for msg in preview["warnings"])

    full = deepcopy(content)
    full["records"].append({**full["records"][-1], "sample_id": "second-real-observation"})
    install(registry, [batch(p["id"], full)], False)
    finished = await run(db, {**request, "case_scope": "reference", "idempotency_key": "micro"})
    assert finished["status"] in {"partial", "failed"}, finished["status"]
    report = db.list("artifacts", p["id"], task_id=finished["id"])[0]["content"]
    assert any("缺少规定版本/摘要" in gap for gap in report["gaps"])
    assert any(c["name"] == "trusted_reference_sources" and not c["passed"] for c in report["review"])


async def test_frozen_reference_survives_registry_rotation_and_projects_are_isolated(db, registry):
    p, other = project(db), project(db)
    team.install(p["id"], ADMIN, db)
    content = {"records": samples("G07")}
    a = asset(db, p, content, source_version="1")
    spec = batch(
        p["id"],
        content,
        conditions=[
            {"kind": "record_count", "sha256": service.digest(content), "count": len(content["records"])},
            {"kind": "product_count", "product": "results", "count": 2},
            {"kind": "product_values", "product": "results", "field": "date", "values": ["2024-01-02"]},
        ],
    )
    install(registry, [spec], False)
    public_algo = team.frozen_profile("G07")["signature"]
    request = task_body(
        p,
        a,
        task_type="case_analysis",
        case_id="G07",
        case_scope="reference",
        max_revisions=0,
        allow_partial=True,
    )
    task = service.create_task(request, ADMIN, db)
    snap = task["snapshot"]
    assert batches.freeze(other["id"], "G07")["entry"] is None
    profile = snap["case_profile"]
    assert profile["reference_batch"]["entry"] == spec
    replacement = batch(p["id"], {"records": ["replacement source"]}, version="v2")
    install(registry, [replacement], True)
    assert team.frozen_profile("G07")["signature"] == public_algo
    assert batches.freeze(p["id"], "G07")["entry"] == replacement
    assert len(list((registry.parent / "history").glob("*.json"))) == 1
    claim = db.claim("frozen-reference-test")
    assert claim["id"] == task["id"]
    finished = await runtime.process_claim(db, claim, "frozen-reference-test")
    assert finished["status"] == "succeeded", finished.get("quality_verdict")
    report = db.list("artifacts", p["id"], task_id=task["id"])[0]["content"]
    assert report["reference_batch"]["version"] == spec["version"]
    assert all(c["passed"] for c in report["review"])
    assert batches.source_checks(profile, snap["inputs"], other["id"])[0]["hard"]
    forged = deepcopy(profile)
    forged["reference_batch"]["entry"]["sources"][0]["source_version"] = "forged"
    assert batches.source_checks(forged, snap["inputs"], p["id"])[0]["hard"]
    mismatched = [{**a, "source_version": "new-version"}]
    assert not batches.source_checks(profile, mismatched, p["id"])[0]["passed"]
    checks = batches.review_checks(
        profile, snap["inputs"], {"results": [{"date": "2024-01-03"}]}, [], p["id"]
    )
    assert not any(c["passed"] for c in checks if c["name"] != "trusted_reference_sources")


def test_operator_only_registration_checks_duplicates_archive_and_corruption(registry, tmp_path, monkeypatch):
    # Do not depend on whether this checkout shares its real asset directory via a
    # Windows junction. This branch exercises the upload-directory exclusion;
    # link exclusion is checked separately below, before any file is read.
    isolated_root = tmp_path / "checkout"
    (isolated_root / ".local/platform-assets").mkdir(parents=True)
    monkeypatch.setattr(batches, "ROOT", isolated_root)
    monkeypatch.setattr("scripts.register_reference_batches.ROOT", isolated_root)
    content = ["public"]
    pub = batch(None, content)
    private = batch("p", ["private"], version="private-v1")
    install(registry, [pub, private], False)
    install(registry, [pub, private], False)
    assert batches.freeze("p", "G07")["entry"] == private
    assert batches.freeze("q", "G07")["entry"] == pub
    with pytest.raises(ValueError, match="同版本"):
        install(registry, [batch(None, ["overwritten"])], True)
    assert not registry.with_suffix(".lock").exists()
    assert batches.freeze("q", "G07")["entry"] == pub
    install(registry, [batch(None, ["next"], version="v2")], True)
    with pytest.raises(ValueError, match="历史同版本"):
        install(registry, [batch(None, ["overwritten"])], True)
    registry.write_text(
        registry.read_text(encoding="utf-8").replace("private-v1", "forged"), encoding="utf-8"
    )
    assert batches.freeze("q", "G07")["state"] == "registry_invalid"
    for action in (
        lambda: install(batches.ROOT / ".local/platform-assets/forged.json", [pub], False),
        lambda: registrations(batches.ROOT / ".local/platform-assets", None, "forged"),
    ):
        with pytest.raises(ValueError, match="上传资产"):
            action()


@pytest.mark.parametrize("link_check", ["is_symlink", "is_junction"])
def test_reference_registration_rejects_linked_parent_before_reading(tmp_path, monkeypatch, link_check):
    from pathlib import Path

    from scripts.register_reference_batches import _allowed_directory

    linked_parent = tmp_path / "untrusted-link"
    original = getattr(Path, link_check)
    monkeypatch.setattr(Path, link_check, lambda self: self == linked_parent or original(self))
    with pytest.raises(ValueError, match="符号链接或目录联接"):
        _allowed_directory(linked_parent / "reference-batches.json")


def test_custom_manifest_enrolls_only_independently_verified_local_files(tmp_path, registry):
    src = tmp_path / "operator-inputs"
    src.mkdir()
    data = b"row,value\nA,1\n"
    checksum = hashlib.sha256(data).hexdigest()
    (src / "observations.csv").write_bytes(data)
    declaration = {
        "schema_version": 1,
        "case_id": "B02",
        "sources": [{"path": "observations.csv", "sha256": checksum, "source_version": "client-rev-3"}],
        "conditions": [{"kind": "record_count", "sha256": checksum, "count": 1}],
    }
    manifest = src / "batch.json"
    manifest.write_text(json.dumps(declaration), encoding="utf-8")
    entries = custom_registration(manifest, "p", "batch-3")
    install(registry, entries, False)
    assert batches.freeze("p", "B02")["entry"] == entries[0] and batches.freeze("q", "B02")["entry"] is None
    assert "observations.csv" not in registry.read_text(encoding="utf-8")
    (src / "observations.csv").write_bytes(b"row,value\nA,999\n")
    with pytest.raises(ValueError, match="SHA-256"):
        custom_registration(manifest, "p", "batch-4")
    declaration["sources"][0]["path"] = "../other/file.csv"
    manifest.write_text(json.dumps(declaration), encoding="utf-8")
    with pytest.raises(ValueError, match="说明所在目录"):
        custom_registration(manifest, "p", "batch-4")


def test_b06_verified_glossary_is_required_but_does_not_bind_either_version(tmp_path, registry):
    materials = tmp_path / "inputs"
    materials.mkdir()
    sources, assets = [], []
    for role, label in [("source", "glossary"), ("after", "revision"), ("before", "draft")]:
        path = materials / (label + ".txt")
        raw = ("synthetic test " + label).encode()
        path.write_bytes(raw)
        checksum = hashlib.sha256(raw).hexdigest()
        version = label + "-1"
        sources.append({"path": path.name, "sha256": checksum, "source_version": version, "role": role})
        assets.append({"id": label, "project_id": "p", "checksum": checksum, "source_version": version})
    manifest = materials / "batch.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "case_id": "B06",
                "supplement": "before_after_binding",
                "sources": sources,
            }
        ),
        encoding="utf-8",
    )
    entry = custom_registration(manifest, "p", "with-glossary")
    install(registry, entry, False)
    profile = {"case_id": "B06", "reference_batch": batches.freeze("p", "B06")}
    binding = {
        "id": "roles",
        "project_id": "p",
        "checksum": hashlib.sha256(b"synthetic role binding").hexdigest(),
        "source_version": "roles-1",
        "modality": "structured",
        "content": [
            {"kind": "document_binding", "target_source_id": "draft", "side": "before"},
            {"kind": "document_binding", "target_source_id": "revision", "side": "after"},
        ],
    }

    def accepted(inputs):
        return all(c["passed"] for c in batches.source_checks(profile, inputs, "p"))

    assert accepted([binding, *assets])
    assert not accepted([binding, *assets[1:]])  # omit glossary
    assert not accepted([binding, {**assets[0], "source_version": "stale"}, *assets[1:]])
    assert not accepted([binding, *assets, {**assets[0], "id": "extra", "checksum": "a" * 64}])
    assert not accepted([binding, *assets, {**assets[0], "id": "duplicate"}])
    assert not accepted(
        [{**binding, "checksum": assets[0]["checksum"]}, *assets]
    )  # duplicate disguised as binding
    assert not accepted([{**assets[0], "project_id": "other"}, *assets[1:], binding])
    assert not accepted(assets)
    for malformed in [
        {"side": "source", "target_source_id": "glossary"},
        {"side": [], "target_source_id": "draft"},
        {"side": "before", "target_source_id": "glossary"},
        {"side": "before", "target_source_id": "revision"},
        {"payload": "unregistered answer"},
    ]:
        wrong = deepcopy(binding)
        wrong["content"][0].update(malformed)
        assert not accepted([wrong, *assets])
    three = deepcopy(binding)
    three["content"].append({"kind": "document_binding", "side": "source", "target_source_id": "glossary"})
    assert not accepted([three, *assets])


@pytest.mark.parametrize(
    ("code", "roles"),
    [
        ("B06", ["before", "after", "before"]),
        ("B06", ["before", "after", "after"]),
        ("B06", ["before", "source", "source"]),
        ("B06", ["after", "source"]),
        ("T06", ["before", "after", "source"]),
        ("B06", ["before", "after", *(["source"] * 48)]),
    ],
)
def test_b06_source_roles_remain_unambiguous(code, roles):
    with pytest.raises(ValueError):
        batches.signed_entry(
            {
                "case_id": code,
                "version": "v1",
                "supplement": "before_after_binding",
                "sources": [
                    {"sha256": f"{i:064x}", "source_version": "1", "role": role}
                    for i, role in enumerate(roles, 1)
                ],
            }
        )
