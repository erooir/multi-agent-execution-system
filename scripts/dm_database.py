"""Prepare/verify a dedicated DM8 schema; never accept credentials on the command line."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path

from sqlalchemy import create_engine, text

from backend.app.identity_records import DMIdentityStore
from backend.app.platform.dameng import assert_schema, database_url, prepare_schema


def import_identity(engine, source: Path):
    """Private one-time transfer. Preserve hashes/settings/audits, never restore old session tokens."""
    if not source.is_file() or source.is_symlink():
        raise ValueError("身份源必须是现有的私有SQLite文件")
    copied = {}
    with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        records = db.execute(
            "SELECT kind,id,data,updated_at FROM records WHERE kind IN ('users','settings','auth_meta','audits')"
        ).fetchall()
    if not any(row[0] == "users" for row in records):
        raise ValueError("源身份库无账号，拒绝迁移")
    with DMIdentityStore(engine, source.parent).transaction() as target:
        for kind in ("users", "settings", "auth_meta", "audits", "sessions", "auth_limits"):
            if target.any(kind):
                raise ValueError("只允许迁入尚未启用且为空的身份库")
        for kind, key, raw, updated in records:
            item = json.loads(raw)
            if item.get("id") != key or item.get("updated_at") != updated:
                raise ValueError("源身份记录内容与索引不一致")
            target.insert(kind, item)
            if target.get(kind, key) != item:
                raise ValueError("迁移内容回读校验失败")
            copied[kind] = copied.get(kind, 0) + 1
    return copied


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["init", "check", "import-identity", "import-business"])
    parser.add_argument("--identity-source", type=Path)
    parser.add_argument("--business-source", type=Path)
    parser.add_argument(
        "--assets-dir", type=Path, default=Path(__file__).resolve().parents[1] / ".local/platform-assets"
    )
    parser.add_argument("--services-stopped", action="store_true")
    args = parser.parse_args()
    uri = os.environ.get("HANGZHI_DATABASE_URL")
    if not uri:
        raise SystemExit("请在私有环境设置 HANGZHI_DATABASE_URL（不接受命令行口令）")
    url = database_url(uri, Path("."))
    if url.get_backend_name() != "dm":
        raise SystemExit("此命令仅允许连接达梦")
    engine = create_engine(url, pool_pre_ping=True, hide_parameters=True)
    try:
        if args.action == "init":
            prepare_schema(engine)
        elif args.action == "import-identity":
            if not args.services_stopped or not args.identity_source:
                raise SystemExit("迁移身份前须停服备份，并提供 --services-stopped --identity-source")
            print(json.dumps({"identity_counts": import_identity(engine, args.identity_source)}))
        elif args.action == "import-business":
            from .dm_business_migration import import_business

            if not args.services_stopped or not args.business_source:
                raise SystemExit("迁移业务前须停服备份，并提供 --services-stopped --business-source")
            print(
                json.dumps(
                    {"business_counts": import_business(engine, args.business_source, args.assets_dir)}
                )
            )
        with engine.connect() as connection:
            assert_schema(connection)
            banners = connection.execute(text("SELECT BANNER FROM V$VERSION")).scalars().all()
            print(json.dumps({"schema_version": 1, "dialect": "dm", "server": banners}, ensure_ascii=True))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
