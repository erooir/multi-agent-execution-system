"""Relational, transactional domain repository with fenced durable task leases."""

from __future__ import annotations

import copy
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Float,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    and_,
    create_engine,
    or_,
    select,
)
from sqlalchemy import event as sa_event

ROOT = Path(__file__).resolve().parents[3]
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
TERMINAL = {"succeeded", "partial", "failed", "cancelled"}
metadata = MetaData()
tables = {}
for kind in KINDS:
    columns = [
        Column("id", String(100), primary_key=kind != "events"),
        Column("project_id", String(100), index=True),
        Column("status", String(40), index=True),
        Column("created_at", String(40)),
        Column("updated_at", String(40)),
        Column("payload", JSON, nullable=False),
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
            Column("lease_until", Float, nullable=False, default=0),
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


def now():
    return datetime.now(UTC).isoformat()


class LeaseLost(RuntimeError):
    pass


class ConcurrentWrite(ValueError):
    """A caller attempted to replace a row changed since it was read."""


class Repository:
    def __init__(self, url=None, *, engine=None, connection=None):
        self.connection = connection
        if engine is not None:
            self.engine = engine
            return
        default = ROOT / ".local" / "platform-v2.sqlite3"
        default.parent.mkdir(parents=True, exist_ok=True)
        url = url or os.environ.get("HANGZHI_DATABASE_URL", f"sqlite:///{default.as_posix()}")
        self.engine = create_engine(
            url,
            connect_args={"check_same_thread": False, "timeout": 30} if url.startswith("sqlite") else {},
            pool_pre_ping=True,
        )
        if self.engine.dialect.name == "sqlite":

            @sa_event.listens_for(self.engine, "connect")
            def configure(connection, record):
                connection.execute("PRAGMA busy_timeout=30000")
                connection.execute("PRAGMA foreign_keys=ON")
                deadline = time.monotonic() + 30
                while True:
                    try:
                        if connection.execute("PRAGMA journal_mode").fetchone()[0] != "wal":
                            connection.execute("PRAGMA journal_mode=WAL")
                        break
                    except sqlite3.OperationalError as exc:
                        if (
                            getattr(exc, "sqlite_errorcode", None)
                            not in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}
                            or time.monotonic() >= deadline
                        ):
                            raise
                        # Changing journal mode does not honor busy_timeout on
                        # every SQLite build; retry only this initialization lock.
                        time.sleep(0.05)

        # API and workers may cold-start together. Serialize both DDL and the
        # schema marker across processes, using the same connection/transaction.
        with self.engine.connect() as conn:
            if self.engine.dialect.name == "sqlite":
                conn.exec_driver_sql("BEGIN IMMEDIATE")
            else:
                conn.begin()
                if self.engine.dialect.name == "postgresql":
                    conn.exec_driver_sql("SELECT pg_advisory_xact_lock(2036161017)")
            try:
                metadata.create_all(conn)
                versions = conn.execute(select(schema.c.version)).scalars().all()
                if versions and versions != [1]:
                    raise RuntimeError("Unsupported platform schema; run an explicit migration")
                if not versions:
                    conn.execute(schema.insert().values(version=1))
                conn.commit()
            except BaseException:
                conn.rollback()
                raise

    @contextmanager
    def transaction(self):
        if self.connection is not None:
            yield self
            return
        with self.engine.connect() as conn:
            if self.engine.dialect.name == "sqlite":
                conn.exec_driver_sql("BEGIN IMMEDIATE")
            else:
                conn.begin()
            try:
                yield Repository(engine=self.engine, connection=conn)
                conn.commit()
            except BaseException:
                conn.rollback()
                raise

    @staticmethod
    def _record(row):
        if row is None:
            return None
        values = dict(row._mapping)
        record = copy.deepcopy(values.pop("payload"))
        record.update(values)
        return record

    def get(self, kind, record_id, *, for_update=False):
        if self.connection is None:
            if for_update:
                with self.transaction() as tx:
                    return tx.get(kind, record_id, for_update=True)
            with self.engine.connect() as conn:
                return Repository(engine=self.engine, connection=conn).get(kind, record_id)
        table = tables[kind]
        query = select(table).where(table.c.id == record_id)
        if for_update and self.engine.dialect.name == "postgresql":
            query = query.with_for_update()
        return self._record(self.connection.execute(query).first())

    def list(self, kind, project_id=None, **filters):
        if self.connection is None:
            with self.engine.connect() as conn:
                return Repository(engine=self.engine, connection=conn).list(kind, project_id, **filters)
        table = tables[kind]
        query = select(table)
        if project_id is not None:
            query = query.where(table.c.project_id == project_id)
        for key, value in filters.items():
            if key in table.c:
                query = query.where(table.c[key] == value)
        query = query.order_by(table.c.sequence if kind == "events" else table.c.created_at)
        rows = [self._record(row) for row in self.connection.execute(query)]
        return [row for row in rows if all(row.get(k) == v for k, v in filters.items())]

    def put(self, kind, record):
        if self.connection is None:
            with self.transaction() as tx:
                return tx.put(kind, record)
        table = tables[kind]
        record = copy.deepcopy(record)
        record.setdefault("id", uuid.uuid4().hex)
        previous = self.get(kind, record["id"], for_update=True)
        if (
            previous
            and record.get("updated_at") is not None
            and record["updated_at"] != previous["updated_at"]
        ):
            raise ConcurrentWrite("记录已被并发修改，请重新读取后重试")
        if (
            kind.endswith("_versions")
            and previous
            and {k: v for k, v in previous.items() if k != "updated_at"}
            != {k: v for k, v in record.items() if k != "updated_at"}
        ):
            raise ValueError("Published versions are immutable")
        record.setdefault("created_at", now())
        record["updated_at"] = now()
        if kind == "tasks":
            for key, default in [("lease_token", 0), ("lease_until", 0), ("cancel_requested", False)]:
                record.setdefault(key, default)
        values = {
            column.name: record.get(column.name)
            for column in table.c
            if column.name not in ("payload", "sequence")
        }
        values["payload"] = record
        if previous:
            self.connection.execute(table.update().where(table.c.id == record["id"]).values(**values))
        else:
            self.connection.execute(table.insert().values(**values))
        return self.get(kind, record["id"])

    def delete(self, kind, record_id):
        if self.connection is None:
            with self.transaction() as tx:
                return tx.delete(kind, record_id)
        if kind.endswith("_versions"):
            raise ValueError("Published history cannot be deleted")
        return (
            self.connection.execute(tables[kind].delete().where(tables[kind].c.id == record_id)).rowcount > 0
        )

    def event(self, task_id, event_type, payload=None, **refs):
        if self.connection is None:
            with self.transaction() as tx:
                return tx.event(task_id, event_type, payload, **refs)
        task = self.get("tasks", task_id)
        return self.put(
            "events",
            {
                "task_id": task_id,
                "project_id": task["project_id"],
                "type": event_type,
                "time": now(),
                "trace_id": task.get("trace_id", task_id),
                "payload": payload or {},
                **refs,
            },
        )

    def claim(self, worker_id, lease_seconds=30):
        with self.transaction() as tx:
            table = tables["tasks"]
            query = select(table).where(
                and_(
                    ~table.c.status.in_(TERMINAL),
                    or_(table.c.lease_owner.is_(None), table.c.lease_until < time.time()),
                )
            )
            query = query.order_by(table.c.created_at).limit(1)
            if self.engine.dialect.name == "postgresql":
                query = query.with_for_update(skip_locked=True)
            task = self._record(tx.connection.execute(query).first())
            if not task:
                return None
            recovered = bool(task.get("lease_token", 0))
            task.update(
                lease_owner=worker_id,
                lease_token=task.get("lease_token", 0) + 1,
                lease_until=time.time() + lease_seconds,
            )
            task = tx.put("tasks", task)
            tx.event(
                task["id"],
                "lease.recovered" if recovered else "lease.claimed",
                {"worker_id": worker_id, "token": task["lease_token"]},
            )
            return task

    def fenced(self, task_id, worker_id, token):
        if self.connection is None:
            with self.transaction() as tx:
                return tx.fenced(task_id, worker_id, token)
        query = select(tables["tasks"]).where(tables["tasks"].c.id == task_id)
        if self.engine.dialect.name == "postgresql":
            query = query.with_for_update()
        task = self._record(self.connection.execute(query).first())
        if (
            not task
            or task.get("lease_owner") != worker_id
            or task.get("lease_token") != token
            or task.get("lease_until", 0) <= time.time()
        ):
            raise LeaseLost("Task lease expired or was superseded")
        return task

    def heartbeat(self, task_id, worker_id, token, lease_seconds=30):
        with self.transaction() as tx:
            task = tx.fenced(task_id, worker_id, token)
            task["lease_until"] = time.time() + lease_seconds
            return tx.put("tasks", task)

    def finish_claim(self, task_id, worker_id, token):
        with self.transaction() as tx:
            task = tx.fenced(task_id, worker_id, token)
            task.update(lease_owner=None, lease_until=0)
            return tx.put("tasks", task)


repo = Repository()
