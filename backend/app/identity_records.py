"""Transactional identity operations shared by SQLite history and DM8 deployments."""

from __future__ import annotations

import json
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime

from sqlalchemy import select

from .platform.dameng import identities, identity_guard


class DuplicateIdentity(ValueError):
    pass


class SQLiteRecords:
    def __init__(self, connection):
        self.connection = connection

    def get(self, kind, key):
        row = self.connection.execute(
            "SELECT data FROM records WHERE kind=? AND id=?", (kind, key)
        ).fetchone()
        return json.loads(row[0]) if row else None

    def list(self, kind):
        return [
            json.loads(row[0])
            for row in self.connection.execute("SELECT data FROM records WHERE kind=?", (kind,))
        ]

    def any(self, kind):
        return (
            self.connection.execute("SELECT 1 FROM records WHERE kind=? LIMIT 1", (kind,)).fetchone()
            is not None
        )

    def insert(self, kind, record):
        if self.get(kind, record["id"]) is not None:
            raise DuplicateIdentity()
        self.connection.execute(
            "INSERT INTO records(kind,id,data,updated_at) VALUES(?,?,?,?)",
            (kind, record["id"], json.dumps(record, ensure_ascii=False), record["updated_at"]),
        )

    def replace(self, kind, record):
        self.delete(kind, record["id"])
        self.insert(kind, record)

    def delete(self, kind, key=None):
        sql = "DELETE FROM records WHERE kind=?"
        return self.connection.execute(
            sql if key is None else sql + " AND id=?", (kind,) if key is None else (kind, key)
        ).rowcount


class DMRecords:
    def __init__(self, connection):
        self.connection = connection

    def get(self, kind, key):
        return self.connection.execute(
            select(identities.c.data).where(identities.c.kind == kind, identities.c.id == key)
        ).scalar_one_or_none()

    def list(self, kind):
        return (
            self.connection.execute(
                select(identities.c.data)
                .where(identities.c.kind == kind)
                .order_by(identities.c.updated_at.desc())
            )
            .scalars()
            .all()
        )

    def any(self, kind):
        return (
            self.connection.execute(select(identities.c.id).where(identities.c.kind == kind).limit(1)).first()
            is not None
        )

    def insert(self, kind, record):
        if self.get(kind, record["id"]) is not None:
            raise DuplicateIdentity()
        self.connection.execute(
            identities.insert().values(
                kind=kind, id=record["id"], data=record, updated_at=record["updated_at"]
            )
        )

    def replace(self, kind, record):
        if self.get(kind, record["id"]) is None:
            self.insert(kind, record)
        else:
            self.connection.execute(
                identities.update()
                .where(identities.c.kind == kind, identities.c.id == record["id"])
                .values(data=record, updated_at=record["updated_at"])
            )

    def delete(self, kind, key=None):
        stmt = identities.delete().where(identities.c.kind == kind)
        if key is not None:
            stmt = stmt.where(identities.c.id == key)
        return self.connection.execute(stmt).rowcount


class DMIdentityStore:
    """Lock one existing guard row for auth writes; no process-local uniqueness races."""

    def __init__(self, engine, private_directory):
        self.engine = engine
        # The identity data is in DM. This path identifies the private bootstrap-secret directory only.
        self.path = private_directory / "dm-identity"

    @contextmanager
    def transaction(self):
        with self.engine.begin() as conn:
            conn.execute(
                select(identity_guard.c.id).where(identity_guard.c.id == 1).with_for_update()
            ).scalar_one()
            yield DMRecords(conn)

    def list(self, kind):
        with self.engine.connect() as conn:
            return DMRecords(conn).list(kind)

    def get(self, kind, record_id):
        with self.engine.connect() as conn:
            return DMRecords(conn).get(kind, record_id)

    def save(self, kind, item):
        record = dict(item)
        stamp = datetime.now(UTC).isoformat()
        record.setdefault("id", uuid.uuid4().hex[:16])
        record.setdefault("created_at", stamp)
        record["updated_at"] = stamp
        with self.transaction() as tx:
            tx.replace(kind, record)
        return record

    def delete(self, kind, record_id):
        with self.transaction() as tx:
            return tx.delete(kind, record_id) > 0

    def audit(self, action, entity, detail, user="system"):
        return self.save("audits", {"action": action, "entity": entity, "detail": detail, "user": user})
