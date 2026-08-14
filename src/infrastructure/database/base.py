"""The single shared SQLAlchemy declarative base.

Every component's own `models.py` inherits its `*Record` classes from this
`Base` (see docs/architecture/repository-structure.md and
docs/architecture/database-ownership.md). One `Base` means one
`MetaData`, which is what makes a single Alembic autogenerate run able to
see every component's tables.

This module defines no table. Table schemas are owned exclusively by the
component listed in docs/architecture/database-ownership.md.
"""

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

# Deterministic constraint/index names so Alembic autogenerate can emit
# stable, reversible DDL instead of relying on PostgreSQL-assigned names.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative base shared by every component's SQLAlchemy models."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


__all__ = ["NAMING_CONVENTION", "Base"]
