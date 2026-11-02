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
    false,
    func,
    inspect,
    literal_column,
    select,
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
    """Explicit single-process deployment step, under a dedicated application user.

    DM DDL can commit implicitly. Never race API/Worker DDL or adopt a partially
    initialized/unrelated schema. An interrupted first bootstrap needs DBA inspection.
    """
    from .db_schema import metadata, schema

    if engine.dialect.name != "dm":
        raise ValueError("只允许初始化达梦schema")
    with engine.begin() as connection:
        existing = {name.lower() for name in inspect(connection).get_table_names()}
        if existing:
            expected = set(metadata.tables) | set(identity_metadata.tables)
            # The supervised question portal adds one isolated table to an existing v1 installation.
            # This is the only recognized incremental schema. Never guess how to fix a partial deployment.
            if existing == expected - {"questions"}:
                assert_schema(connection, before_questions=True)
                metadata.tables["questions"].create(connection)
                assert_schema(connection)
                return
            if existing != expected:
                raise RuntimeError("达梦schema必须为空或为完整平台schema；拒绝自动接管或修复")
            assert_schema(connection)
            return
        metadata.create_all(connection)
        identity_metadata.create_all(connection)
        connection.execute(identity_guard.insert().values(id=1))
        connection.execute(schema.insert().values(version=1))


def assert_schema(connection, *, before_questions=False):
    from .db_schema import metadata, schema

    inspector = inspect(connection)
    expected = set(metadata.tables) | set(identity_metadata.tables)
    if before_questions:
        expected.remove("questions")
    if {name.lower() for name in inspector.get_table_names()} != expected:
        raise RuntimeError("达梦平台业务表不完整；请使用受控 init 升级现有 schema")
    if not before_questions:
        # Verify the application columns as their owner. DM column reflection may require SYS privileges.
        connection.execute(select(metadata.tables["questions"]).where(false()))

    if connection.execute(select(schema.c.version)).scalars().all() != [1]:
        raise RuntimeError("达梦平台schema版本不匹配")
    if connection.execute(select(identity_guard.c.id)).scalars().all() != [1]:
        raise RuntimeError("达梦身份写入锁未正确初始化")


def ensure_initialized(engine):
    with engine.connect() as connection:
        assert_schema(connection)
