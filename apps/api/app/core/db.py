"""Engine/session + tenant-scoped repository.

Defense in depth on isolation:
  1) App layer  — TenantScope filters EVERY query by the server-resolved tenant_id.
  2) DB layer   — Postgres Row-Level Security (added in the Alembic baseline) enforces the
                  same boundary even if app code forgets. RLS is a no-op on SQLite, so the
                  app-layer guard is what the sandbox tests exercise.
Tenancy is ALWAYS server-resolved and never taken from the client for scoping.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session as OrmSession
from sqlalchemy.orm import sessionmaker

from .config import get_settings

_settings = get_settings()
_connect_args = {"check_same_thread": False} if _settings.database_url.startswith("sqlite") else {}
engine = create_engine(_settings.database_url, future=True, connect_args=_connect_args)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, class_=OrmSession)


class ScopedQuery:
    """Chainable, tenant-scoped query wrapper (supports .filter(), .first(), .all(), iteration)."""

    def __init__(self, db: OrmSession, stmt):
        self._db = db
        self._stmt = stmt

    def filter(self, *conditions):
        self._stmt = self._stmt.where(*conditions)
        return self

    def first(self):
        return self._db.execute(self._stmt).scalars().first()

    def all(self):
        return list(self._db.execute(self._stmt).scalars())

    def __iter__(self):
        return iter(self._db.execute(self._stmt).scalars())


@contextmanager
def session_scope() -> Iterator[OrmSession]:
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


class TenantScope:
    """Wraps a DB session and forces tenant_id on reads and writes."""

    def __init__(self, db: OrmSession, tenant_id: str):
        self.db = db
        self.tenant_id = tenant_id

    def add(self, obj):
        if hasattr(obj, "tenant_id") and not getattr(obj, "tenant_id", None):
            obj.tenant_id = self.tenant_id
        elif hasattr(obj, "tenant_id") and obj.tenant_id != self.tenant_id:
            raise PermissionError("cross-tenant write blocked")
        self.db.add(obj)
        return obj

    def query(self, model):
        stmt = select(model)
        if hasattr(model, "tenant_id"):
            stmt = stmt.where(model.tenant_id == self.tenant_id)
        return ScopedQuery(self.db, stmt)

    def get(self, model, **filters):
        stmt = select(model).where(model.tenant_id == self.tenant_id)
        for k, v in filters.items():
            stmt = stmt.where(getattr(model, k) == v)
        return self.db.execute(stmt).scalars().first()

    def flush(self):
        self.db.flush()
