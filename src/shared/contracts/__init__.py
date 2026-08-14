"""Shared Protocol/ABC definitions — cross-cutting interface boundaries
that every component's own internal modules implement. See
docs/architecture/repository-structure.md.

Nothing in this package encodes business logic or entity-specific fields;
that belongs in each component's own `repository.py`/`service.py`.
"""

from shared.contracts.repository import Repository

__all__ = ["Repository"]
