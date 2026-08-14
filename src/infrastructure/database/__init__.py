"""Database infrastructure — SQLAlchemy engine/session and Alembic
migrations, shared by every component's own repository.py. Owned by the
Database Agent (.claude/agents/database-agent.md). See
docs/architecture/overview.md#deployment-model and
docs/architecture/ownership.md#infrastructure-ownership-non-business.

One PostgreSQL database; table *ownership* is enforced by code convention
(docs/architecture/database-ownership.md), not by network isolation. This
module owns only the connection/session/migration machinery, never a
business table's schema — those live under each owning component's
models.py.

Public surface re-exported here is the intended import point for every
other component's `models.py` / `repository.py` (see
docs/architecture/dependency-graph.md#4-database-dependencies):

    from infrastructure.database import Base            # models.py
    from infrastructure.database import get_session      # repository.py / api dependencies

`get_session` is an async generator yielding an `AsyncSession` — see
session.py for the exact commit/rollback contract.
"""

from infrastructure.database.base import NAMING_CONVENTION, Base
from infrastructure.database.config import (
    DatabaseSettings,
    build_database_url,
    get_database_settings,
)
from infrastructure.database.engine import (
    create_async_database_engine,
    create_database_engine,
    dispose_async_engine,
    dispose_engine,
    get_async_engine,
    get_engine,
)
from infrastructure.database.health import check_connection, check_connection_async
from infrastructure.database.session import (
    create_session_factory,
    get_session,
    get_session_factory,
    reset_session_factory,
    session_scope,
)

__all__ = [
    "NAMING_CONVENTION",
    "Base",
    "DatabaseSettings",
    "build_database_url",
    "check_connection",
    "check_connection_async",
    "create_async_database_engine",
    "create_database_engine",
    "create_session_factory",
    "dispose_async_engine",
    "dispose_engine",
    "get_async_engine",
    "get_database_settings",
    "get_engine",
    "get_session",
    "get_session_factory",
    "reset_session_factory",
    "session_scope",
]
