"""Relational, transactional domain repository with fenced durable task leases."""

from __future__ import annotations

import copy
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import (
    and_,
    case,
    create_engine,
    false,
    func,
    inspect,
    or_,
    select,
    true,
)
from sqlalchemy import event as sa_event

from .dameng import database_url, json_scalar, supports_row_lock
from .db_schema import SCHEMA_VERSION, metadata, schema, tables

ROOT = Path(__file__).resolve().parents[3]
TERMINAL = {"succeeded", "partial", "failed", "cancelled"}
QUEUE_AGING_SECONDS = 60
QUEUE_POLICY_VERSION = "interactive-aging-v1"
# String identity/reference fields used by detail, lineage and monitoring
# queries. Other types (including nested JSON) retain the original Python
# comparison after the project/physical-column filters.
_REFERENCE_FILTERS = frozenset(
    {"task_id", "agent_id", "node_id", "instance_id", "workflow_id", "definition_id", "owner_id"}
)


def assert_dm_schema(connection):
    from .dameng import identity_guard, identity_metadata

    expected = set(metadata.tables) | set(identity_metadata.tables)
    if {name.lower() for name in inspect(connection).get_table_names()} != expected:
        raise RuntimeError("达梦平台业务表不完整；请使用受控 init 升级现有 schema")
    for name in ("questions", "capability_definitions", "capability_versions"):
        connection.execute(select(metadata.tables[name]).where(false()))
    if connection.execute(select(schema.c.version)).scalars().all() != [SCHEMA_VERSION]:
        raise RuntimeError("达梦平台schema版本不匹配")
    if connection.execute(select(identity_guard.c.id)).scalars().all() != [1]:
        raise RuntimeError("达梦身份写入锁未正确初始化")


def ensure_initialized(engine):
    with engine.connect() as connection:
        assert_dm_schema(connection)


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
        url = database_url(url, default)
        dialect = url.get_backend_name()
        self.engine = create_engine(
            url,
            connect_args={"check_same_thread": False, "timeout": 30} if dialect == "sqlite" else {},
            hide_parameters=True,
            pool_pre_ping=True,
        )
        if dialect == "dm":
            ensure_initialized(self.engine)
            return
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
                if versions and versions not in ([1], [2], [SCHEMA_VERSION]):
                    raise RuntimeError("Unsupported platform schema; run an explicit migration")
                if versions in ([1], [2]):
                    conn.execute(schema.update().values(version=SCHEMA_VERSION))
                elif not versions:
                    conn.execute(schema.insert().values(version=SCHEMA_VERSION))
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

    def lock_project_initialization(self):
        """Serialize first-project creation across API processes, including an empty projects table."""
        if self.connection is None:
            raise RuntimeError("Project initialization requires a transaction")
        # SQLite transaction() already holds BEGIN IMMEDIATE. DM/PostgreSQL
        # lock the existing singleton marker; locking a missing project cannot
        # protect the initial check. The marker itself is never modified here.
        query = select(schema.c.version).where(schema.c.version == SCHEMA_VERSION)
        if supports_row_lock(self.engine):
            query = query.with_for_update()
        self.connection.execute(query).scalar_one()

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
        if for_update and supports_row_lock(self.engine):
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
            elif key in _REFERENCE_FILTERS and isinstance(value, str) and value:
                query = query.where(json_scalar(self.engine, table.c.payload, key) == value)
        query = query.order_by(table.c.sequence if kind == "events" else table.c.created_at)
        rows = [self._record(row) for row in self.connection.execute(query)]
        return [row for row in rows if all(row.get(k) == v for k, v in filters.items())]

    def operational_tasks(self, project_ids):
        """Read only scheduling/timing columns; do not materialize source snapshots."""
        if not project_ids:
            return []
        if self.connection is None:
            with self.engine.connect() as conn:
                return Repository(engine=self.engine, connection=conn).operational_tasks(project_ids)
        table = tables["tasks"]
        query = select(
            *[
                table.c[key]
                for key in (
                    "id",
                    "project_id",
                    "status",
                    "created_at",
                    "updated_at",
                    "lease_owner",
                    "lease_token",
                    "lease_until",
                )
            ],
            json_scalar(self.engine, table.c.payload, "finished_at").label("finished_at"),
            json_scalar(self.engine, table.c.payload, "reason_code").label("reason_code"),
            json_scalar(self.engine, table.c.payload, "request", "mode").label("mode"),
            json_scalar(self.engine, table.c.payload, "snapshot", "scheduling", "service_class").label(
                "service_class"
            ),
        ).where(table.c.project_id.in_(project_ids))
        return [dict(row._mapping) for row in self.connection.execute(query)]

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
            claimed_at = datetime.now(UTC)
            current_time = claimed_at.timestamp()
            query = select(table).where(
                and_(
                    ~table.c.status.in_(TERMINAL),
                    or_(table.c.lease_owner.is_(None), table.c.lease_until < current_time),
                )
            )
            # JSON expressions compile to native PostgreSQL/SQLite extraction;
            # existing schema-v1 databases need no new columns or migration.
            # Every normal batch ages ahead of new interactive work. Cleanup and
            # lease recovery still take precedence; running work is not preempted.
            service_class = func.coalesce(
                json_scalar(self.engine, table.c.payload, "snapshot", "scheduling", "service_class"), "batch"
            )
            aging_at = json_scalar(self.engine, table.c.payload, "snapshot", "scheduling", "aging_at")
            aged = or_(
                aging_at <= claimed_at.isoformat(),
                and_(
                    aging_at.is_(None),
                    table.c.created_at <= (claimed_at - timedelta(seconds=QUEUE_AGING_SECONDS)).isoformat(),
                ),
            )
            priority = case(
                (table.c.cancel_requested == true(), 0),
                (table.c.lease_token > 0, 1),
                (and_(service_class == "batch", aged), 2),
                (service_class == "interactive", 3),
                else_=4,
            )
            ordered = query.order_by(priority, table.c.created_at, table.c.id)
            if self.engine.dialect.name == "dm":
                # DM applies FETCH FIRST before SKIP LOCKED: all concurrent workers
                # may select the same head then skip it despite other available
                # rows. Read only ordered ids, then individually lock and recheck
                # eligibility. No snapshot-bearing payload is read until locked.
                task = None
                for key in (
                    tx.connection.execute(ordered.with_only_columns(table.c.id).limit(64)).scalars().all()
                ):
                    candidate = query.where(table.c.id == key).with_for_update(skip_locked=True)
                    task = self._record(tx.connection.execute(candidate).first())
                    if task is not None:
                        break
            else:
                query = ordered.limit(1)
                if supports_row_lock(self.engine):
                    query = query.with_for_update(skip_locked=True)
                task = self._record(tx.connection.execute(query).first())
            if not task:
                return None
            recovered = bool(task.get("lease_token", 0))
            task.update(
                lease_owner=worker_id,
                lease_token=task.get("lease_token", 0) + 1,
                lease_until=current_time + lease_seconds,
            )
            task.setdefault("first_claimed_at", claimed_at.isoformat())
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
        if supports_row_lock(self.engine):
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
