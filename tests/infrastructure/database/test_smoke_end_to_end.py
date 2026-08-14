"""End-to-end smoke test for the engine -> session -> Base chain.

Defines a single throwaway table directly on the shared `Base` inside this
test module (never touching any real component's `models.py`), creates it
via `Base.metadata.create_all` against a SQLite in-memory engine restricted
to just this table, and performs one insert/select round trip.

Uses a plain sync `sqlalchemy.orm.Session` rather than this package's async
`get_session`, because the async path requires `aiosqlite` for SQLite,
which is not among the installed dependencies in this environment (only
`psycopg[binary]` is listed in pyproject.toml for PostgreSQL) — this is the
sync-SQLite fallback the task brief explicitly allows for
engine/session-lifecycle-only coverage. The commit/rollback *sequencing* of
the async session API is covered separately, against mocks, in
test_session.py.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import String, Uuid
from sqlalchemy.orm import Mapped, Session, mapped_column

from infrastructure.database.base import Base
from infrastructure.database.config import DatabaseSettings
from infrastructure.database.engine import create_database_engine


class _SmokeTestWidgetRecord(Base):
    """Throwaway table that exists only for this test module.

    Not a business table and not owned by any component per
    docs/architecture/database-ownership.md — it validates the shared
    Base/engine/session machinery only.
    """

    __tablename__ = "_database_infra_smoke_test_widgets"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)


def test_create_all_and_round_trip_insert_select() -> None:
    settings = DatabaseSettings(url="sqlite:///:memory:")
    engine = create_database_engine(settings)
    try:
        # Restrict create_all to just this table so the test stays isolated
        # from whatever other tables other tests in this session may have
        # accumulated onto the shared Base.metadata (e.g. via metadata.py's
        # load_metadata()).
        Base.metadata.create_all(engine, tables=[_SmokeTestWidgetRecord.__table__])

        widget_id = uuid4()
        with Session(engine) as session:
            session.add(_SmokeTestWidgetRecord(id=widget_id, name="round-trip"))
            session.commit()

        with Session(engine) as session:
            fetched = session.get(_SmokeTestWidgetRecord, widget_id)
            assert fetched is not None
            assert fetched.name == "round-trip"
    finally:
        Base.metadata.drop_all(engine, tables=[_SmokeTestWidgetRecord.__table__])
        engine.dispose()


def test_round_trip_rolls_back_uncommitted_changes() -> None:
    settings = DatabaseSettings(url="sqlite:///:memory:")
    engine = create_database_engine(settings)
    try:
        Base.metadata.create_all(engine, tables=[_SmokeTestWidgetRecord.__table__])
        widget_id = uuid4()

        with Session(engine) as session:
            session.add(_SmokeTestWidgetRecord(id=widget_id, name="never-committed"))
            session.rollback()

        with Session(engine) as session:
            assert session.get(_SmokeTestWidgetRecord, widget_id) is None
    finally:
        Base.metadata.drop_all(engine, tables=[_SmokeTestWidgetRecord.__table__])
        engine.dispose()
