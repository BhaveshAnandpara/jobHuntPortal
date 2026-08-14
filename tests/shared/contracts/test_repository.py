"""Regression test for the shared.contracts import defect: `__init__.py`
imported `shared.contracts.repository.Repository`, but repository.py did not
exist, so `import shared.contracts` raised ModuleNotFoundError for every
caller (see docs/architecture — Job Matching architecture cleanup report).
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID, uuid4

import pytest


def test_shared_contracts_package_imports() -> None:
    from shared.contracts import Repository

    assert Repository is not None


def test_repository_is_a_protocol() -> None:
    from shared.contracts.repository import Repository

    assert issubclass(type(Repository), type(Protocol))


@pytest.mark.asyncio
async def test_a_conforming_repository_satisfies_the_protocol_structurally() -> None:
    from shared.contracts.repository import Repository

    class _Entity:
        def __init__(self, id: UUID) -> None:
            self.id = id

    class _FakeRepository:
        def __init__(self) -> None:
            self._rows: dict[UUID, _Entity] = {}

        async def get(self, entity_id: UUID) -> _Entity | None:
            return self._rows.get(entity_id)

        async def add(self, entity: _Entity) -> _Entity:
            self._rows[entity.id] = entity
            return entity

    repo: Repository[_Entity, UUID] = _FakeRepository()  # type: ignore[assignment]
    entity = _Entity(id=uuid4())

    added = await repo.add(entity)
    fetched = await repo.get(entity.id)

    assert added is entity
    assert fetched is entity


def test_existing_repositories_already_satisfy_the_shape_without_modification() -> None:
    """Confirms the Protocol's shape (`get`/`add`) matches already-working
    repositories, per the task's "without changing the repository contract
    semantics" requirement — no source change to these classes was needed.
    """
    import inspect

    from matching.repository import JobMatchRepository
    from profiles.repository import CandidateProfileRepository, ResumeRepository
    from users.repository import UserRepository

    for cls in (UserRepository, ResumeRepository, CandidateProfileRepository, JobMatchRepository):
        assert "get" in dir(cls)
        assert "add" in dir(cls)
        assert inspect.iscoroutinefunction(cls.get)
        assert inspect.iscoroutinefunction(cls.add)
