"""Storage metadata; importing definitions never opens a database."""

from __future__ import annotations

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Double,
    Float,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
)

from .dameng import DMDocument

KINDS = [
    "projects",
    "agents",
    "agent_versions",
    "workflows",
    "workflow_versions",
    "data_assets",
    "tasks",
    "nodes",
    "instances",
    "artifacts",
    "events",
    "schedules",
    "evaluations",
    "improvements",
    "memories",
    "connectors",
    "workers",
    "audits",
]
metadata = MetaData()
tables = {}
for kind in KINDS:
    columns = [
        Column("id", String(100), primary_key=kind != "events"),
        Column("project_id", String(100), index=True),
        Column("status", String(40), index=True),
        Column("created_at", String(40)),
        Column("updated_at", String(40)),
        Column("payload", JSON().with_variant(DMDocument(), "dm"), nullable=False),
    ]
    if kind == "events":
        columns += [
            Column("sequence", Integer, primary_key=True, autoincrement=True),
            Column("task_id", String(100), index=True),
            UniqueConstraint("id"),
        ]
    if kind == "tasks":
        columns += [
            Column("idempotency_key", String(160)),
            Column("lease_owner", String(100)),
            Column("lease_token", Integer, nullable=False, default=0),
            Column("lease_until", Float().with_variant(Double(), "dm"), nullable=False, default=0),
            Column("cancel_requested", Boolean, nullable=False, default=False),
            UniqueConstraint("project_id", "idempotency_key"),
        ]
    if kind in ("agent_versions", "workflow_versions"):
        columns += [
            Column("definition_id", String(100)),
            Column("version", Integer),
            UniqueConstraint("definition_id", "version"),
        ]
    tables[kind] = Table(kind, metadata, *columns)
schema = Table("schema_version", metadata, Column("version", Integer, primary_key=True))
