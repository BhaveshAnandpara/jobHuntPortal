"""Shared enumerations.

Locked contract — see docs/architecture/shared-types.md#shared-enums and
docs/architecture/state-machines.md. No component may invent its own string
values for any of these concepts; extend an enum here rather than creating a
parallel one. Enum values are additive-only in normal operation (see
shared-types.md#versioning-rules).
"""

from enum import Enum


class ResumeStatus(str, Enum):
    """Owner: Resume/Profile Service. See state-machines.md#resume-lifecycle-resumestatus--resumestatus."""

    UPLOADED = "UPLOADED"
    PARSING = "PARSING"
    PARSED = "PARSED"
    PARSE_FAILED = "PARSE_FAILED"
    ARCHIVED = "ARCHIVED"


class ProfileStatus(str, Enum):
    """Owner: Resume/Profile Service. See
    state-machines.md#candidateprofile-lifecycle-candidateprofilestatus--profilestatus.
    """

    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


class JobSourceType(str, Enum):
    """Owner: Job Discovery Service. Extensible — adding a source is additive."""

    MANUAL_URL = "MANUAL_URL"
    LINKEDIN = "LINKEDIN"
    INDEED = "INDEED"
    COMPANY_CAREERS_PAGE = "COMPANY_CAREERS_PAGE"
    OTHER = "OTHER"


class JobProcessingStatus(str, Enum):
    """Owner: Job Ingestion / Job Discovery Service."""

    PENDING = "PENDING"
    NORMALIZED = "NORMALIZED"
    MATCHED = "MATCHED"
    FAILED = "FAILED"


class MatchRecommendation(str, Enum):
    """Owner: Job Matching Service."""

    SHORTLIST = "SHORTLIST"
    BORDERLINE = "BORDERLINE"
    IGNORE = "IGNORE"


class ContactType(str, Enum):
    """Owner: Contact Discovery Service.

    Deliberately profession-generic — captures functional tier only
    (practitioner vs. lead vs. hiring manager vs. recruiter vs. executive).
    A profession-specific title (e.g. "Senior Mechanical Engineer") belongs
    in `Contact.headline` as free text, never as a new enum member here. See
    shared-types.md#shared-enums.
    """

    PRACTITIONER = "PRACTITIONER"
    TEAM_LEAD = "TEAM_LEAD"
    HIRING_MANAGER = "HIRING_MANAGER"
    RECRUITER = "RECRUITER"
    EXECUTIVE = "EXECUTIVE"
    DEPARTMENT_LEADER = "DEPARTMENT_LEADER"
    OTHER = "OTHER"


class ContactStatus(str, Enum):
    """Owner: Contact Discovery Service. See
    state-machines.md#contact-lifecycle-contactstatus--contactstatus.
    """

    DISCOVERED = "DISCOVERED"
    RANKED = "RANKED"
    ARCHIVED = "ARCHIVED"


class OutreachChannel(str, Enum):
    """Owner: Outreach Service."""

    EMAIL = "EMAIL"
    LINKEDIN_MESSAGE = "LINKEDIN_MESSAGE"
    LINKEDIN_CONNECTION_REQUEST = "LINKEDIN_CONNECTION_REQUEST"
    OTHER = "OTHER"


class OutreachStatus(str, Enum):
    """Owner: Outreach Service. See state-machines.md#outreach-lifecycle-outreachstatus--outreachstatus."""

    DRAFT = "DRAFT"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EDITED = "EDITED"
    SENT = "SENT"
    SEND_FAILED = "SEND_FAILED"


class OutreachDecisionType(str, Enum):
    """Owner: Outreach Service. Used in OutreachApprovedEvent's
    OutreachDecision payload — see event-contracts.md#outreachapprovedevent.
    """

    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class ApplicationStatus(str, Enum):
    """Owner: Tracking Service. See state-machines.md#opportunity-lifecycle-applicationstatus--applicationstatus.

    Terminal states: OFFER, REJECTED, IGNORED, WITHDRAWN.
    """

    DISCOVERED = "DISCOVERED"
    MATCHED = "MATCHED"
    SHORTLISTED = "SHORTLISTED"
    CONTACT_SEARCH = "CONTACT_SEARCH"
    CONTACT_FOUND = "CONTACT_FOUND"
    OUTREACH_GENERATED = "OUTREACH_GENERATED"
    OUTREACH_APPROVED = "OUTREACH_APPROVED"
    OUTREACH_SENT = "OUTREACH_SENT"
    REFERRED = "REFERRED"
    APPLIED = "APPLIED"
    INTERVIEW = "INTERVIEW"
    OFFER = "OFFER"
    REJECTED = "REJECTED"
    IGNORED = "IGNORED"
    WITHDRAWN = "WITHDRAWN"


class RemoteWorkPreference(str, Enum):
    """Owner: User Service."""

    REMOTE = "REMOTE"
    HYBRID = "HYBRID"
    ONSITE = "ONSITE"
    NO_PREFERENCE = "NO_PREFERENCE"


class ProfileChangeType(str, Enum):
    """Owner: Resume/Profile Service. Used in ProfileUpdatedEvent's
    ProfileUpdateSummary payload — see event-contracts.md#profileupdatedevent.
    """

    CREATED = "CREATED"
    ARCHIVED = "ARCHIVED"


class EventType(str, Enum):
    """Owner: shared, additive only. One member per Kafka event payload
    type. See event-contracts.md#eventtype-enum-members.
    """

    JOB_DISCOVERED = "JOB_DISCOVERED"
    PROFILE_UPDATED = "PROFILE_UPDATED"
    JOB_MATCHED = "JOB_MATCHED"
    JOB_SHORTLISTED = "JOB_SHORTLISTED"
    CONTACTS_REQUESTED = "CONTACTS_REQUESTED"
    CONTACTS_FOUND = "CONTACTS_FOUND"
    OUTREACH_GENERATED = "OUTREACH_GENERATED"
    OUTREACH_APPROVED = "OUTREACH_APPROVED"
    OUTREACH_SENT = "OUTREACH_SENT"
    APPLICATION_UPDATED = "APPLICATION_UPDATED"


class WorkflowType(str, Enum):
    """Owner: shared, additive only."""

    JOB_MATCHING = "JOB_MATCHING"
    CONTACT_DISCOVERY = "CONTACT_DISCOVERY"
    OUTREACH_GENERATION = "OUTREACH_GENERATION"


class WorkflowStatus(str, Enum):
    """Owner: shared. See state-machines.md#agent-execution-lifecycle-workflowexecutionstatus--workflowstatus."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    RETRYING = "RETRYING"


class AgentExecutionStatus(str, Enum):
    """Owner: shared. Node/tool-call granularity, distinct from
    WorkflowStatus (run granularity). Not persisted per-node — used only for
    structured logging/tracing within a run.
    """

    STARTED = "STARTED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    RETRIED = "RETRIED"


__all__ = [
    "ResumeStatus",
    "ProfileStatus",
    "JobSourceType",
    "JobProcessingStatus",
    "MatchRecommendation",
    "ContactType",
    "ContactStatus",
    "OutreachChannel",
    "OutreachStatus",
    "OutreachDecisionType",
    "ApplicationStatus",
    "RemoteWorkPreference",
    "ProfileChangeType",
    "EventType",
    "WorkflowType",
    "WorkflowStatus",
    "AgentExecutionStatus",
]
