"""Local SQLite backup/restore. Runtime material never belongs in source packages."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import sqlite3
import uuid
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

DATABASES = ("budget.sqlite3", "workbench.sqlite3", "platform-v2.sqlite3")
CONTENT_DIRECTORY = "platform-assets"


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def read_database(path: Path):
    return sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)


def copy_database(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise ValueError("备份目标已存在，不覆盖")
    with closing(read_database(source)) as src, closing(sqlite3.connect(target)) as dst:
        if src.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("源数据库完整性检查失败")
        src.backup(dst)
        if dst.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("备份数据库完整性检查失败")


def budget_summary(path: Path) -> dict:
    with closing(read_database(path)) as db:
        row = db.execute(
            "SELECT COUNT(*),COALESCE(SUM(CASE WHEN state='settled' THEN cost ELSE 0 END),0),"
            "COALESCE(SUM(CASE WHEN state!='settled' THEN reserved ELSE 0 END),0) FROM charges"
        ).fetchone()
    return {"request_count": row[0], "spent_cny": round(row[1], 6), "reserved_cny": round(row[2], 6)}


def _local_path(root: Path, path: Path) -> Path:
    path = path.resolve()
    if not path.is_relative_to((root / ".local").resolve()):
        raise ValueError("运行数据操作必须留在当前项目 .local 内")
    return path


def create_backup(root: Path, destination: Path | None = None, *, services_stopped: bool = False) -> Path:
    root = root.resolve()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    destination = _local_path(
        root, destination or root / ".local" / "backups" / f"snapshot-{stamp}-{uuid.uuid4().hex[:6]}"
    )
    if not destination.is_relative_to((root / ".local" / "backups").resolve()):
        raise ValueError("备份目录必须位于 .local/backups 内，不能与源文件重叠")
    if destination.exists():
        raise ValueError("备份目录已存在，不覆盖")
    local = root / ".local"
    if not (local / "budget.sqlite3").is_file():
        raise ValueError("历史预算账本缺失，禁止生成看似完整的备份")
    destination.mkdir(parents=True)
    files = []
    for name in DATABASES:
        source = local / name
        if source.exists():
            if source.is_symlink():
                raise ValueError("数据库路径不能是符号链接")
            target = destination / name
            copy_database(source, target)
            files.append({"path": name, "bytes": target.stat().st_size, "sha256": digest(target)})
    assets = local / CONTENT_DIRECTORY
    if assets.exists():
        for source in sorted(assets.rglob("*")):
            if source.is_symlink() or not source.resolve().is_relative_to(assets.resolve()):
                raise ValueError("受控文件目录含符号链接，拒绝跨目录备份")
            if source.is_file():
                relative = source.relative_to(local)
                target = destination / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
                files.append(
                    {"path": relative.as_posix(), "bytes": target.stat().st_size, "sha256": digest(target)}
                )
    manifest = {
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "kind": "private-runtime-backup",
        "cross_database_consistent": services_stopped,
        "consistency_note": "SQLite backup API; 跨库一致性要求调用者已停止 API 与所有 Worker。",
        "budget": budget_summary(destination / "budget.sqlite3"),
        "files": files,
    }
    (destination / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    verify_backup(destination)
    return destination


def verify_backup(directory: Path) -> dict:
    directory = directory.resolve()
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1 or manifest.get("kind") != "private-runtime-backup":
        raise ValueError("不支持的备份格式")
    seen = set()
    for entry in manifest["files"]:
        candidate = directory / entry["path"]
        path = candidate.resolve()
        relative = path.relative_to(directory).as_posix() if path.is_relative_to(directory) else ""
        if not relative or relative in seen or candidate.is_symlink():
            raise ValueError("备份文件路径不合法")
        if relative not in DATABASES and not relative.startswith(CONTENT_DIRECTORY + "/"):
            raise ValueError("备份包含未授权运行文件")
        seen.add(relative)
        if path.stat().st_size != entry["bytes"] or digest(path) != entry["sha256"]:
            raise ValueError("备份文件哈希不一致")
        if relative in DATABASES:
            with closing(read_database(path)) as db:
                if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise ValueError("备份数据库损坏")
    if "budget.sqlite3" not in seen or budget_summary(directory / "budget.sqlite3") != manifest["budget"]:
        raise ValueError("备份预算账本缺失或摘要不一致")
    return manifest


def merge_budget_history(source: Path, current: Path) -> dict:
    """Append missing historical charges; never overwrite a current charge or lower its occupancy."""
    before = budget_summary(current)
    with closing(read_database(source)) as src, closing(sqlite3.connect(current)) as dst:
        src.row_factory = sqlite3.Row
        rows = src.execute("SELECT * FROM charges").fetchall()
        dst.execute("BEGIN IMMEDIATE")
        try:
            for row in rows:
                if (
                    row["state"] not in {"settled", "reserved", "uncertain"}
                    or not isinstance(row["reserved"], (int, float))
                    or not math.isfinite(row["reserved"])
                    or row["reserved"] <= 0
                    or not isinstance(row["cost"], (int, float))
                    or not math.isfinite(row["cost"])
                    or row["cost"] < 0
                ):
                    raise ValueError("备份费用记录无效")
                current_row = dst.execute(
                    "SELECT state,reserved,cost FROM charges WHERE id=?", (row["id"],)
                ).fetchone()
                if current_row:
                    state, reserved, cost = current_row
                    occupied = cost if state == "settled" else reserved
                    if (
                        reserved != row["reserved"]
                        or (state == "settled" and row["state"] == "settled" and cost != row["cost"])
                        or (row["state"] == "settled" and row["cost"] > occupied)
                    ):
                        raise ValueError("同一请求的预算记录冲突；必须核对完整历史，不能选择较低金额继续恢复")
                dst.execute(
                    "INSERT OR IGNORE INTO charges(id,created_at,purpose,run_id,state,reserved,cost,input_tokens,output_tokens,model) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?)",
                    tuple(
                        row[key]
                        for key in (
                            "id",
                            "created_at",
                            "purpose",
                            "run_id",
                            "state",
                            "reserved",
                            "cost",
                            "input_tokens",
                            "output_tokens",
                            "model",
                        )
                    ),
                )
            for name, value in src.execute("SELECT name,value FROM counters"):
                dst.execute(
                    "INSERT INTO counters(name,value) VALUES(?,?) ON CONFLICT(name) DO UPDATE SET value=MAX(value,excluded.value)",
                    (name, value),
                )
            dst.execute(
                "CREATE TABLE IF NOT EXISTS task_limits(run_id TEXT PRIMARY KEY,limit_cny REAL NOT NULL)"
            )
            if src.execute("SELECT 1 FROM sqlite_master WHERE name='task_limits'").fetchone():
                for run_id, limit in src.execute("SELECT run_id,limit_cny FROM task_limits"):
                    dst.execute(
                        "INSERT INTO task_limits(run_id,limit_cny) VALUES(?,?) ON CONFLICT(run_id) DO UPDATE SET limit_cny=MIN(limit_cny,excluded.limit_cny)",
                        (run_id, limit),
                    )
            dst.commit()
        except BaseException:
            dst.rollback()
            raise
    after = budget_summary(current)
    return {"before": before, "after": after, "rule": "保留当前记录，只追加缺失的历史记录；未决预留不释放"}


def restore_backup(
    root: Path,
    directory: Path,
    *,
    services_stopped: bool = False,
    restore_missing_ledger: bool = False,
) -> dict:
    if not services_stopped:
        raise ValueError("恢复前必须停止 API 与所有 Worker，并指定 --services-stopped")
    root = root.resolve()
    directory = _local_path(root, directory)
    manifest = verify_backup(directory)
    if not manifest["cross_database_consistent"]:
        raise ValueError("备份未声明停机跨库一致性，只能单库诊断，不能整套恢复")
    local = root / ".local"
    current_budget = local / "budget.sqlite3"
    for entry in manifest["files"]:
        name = entry["path"]
        target = _local_path(root, local / name)
        if (
            name in DATABASES
            and name != "budget.sqlite3"
            and any(Path(str(target) + suffix).exists() for suffix in ("-wal", "-shm"))
        ):
            raise ValueError("目标数据库仍有 WAL/SHM；确认服务已停止并执行完整检查点后再恢复")
    before_backup = None
    if current_budget.exists():
        before_backup = create_backup(root, services_stopped=True)
        budget_result = merge_budget_history(directory / "budget.sqlite3", current_budget)
    elif restore_missing_ledger:
        copy_database(directory / "budget.sqlite3", current_budget)
        budget_result = {"restored_from_verified_snapshot": budget_summary(current_budget)}
    else:
        raise ValueError("当前历史账本缺失；核实最新备份后显式指定 --restore-missing-ledger")
    for entry in manifest["files"]:
        name = entry["path"]
        if name == "budget.sqlite3":
            continue
        target = _local_path(root, local / name)
        target.parent.mkdir(parents=True, exist_ok=True)
        staged = target.with_name(target.name + ".restore-" + uuid.uuid4().hex)
        shutil.copy2(directory / name, staged)
        staged.replace(target)
    result = {
        "restored_at": datetime.now(UTC).isoformat(),
        "source": str(directory),
        "pre_restore_backup": str(before_backup) if before_backup else None,
        "budget": budget_result,
    }
    report = local / "backups" / f"restore-{uuid.uuid4().hex}.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("backup", "verify", "restore"))
    parser.add_argument("--path", type=Path)
    parser.add_argument("--services-stopped", action="store_true")
    parser.add_argument("--restore-missing-ledger", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    if args.action != "verify" and os.environ.get("HANGZHI_DATABASE_URL", "").startswith("postgres"):
        parser.error("此脚本仅处理 SQLite 开发运行态。PostgreSQL 请使用 docs/OPERATIONS.md 的协调备份流程")
    if args.action == "backup":
        result = {"backup": str(create_backup(root, args.path, services_stopped=args.services_stopped))}
    elif not args.path:
        parser.error("verify/restore 需要 --path")
    elif args.action == "verify":
        result = verify_backup(args.path)
    else:
        result = restore_backup(
            root,
            args.path,
            services_stopped=args.services_stopped,
            restore_missing_ledger=args.restore_missing_ledger,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
