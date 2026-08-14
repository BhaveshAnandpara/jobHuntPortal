"""Tests for the Alembic model-discovery helper.

Verifies metadata.py is consistent with base.py's `NAMING_CONVENTION` (not a
duplicate/competing MetaData definition) and that discovery finds every
component's `models.py` without hard-coding a component list here (that
would make this infrastructure layer depend on business components, contrary
to docs/architecture/dependency-graph.md).
"""

from __future__ import annotations

from infrastructure.database.base import NAMING_CONVENTION, Base
from infrastructure.database.metadata import discover_model_modules, load_metadata


def test_discover_model_modules_finds_known_component_models() -> None:
    modules = discover_model_modules()
    # These three components' models.py are known, already-implemented
    # tables per docs/architecture/database-ownership.md; asserting their
    # presence (rather than the full/exact set) keeps this test from being
    # coupled to components not yet implemented in this wave.
    assert "users.models" in modules
    assert "profiles.models" in modules
    assert "jobs.models" in modules


def test_discover_model_modules_excludes_infrastructure_and_shared() -> None:
    modules = discover_model_modules()
    assert not any(m.startswith("infrastructure.") for m in modules)
    assert not any(m.startswith("shared.") for m in modules)


def test_discover_model_modules_only_returns_models_modules() -> None:
    modules = discover_model_modules()
    assert all(m.rsplit(".", 1)[-1] == "models" for m in modules)


def test_load_metadata_returns_the_shared_base_metadata_object() -> None:
    # Not a copy, not a second MetaData instance — Alembic autogenerate must
    # see the exact same object every component's Base.metadata accumulates
    # onto, or it would miss tables.
    assert load_metadata() is Base.metadata


def test_load_metadata_uses_the_same_naming_convention_as_base() -> None:
    metadata = load_metadata()
    assert metadata.naming_convention == NAMING_CONVENTION


def test_load_metadata_picks_up_already_implemented_component_tables() -> None:
    metadata = load_metadata()
    # users/models.py and profiles/models.py are real, already-implemented
    # tables (per database-ownership.md) at the time this test runs.
    assert "users" in metadata.tables
    assert "resumes" in metadata.tables
