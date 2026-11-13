"""DM8 storage boundary. Importing this module never opens the identity DB or the lifetime ledger."""

from __future__ import annotations

import json
import os
import re

from sqlalchemy import (
    CLOB,
    Column,
    Integer,
    MetaData,
    String,
    Table,
    func,
    literal_column,
)
from sqlalchemy.engine import make_url
from sqlalchemy.types import TypeDecorator


class DMDocument(TypeDecorator):
    """Use a real CLOB, including for documents much larger than VARCHAR/page limits."""

    impl = CLOB
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return None if value is None else json.dumps(value, ensure_ascii=False, allow_nan=False)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if hasattr(value, "read"):
            value = value.read()
        return json.loads(value)


identity_metadata = MetaData()
identities = Table(
    "hz_identity_records",
    identity_metadata,
    Column("kind", String(64), primary_key=True),
    Column("id", String(160), primary_key=True),
    Column("data", DMDocument, nullable=False),
    Column("updated_at", String(40), nullable=False),
)
identity_guard = Table("hz_identity_guard", identity_metadata, Column("id", Integer, primary_key=True))


def database_url(value, legacy_path):
    url = make_url(value or os.environ.get("HANGZHI_DATABASE_URL") or f"sqlite:///{legacy_path.as_posix()}")
    if os.environ.get("HANGZHI_REQUIRE_DM", "").lower() == "true" and url.get_backend_name() != "dm":
        raise RuntimeError("正式部署必须配置达梦数据库，禁止回退其他业务数据库")
    if url.get_backend_name() == "dm":
        if url.drivername not in {"dm", "dm+dmPython"}:
            raise ValueError("DM8仅支持官方dmPython同步驱动")
        if (
            not url.host
            or not url.username
            or not url.database
            or url.username.upper() != url.database.upper()
        ):
            raise ValueError("DM8需要独立账号，并显式指定同名应用schema")
        if url.username.upper() in {"SYSDBA", "SYS", "SYSAUDITOR", "SYSSSO", "SYSTEM"}:
            raise ValueError("应用不得使用系统管理员账号")
        # dmPython otherwise defaults to the Windows locale (often GBK) even
        # for a database initialized with CHARSET=1. This affects CLOB, bound
        # VARCHARs and JSON_VALUE results, not just JSON serialization.
        if url.query.get("local_code", "1") != "1":
            raise ValueError("达梦客户端必须使用 UTF-8 (dmPython.PG_UTF8/local_code=1)")
        url = url.update_query_dict({"local_code": "1"})
    return url


def supports_row_lock(engine):
    return engine.dialect.name in {"dm", "postgresql"}


def json_scalar(engine, column, *keys):
    if engine.dialect.name == "dm":
        # DM JSON_VALUE returns scalars, so compile ONE complete path, not
        # JSON_VALUE(JSON_VALUE(...), ...). Only code-owned field names may enter SQL.
        if not keys or any(not re.fullmatch(r"[a-z_][a-z0-9_]*", k) for k in keys):
            raise ValueError("Invalid JSON path")
        return func.json_value(column, literal_column("'$." + ".".join(keys) + "'"), type_=String())
    for key in keys:
        column = column[key]
    return column.as_string()


def prepare_schema(engine):
    """Compatibility entry point for the single explicit, additive migration command."""
    from scripts.dm_database import prepare_schema as migrate

    return migrate(engine)


def assert_schema(connection, *, before_questions=False):
    from .db import assert_dm_schema

    if before_questions:
        raise RuntimeError("Legacy pre-question schema requires the explicit migration command")
    return assert_dm_schema(connection)


def ensure_initialized(engine):
    with engine.connect() as connection:
        assert_schema(connection)
