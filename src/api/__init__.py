"""Top-level FastAPI app assembly. See
docs/architecture/repository-structure.md.

This package only mounts each component's own api/ router — it never
defines a route itself. Business logic and route handlers live in each
component's own api/ submodule.
"""
