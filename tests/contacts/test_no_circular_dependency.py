"""Scenario I: no circular dependency via `outreach.sent`.

docs/architecture/dependency-graph.md#no-circular-dependencies explicitly
documents that Contact Discovery Service consuming Outreach Service's
`outreach.sent` (to mark a contact "contacted") was rejected during the
architecture pass, because it would close a cycle with Outreach Service's
existing consumption of `contacts.found`. `ContactStatus` therefore has no
`CONTACTED` value, and this component must never subscribe to or reference
`outreach.sent`/`Topic.OUTREACH_SENT` anywhere.
"""

from __future__ import annotations

import inspect

import contacts.consumers as consumers_module
import contacts.events as events_module
from infrastructure.kafka.topics import Topic
from shared.types.enums import ContactStatus


def test_contacts_consumers_never_subscribes_to_outreach_sent() -> None:
    """The forbidden thing is *subscribing to/importing* `outreach.sent` —
    mentioning "Outreach Service" by name in an explanatory comment (e.g.
    contrasting this component's idempotency approach with what its
    downstream consumers must tolerate) is not a circular dependency and
    is not what this check guards against.
    """
    source = inspect.getsource(consumers_module)
    assert "outreach.sent" not in source.lower()
    assert "OUTREACH_SENT" not in source
    assert "outreach.approved" not in source.lower()
    assert "Topic.OUTREACH" not in source


def test_contacts_events_never_references_outreach_sent() -> None:
    source = inspect.getsource(events_module)
    assert "outreach.sent" not in source.lower()
    assert "OUTREACH_SENT" not in source


def test_contact_status_has_no_contacted_value() -> None:
    assert "CONTACTED" not in ContactStatus.__members__
    assert set(ContactStatus.__members__) == {"DISCOVERED", "RANKED", "ARCHIVED"}


def test_topic_outreach_sent_exists_but_is_never_imported_by_contacts_package() -> None:
    # Sanity check that OUTREACH_SENT is a real topic (so this test would
    # actually catch a regression), while confirming this component's own
    # modules never subscribe to it.
    assert Topic.OUTREACH_SENT.value == "outreach.sent"
    for module in (consumers_module, events_module):
        source = inspect.getsource(module)
        assert "OUTREACH_SENT" not in source
