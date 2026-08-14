"""Generic repository contract — the structural shape every component's
persistence boundary follows for its primary entity, per
docs/architecture/repository-structure.md ("shared/contracts/ # shared
Protocol/ABC definitions (e.g. Repository base)").

This is a `typing.Protocol`, not a base class: a component's repository
satisfies it structurally (duck typing) by implementing `get`/`add` with
matching signatures — no inheritance, import cycle, or SQLAlchemy coupling
required. Nothing here encodes business logic or entity-specific fields
(this module's own docstring rule); `T`/`IdT` are supplied by each
component's own repository.

Already-implemented repositories (`UserRepository`, `ResumeRepository`,
`CandidateProfileRepository`, `JobMatchRepository`) already satisfy this
shape without modification. A repository is free to add further
domain-specific methods beyond `get`/`add` (e.g. `list_for_user`,
`get_by_email`, `upsert`) or to omit `add` in favor of a differently-named
write method when a single generic `add` doesn't fit the entity's write
pattern (e.g. `jobs.repository`'s `insert_manual`/`insert_discovered` split,
required by the two-writer `jobs` table — see
docs/architecture/ownership.md#shared-write-jobs-table) — those are
legitimate deviations, not violations of this contract. Nothing in the
codebase currently asserts `isinstance`/structural conformance against this
Protocol; it exists as a documented baseline shape for component authors
to follow, referenced by docstring in `contacts/repository.py`,
`outreach/repository.py`, and `tracking/repository.py`.
"""

from typing import Protocol, TypeVar

T = TypeVar("T")
IdT = TypeVar("IdT")


class Repository(Protocol[T, IdT]):
    """Structural minimum: fetch one entity by id, persist one new entity."""

    async def get(self, entity_id: IdT) -> T | None: ...

    async def add(self, entity: T) -> T: ...


__all__ = ["Repository"]
