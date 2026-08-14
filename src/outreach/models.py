"""Database record types owned by Outreach Service.
See docs/architecture/database-ownership.md#outreach.

Field shapes are locked in docs/architecture/domain-model.md#outreach — do
not diverge from them when implementing.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, String, Uuid
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from infrastructure.database import Base
from shared.types.enums import OutreachChannel, OutreachStatus

# Stored as VARCHAR + CHECK rather than a native PostgreSQL ENUM type, same
# rationale as matching/models.py's _MATCH_RECOMMENDATION and
# contacts/models.py's _CONTACT_TYPE/_CONTACT_STATUS: enum values are
# additive-only (shared-types.md#versioning-rules), so a non-native enum
# makes adding a member a code change rather than a DDL migration on a
# shared type.
_OUTREACH_CHANNEL = SAEnum(OutreachChannel, name="outreach_channel", native_enum=False, length=32)
_OUTREACH_STATUS = SAEnum(OutreachStatus, name="outreach_status", native_enum=False, length=32)


class OutreachRecord(Base):
    """Maps to the `outreach` table. May create/update: Outreach Service
    only — including the human approval and send-worker updates, both
    internal to this service.

    `job_id`/`contact_id`/`user_id`/`decided_by` are *logical* foreign keys
    only (no DB-enforced `ForeignKey(...)`) to other components' tables
    (`jobs`, `contacts`, `users`) — same precedent as
    `matching/models.py:JobMatchRecord` and `contacts/models.py:ContactRecord`
    (see those modules' docstrings for the full cross-component-reference
    rationale).
    """

    __tablename__ = "outreach"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    job_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)
    contact_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)
    user_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)
    channel: Mapped[OutreachChannel] = mapped_column(_OUTREACH_CHANNEL, nullable=False)
    draft_message: Mapped[str] = mapped_column(String, nullable=False)
    final_message: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[OutreachStatus] = mapped_column(_OUTREACH_STATUS, nullable=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_by: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    external_message_id: Mapped[str | None] = mapped_column(String, nullable=True)
    send_error: Mapped[str | None] = mapped_column(String, nullable=True)
    # Persistence-layer-only field, deliberately NOT on the canonical
    # `Outreach` domain type (shared.types.domain.outreach) or on
    # `OutreachDraft`/`OutreachResponse`/any event payload —
    # domain-model.md#outreach's field list is locked and has no
    # recipient-address concept. Same precedent as
    # `matching/models.py:JobMatchRecord.published_at`
    # (database-ownership.md#job_matches's "Publish-reliability column"
    # section: a persistence-only column the domain type never exposes).
    #
    # The gap this closes: the send-worker consumer
    # (`outreach.consumers.handle_outreach_approved`) needs a real address
    # to hand `MessageSendClient` (`OutboundMessage.recipient`), but the
    # locked `Outreach` entity carries only `contact_id`, never the
    # contact's `profile_url`/`email` — and Outreach Service has no
    # permitted read path back to `Contact` at send time
    # (database-ownership.md#contacts: "May read: Outreach Service ... via
    # ContactRankingResult event payload, not direct query" — and that
    # event has already been fully consumed by the time `outreach.approved`
    # is processed, in a separate consumer invocation, possibly a separate
    # process). `select_channel` already resolved the reachable address
    # (`contact.profile_url` or `contact.email`) once, at generation time,
    # to choose the channel; `persist_and_publish` stores that same value
    # here so the send worker doesn't need a second, currently-nonexistent
    # API dependency just to re-look it up. Never projected by
    # `OutreachRepository._to_domain` — read only via
    # `OutreachRepository.get_recipient_address`. Flagged as a real,
    # documented architecture gap in this component's implementation
    # report (the clean long-term fix would be an additive
    # `RankedContact`/`ContactRankingResult` field, or a persisted
    # recipient concept in domain-model.md#outreach).
    recipient_address: Mapped[str | None] = mapped_column(String, nullable=True)


__all__ = ["OutreachRecord"]
