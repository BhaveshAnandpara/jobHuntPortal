"""Collects every component's tables onto the shared `Base.metadata`.

Alembic autogenerate can only see a table whose model module has been
imported. Rather than hard-coding a list of components here — which would
make this infrastructure layer depend on business components, contrary to
docs/architecture/dependency-graph.md — the component `models.py` modules
are discovered by walking the source tree. The coupling is therefore
directional-by-convention only, and exists solely for the migration
tooling; nothing in the engine/session path imports a component.
"""

from __future__ import annotations

import importlib
import pkgutil
from pathlib import Path

from sqlalchemy import MetaData

from infrastructure.database.base import Base

SRC_ROOT = Path(__file__).resolve().parents[2]
MODELS_MODULE_NAME = "models"
EXCLUDED_TOP_LEVEL_PACKAGES = frozenset({"infrastructure", "shared"})


def discover_model_modules() -> list[str]:
    """Return the importable dotted paths of every component `models.py`."""
    modules: list[str] = []
    for package_path in sorted(SRC_ROOT.iterdir()):
        if not package_path.is_dir() or not (package_path / "__init__.py").exists():
            continue
        if package_path.name in EXCLUDED_TOP_LEVEL_PACKAGES:
            continue
        for module in pkgutil.walk_packages([str(package_path)], f"{package_path.name}."):
            if module.name.rsplit(".", 1)[-1] == MODELS_MODULE_NAME:
                modules.append(module.name)
    return modules


def load_metadata() -> MetaData:
    """Import every component's models and return the populated shared metadata."""
    for module_name in discover_model_modules():
        importlib.import_module(module_name)
    return Base.metadata


__all__ = ["discover_model_modules", "load_metadata"]
