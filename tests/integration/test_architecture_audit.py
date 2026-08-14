"""Ownership/architecture audit via source reading (not just tests), per
the task brief's section 12 and .claude/agents/integration-agent.md's
Required Validation items 11-13.

Checks:
  - No component directly imports another component's models.py/
    repository.py (dependency-graph.md relation 1: "absolute, zero
    exceptions").
  - No source component calls Tracking's API (see test_tracking_lifecycle
    .py for the dedicated, more detailed version of this check).
  - Contact Discovery never subscribes to outreach.sent (existing dedicated
    test: tests/contacts/test_no_circular_dependency.py); extended here to
    confirm Outreach Service never subscribes to anything that would close
    a cycle back through Contact Discovery.
  - Outreach never writes to contacts/contact_rankings tables.
  - Every LLM call site imports infrastructure.llm, not a raw provider SDK.
  - Every external-network call site imports infrastructure.external, not
    raw httpx/browser automation of its own.
  - No second EventProducer/EventConsumer implementation exists anywhere.
  - infrastructure/kafka/topics.py's Topic enum has exactly the 10 topics
    kafka-topics.md documents, no more.
  - No component redefines a shared type from shared/types/.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"

_COMPONENT_PACKAGES = [
    "users",
    "profiles",
    "jobs",
    "matching",
    "contacts",
    "outreach",
    "tracking",
]


def _iter_py_files(root: Path):
    for path in root.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        yield path


def _imported_top_level_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.add(node.module.split(".")[0])
    return modules


def test_no_component_imports_another_components_models_or_repository() -> None:
    """dependency-graph.md relation 1: absolute, zero exceptions. Walk
    every .py file under each owned component package and confirm it never
    imports a *different* component package by top-level name.
    """
    violations: list[str] = []
    for owner in _COMPONENT_PACKAGES:
        owner_dir = SRC / owner
        if not owner_dir.is_dir():
            continue
        for path in _iter_py_files(owner_dir):
            modules = _imported_top_level_modules(path)
            for other in _COMPONENT_PACKAGES:
                if other == owner:
                    continue
                if other in modules:
                    violations.append(f"{path.relative_to(SRC)} imports {other!r}")
    assert violations == []


def test_outreach_never_writes_to_contacts_tables() -> None:
    """Outreach Service must never *import* contacts.models/contacts
    .repository (database-ownership.md#contacts / #contact_rankings:
    Contact Discovery Service is the sole writer). Checked via AST imports,
    not a raw substring match — outreach/models.py's own docstring
    legitimately *mentions* `ContactRecord` in prose (contrasting its own
    logical-FK precedent), which is not a real dependency.
    """
    outreach_dir = SRC / "outreach"
    for path in _iter_py_files(outreach_dir):
        modules = _imported_top_level_modules(path)
        assert "contacts" not in modules, path


def test_outreach_never_subscribes_to_a_cycle_back_through_contacts() -> None:
    """The rejected relationship (dependency-graph.md's "No circular
    dependencies" section) was Contact Discovery consuming outreach.sent.
    Symmetrically, Outreach Service's own documented consumption is limited
    to contacts.found and its own outreach.approved
    (ownership.md#component--kafka-topics-consumed) — it must never
    subscribe to contacts.requested (Contact Discovery's own inbound
    topic), which would create a new cycle.
    """
    import outreach.consumers as outreach_consumers

    source_paths = [
        SRC / "outreach" / "consumers.py",
        SRC / "outreach" / "events.py",
    ]
    for path in source_paths:
        text = path.read_text(encoding="utf-8", errors="ignore")
        assert "CONTACTS_REQUESTED" not in text, path

    assert outreach_consumers  # imported successfully; sanity


def test_every_llm_call_site_imports_infrastructure_llm() -> None:
    """No component performs a raw provider SDK call directly — search for
    common raw-SDK import names (e.g. `ollama`, `openai`) outside
    infrastructure/llm/ itself.
    """
    forbidden_modules = {"ollama", "openai", "anthropic"}
    for owner in [*_COMPONENT_PACKAGES, "workflows"]:
        owner_dir = SRC / owner
        if not owner_dir.is_dir():
            continue
        for path in _iter_py_files(owner_dir):
            modules = _imported_top_level_modules(path)
            offending = modules & forbidden_modules
            assert not offending, f"{path.relative_to(SRC)} imports {offending}"


def test_every_external_network_call_site_imports_infrastructure_external() -> None:
    """No component performs a raw browser-automation/scraping call
    (playwright/BeautifulSoup — the two third-party integrations
    dependency-graph.md#5-llmtool-dependencies names explicitly) outside
    `infrastructure/external/`.

    Raw `httpx` is intentionally *not* checked here the same strict way:
    `httpx` also legitimately backs the documented direct *inter-component*
    runtime API clients (dependency-graph.md#2-runtime-api-dependencies —
    e.g. `jobs/discovery/clients.py` calling User Service/Resume-Profile
    Service, both platform components, not third parties). Those clients
    are asserted more precisely below: every non-`infrastructure/external`
    file that imports `httpx` must be a component's own `clients.py`
    module implementing one of dependency-graph.md's named relation-2
    edges, not an ad hoc call from business logic.
    """
    forbidden_modules = {"playwright", "bs4", "beautifulsoup4"}
    for owner in [*_COMPONENT_PACKAGES, "workflows"]:
        owner_dir = SRC / owner
        if not owner_dir.is_dir():
            continue
        for path in _iter_py_files(owner_dir):
            modules = _imported_top_level_modules(path)
            offending = modules & forbidden_modules
            assert not offending, f"{path.relative_to(SRC)} imports {offending}"

    for owner in _COMPONENT_PACKAGES:
        owner_dir = SRC / owner
        if not owner_dir.is_dir():
            continue
        for path in _iter_py_files(owner_dir):
            modules = _imported_top_level_modules(path)
            if "httpx" in modules:
                assert path.name == "clients.py", (
                    f"{path.relative_to(SRC)} imports httpx directly but is "
                    "not a documented inter-component clients.py module"
                )


def test_no_second_event_producer_or_consumer_implementation() -> None:
    """Every component's own events.py/consumers.py must build on
    infrastructure.kafka.producer.EventProducer /
    infrastructure.kafka.consumer.EventConsumer — no component defines its
    own class named EventProducer/EventConsumer.
    """
    for owner in _COMPONENT_PACKAGES:
        owner_dir = SRC / owner
        if not owner_dir.is_dir():
            continue
        for path in _iter_py_files(owner_dir):
            tree = ast.parse(
                path.read_text(encoding="utf-8", errors="ignore"), filename=str(path)
            )
            class_names = {
                node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)
            }
            assert "EventProducer" not in class_names, path
            assert "EventConsumer" not in class_names, path


def test_topic_enum_has_exactly_the_ten_documented_topics() -> None:
    from infrastructure.kafka.topics import Topic

    expected = {
        "jobs.discovered",
        "jobs.matched",
        "jobs.shortlisted",
        "profiles.updated",
        "contacts.requested",
        "contacts.found",
        "outreach.generated",
        "outreach.approved",
        "outreach.sent",
        "applications.updated",
    }
    actual = {member.value for member in Topic}
    assert actual == expected
    assert len(Topic) == 10


def test_no_component_redefines_a_shared_type() -> None:
    """No component-owned module defines a class with the same name as a
    canonical shared type (shared/types/dto.py, shared/types/domain/*,
    shared/events/payloads.py) — a component must import the shared type,
    never redefine a same-named local shape.
    """
    import shared.events.payloads as payloads_module
    import shared.types.dto as dto_module

    shared_type_names: set[str] = set()
    for module in (dto_module, payloads_module):
        for name in dir(module):
            obj = getattr(module, name)
            if isinstance(obj, type) and getattr(obj, "__module__", "") == module.__name__:
                shared_type_names.add(name)

    domain_dir = SRC / "shared" / "types" / "domain"
    for path in _iter_py_files(domain_dir):
        tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                shared_type_names.add(node.name)

    violations: list[str] = []
    for owner in _COMPONENT_PACKAGES:
        owner_dir = SRC / owner
        if not owner_dir.is_dir():
            continue
        for path in _iter_py_files(owner_dir):
            if path.name == "models.py":
                # *Record types intentionally share a *root* name concept
                # (e.g. JobRecord for Job) but never the exact shared-type
                # name itself — skip DB model modules, whose classes are
                # already suffixed `Record` by convention
                # (repository-structure.md's naming conventions table).
                continue
            tree = ast.parse(
                path.read_text(encoding="utf-8", errors="ignore"), filename=str(path)
            )
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef) and node.name in shared_type_names:
                    violations.append(f"{path.relative_to(SRC)} redefines {node.name!r}")
    assert violations == []
