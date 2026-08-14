"""Tests for the shared declarative `Base` and its constraint-naming convention.

This module defines no business table — see
docs/architecture/database-ownership.md. It only exercises the shared
`Base`/`NAMING_CONVENTION` infrastructure that every component's own
`models.py` inherits.
"""

from __future__ import annotations

from sqlalchemy import Column, ForeignKey, Integer, MetaData, String, Table
from sqlalchemy.orm import DeclarativeBase

from infrastructure.database.base import NAMING_CONVENTION, Base


def test_base_is_a_declarative_base() -> None:
    assert issubclass(Base, DeclarativeBase)


def test_base_metadata_uses_naming_convention() -> None:
    assert Base.metadata.naming_convention == NAMING_CONVENTION


def test_naming_convention_covers_the_standard_constraint_types() -> None:
    # Alembic autogenerate needs deterministic names for every constraint
    # kind it may emit; missing one silently falls back to dialect-assigned
    # names, which is exactly what NAMING_CONVENTION exists to avoid.
    assert set(NAMING_CONVENTION) == {"ix", "uq", "ck", "fk", "pk"}


def test_naming_convention_produces_deterministic_names_across_metadata_instances() -> None:
    # Two independently-constructed MetaData objects using the same
    # convention must name an equivalent schema identically — this is what
    # makes Alembic's emitted DDL stable/reversible run to run.
    def build(metadata: MetaData) -> Table:
        parent = Table(
            "convention_check_parent",
            metadata,
            Column("id", Integer, primary_key=True),
        )
        Table(
            "convention_check_child",
            metadata,
            Column("id", Integer, primary_key=True),
            Column("parent_id", Integer, ForeignKey("convention_check_parent.id")),
            Column("name", String(32), unique=True),
        )
        return parent

    md_a = MetaData(naming_convention=NAMING_CONVENTION)
    md_b = MetaData(naming_convention=NAMING_CONVENTION)
    build(md_a)
    build(md_b)

    names_a = {c.name for t in md_a.tables.values() for c in t.constraints}
    names_b = {c.name for t in md_b.tables.values() for c in t.constraints}
    assert names_a == names_b
    assert "fk_convention_check_child_parent_id_convention_check_parent" in names_a
    assert "pk_convention_check_parent" in names_a
    assert "uq_convention_check_child_name" in names_a


def test_base_defines_no_table() -> None:
    # Base itself is abstract infrastructure; it must never accumulate a
    # table of its own (that would imply this layer owns business data).
    assert "__tablename__" not in Base.__dict__
    assert Base not in {mapper.class_ for mapper in Base.registry.mappers}
