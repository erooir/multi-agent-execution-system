"""Backup/restore and public packaging regression cases with synthetic data."""

import json
import sqlite3
import subprocess
import zipfile
from contextlib import closing

import pytest

from backend.app.model_gateway import BudgetLedger
from scripts.offline_package import create_package, validate_public_path
from scripts.runtime_backup import create_backup, restore_backup, verify_backup


def synthetic_runtime(root):
    local = root / ".local"
    local.mkdir()
    ledger = BudgetLedger(local / "budget.sqlite3")
    rid = ledger.reserve(0.2, "synthetic", "one")
    ledger.settle(rid, {"prompt_tokens": 1000, "completion_tokens": 500})
    with closing(sqlite3.connect(local / "platform-v2.sqlite3")) as db:
        db.execute("CREATE TABLE sample(value TEXT)")
        db.execute("INSERT INTO sample VALUES('before')")
        db.commit()
    (local / "platform-assets").mkdir()
    (local / "platform-assets" / "public.txt").write_text("synthetic", encoding="utf-8")
    (local / "new-platform-handoff-20261017").mkdir()
    (local / "new-platform-handoff-20261017" / "private.txt").write_text("private", encoding="utf-8")
    return ledger


def test_backup_restore_preserves_newer_lifetime_charges(tmp_path):
    ledger = synthetic_runtime(tmp_path)
    backup = create_backup(tmp_path, services_stopped=True)
    manifest = verify_backup(backup)
    assert manifest["budget"]["spent_cny"] == 0.012
    assert not any("handoff" in entry["path"] for entry in manifest["files"])
    with closing(sqlite3.connect(tmp_path / ".local" / "platform-v2.sqlite3")) as db:
        db.execute("UPDATE sample SET value='after'")
        db.commit()
    rid = ledger.reserve(0.5, "later", "two")
    ledger.uncertain(rid)
    result = restore_backup(tmp_path, backup, services_stopped=True)
    assert result["budget"]["after"]["request_count"] == 2
    assert ledger.budget()["reserved_cny"] == 0.5
    with closing(sqlite3.connect(tmp_path / ".local" / "platform-v2.sqlite3")) as db:
        assert db.execute("SELECT value FROM sample").fetchone()[0] == "before"


def test_backup_tampering_and_nonquiescent_restore_rejected(tmp_path):
    synthetic_runtime(tmp_path)
    backup = create_backup(tmp_path)
    with pytest.raises(ValueError, match="跨库一致性"):
        restore_backup(tmp_path, backup, services_stopped=True)
    (backup / "platform-assets" / "public.txt").write_text("tampered", encoding="utf-8")
    with pytest.raises(ValueError, match="哈希"):
        verify_backup(backup)


def test_restore_preserves_newer_development_test_reservations(tmp_path):
    synthetic_runtime(tmp_path)
    development = BudgetLedger(tmp_path / ".local" / "development-test.sqlite3")
    development.reserve(0.2, "synthetic-development", "one", limit=10)
    backup = create_backup(tmp_path, services_stopped=True)
    development.reserve(0.3, "synthetic-development", "two", limit=10)
    restore_backup(tmp_path, backup, services_stopped=True)
    assert development.budget(10)["reserved_cny"] == 0.5


def test_backup_never_recurses_into_source_or_overwrites(tmp_path):
    synthetic_runtime(tmp_path)
    with pytest.raises(ValueError, match="重叠"):
        create_backup(tmp_path, tmp_path / ".local" / "platform-assets" / "bad")
    backup = create_backup(tmp_path)
    with pytest.raises(ValueError, match="不覆盖"):
        create_backup(tmp_path, backup)


def test_restore_rejects_conflicting_settled_amounts(tmp_path):
    ledger = synthetic_runtime(tmp_path)
    backup = create_backup(tmp_path, services_stopped=True)
    with closing(sqlite3.connect(ledger.path)) as db:
        db.execute("UPDATE charges SET cost=cost+0.01")
        db.commit()
    with pytest.raises(ValueError, match="预算记录冲突"):
        restore_backup(tmp_path, backup, services_stopped=True)
    assert ledger.budget()["spent_cny"] == 0.022


@pytest.mark.parametrize(
    "path",
    [
        ".local/runtime.json",
        "frontend/.env",
        "original.docx",
        "backend/private.sqlite3",
        "../outside.py",
    ],
)
def test_release_paths_exclude_runtime_private_material(path):
    with pytest.raises(ValueError):
        validate_public_path(path)


def test_release_package_marks_unverified_offline_and_has_hashes(tmp_path, monkeypatch):
    from scripts import offline_package

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "README.md").write_text("Public synthetic project", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=tmp_path, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Backup Test",
            "-c",
            "user.email=synthetic@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
        cwd=tmp_path,
        check=True,
    )
    monkeypatch.setattr(offline_package, "dependency_inventory", list)
    output = tmp_path / ".local" / "release" / "bundle.zip"
    result = create_package(tmp_path, output)
    assert result["offline_acceptance"] == "NOT_RUN"
    with zipfile.ZipFile(output) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["frontend_built"] is False
        assert manifest["redistribution_review"] == "REQUIRED"
        assert all(entry["sha256"] for entry in manifest["files"])
        assert not any("budget.sqlite3" in name for name in archive.namelist())
