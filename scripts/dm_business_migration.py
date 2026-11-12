"""Fail-closed one-time transfer of a stopped v1/v2 business SQLite database into empty DM8 tables."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from sqlalchemy import func, select

from backend.app.platform.dameng import identity_guard
from backend.app.platform.db import assert_dm_schema
from backend.app.platform.db_schema import AUTONOMY_TABLES, CAPABILITY_TABLES, SCHEMA_VERSION, tables


def _check_asset(payload, assets_dir):
    ref = Path(payload["content_ref"])
    if ref.is_symlink() or not ref.is_file() or not ref.resolve().is_relative_to(assets_dir.resolve()):
        raise ValueError("媒体原件缺失或超出受控资产目录；不能保留无效快照")
    with ref.open("rb") as handle:
        if hashlib.file_digest(handle, "sha256").hexdigest() != payload.get("checksum"):
            raise ValueError("媒体原件校验失败")


def import_business(engine, source: Path, assets_dir: Path):
    if engine.dialect.name != "dm" or not source.is_file() or source.is_symlink():
        raise ValueError("需要有效的只读SQLite业务库与达梦目标")
    with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as old:
        old.row_factory = sqlite3.Row
        old.execute("PRAGMA query_only=ON")
        old.execute("BEGIN")
        if [r[0] for r in old.execute("PRAGMA integrity_check")] != ["ok"]:
            raise ValueError("SQLite业务源完整性检查失败")
        names = {
            r[0]
            for r in old.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        expected = {*tables, "schema_version"}
        v2 = expected - AUTONOMY_TABLES
        legacy = v2 - CAPABILITY_TABLES
        versions = [r[0] for r in old.execute("SELECT version FROM schema_version")]
        supported = {
            1: (legacy, legacy - {"questions"}, v2, v2 - {"questions"}),
            2: (v2,),
            SCHEMA_VERSION: (expected,),
        }
        if len(versions) != 1 or names not in supported.get(versions[0], ()):
            raise ValueError("不支持的源表或schema版本；拒绝迁移身份库/账本或遗漏数据")
        if old.execute(
            "SELECT 1 FROM tasks WHERE status IS NULL OR status NOT IN ('succeeded','partial','failed','cancelled') LIMIT 1"
        ).fetchone():
            raise ValueError("源库仍有非终态任务，须先收束、停服并备份")
        counts = {}
        with engine.connect() as target:
            assert_dm_schema(target)
            target.commit()
            # Session setting, outside the data transaction. The application schema
            # remains intact on rollback; historical SSE cursors must not be renumbered.
            target.exec_driver_sql("SET IDENTITY_INSERT events ON")
            target.commit()
            try:
                with target.begin():
                    target.execute(
                        select(identity_guard.c.id).where(identity_guard.c.id == 1).with_for_update()
                    ).scalar_one()
                    for table in tables.values():
                        if target.execute(select(table.c.id).limit(1)).first():
                            raise ValueError("达梦业务目标表非空，拒绝覆盖或合并")
                    for name, table in tables.items():
                        if name not in names:
                            counts[name] = 0
                            continue
                        columns = list(table.c.keys())
                        source_cols = {r[1] for r in old.execute(f'PRAGMA table_info("{name}")')}
                        if set(columns) != source_cols:
                            raise ValueError(f"业务表 {name} 的列与迁移契约不符")
                        col_sql = ",".join(f'"{key}"' for key in columns)
                        order = '"sequence"' if name == "events" else '"id"'
                        data = old.execute(f'SELECT {col_sql} FROM "{name}" ORDER BY {order}')
                        count = 0
                        while batch := data.fetchmany(32):
                            expected = {}
                            for row in batch:
                                item = dict(row)
                                item["payload"] = json.loads(item["payload"])
                                if name == "tasks":
                                    item["cancel_requested"] = bool(item["cancel_requested"])
                                if name == "data_assets" and item["payload"].get("content_ref"):
                                    _check_asset(item["payload"], assets_dir)
                                expected[item["id"]] = item
                                target.execute(table.insert().values(**item))
                            actual = {
                                r._mapping["id"]: dict(r._mapping)
                                for r in target.execute(select(table).where(table.c.id.in_(expected)))
                            }
                            if actual != expected:
                                raise ValueError(f"业务表 {name} 全字段回读校验失败，已取消迁移")
                            count += len(batch)
                        if target.execute(select(func.count()).select_from(table)).scalar_one() != count:
                            raise ValueError(f"业务表 {name} 数量校验失败")
                        counts[name] = count
            finally:
                target.exec_driver_sql("SET IDENTITY_INSERT events OFF")
                target.commit()
    return counts
