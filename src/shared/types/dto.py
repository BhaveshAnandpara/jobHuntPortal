"""Cross-cutting DTOs and canonical exchange types.

Locked contract — see docs/architecture/shared-types.md#cross-cutting-dtos
and docs/architecture/shared-types.md#canonical-exchange-types. Each
canonical exchange type below is the *only* shape used for its concept
anywhere it crosses a component boundary (API, event, or LangGraph state).
No component may define a local equivalent — see
docs/architecture/repository-structure.md#contract-naming-conventions.
"""

from datetime import datetime

from pydantic import BaseModel

from shared.errors.codes import ErrorCode
from shared.types.enums import (
    ApplicationStatus,
    ContactType,
    JobSourceType,
    MatchRecommendation,
    OutreachChannel,
    ProfileStatus,
)
from shared.types.ids import (
    ApplicationId,
    ContactId,
    JobId,
    JobMatchId,
    OutreachId,
    ProfileId,
    ResumeId,
    UserId,
)

# ---------------------------------------------------------------------------
# Cross-cutting DTOs
# ---------------------------------------------------------------------------


class EducationEntry(BaseModel):
    institution: str
    degree: str | None = None
    field_of_study: str | None = None
    graduation_year: int | None = None


class ContactInfo(BaseModel):
    """A candidate's own contact details, as literally stated on their
    resume. Every field is optional: a resume that never states a phone
    number must yield `phone=None`, never an invented one — and a resume
    with no discoverable contact details at all yields no `ContactInfo` at
    all (the owning field is `ContactInfo | None`).

    Lives here next to `EducationEntry` — like it, this shape is carried
    by both `CandidateProfile` (domain) and `ResumeProfile` (DTO), and
    `shared.types.domain` imports from this module rather than the other
    way around.
    """

    full_name: str | None = None
    email: str | None = None
    phone: str | None = None
    location: str | None = None
    # Profile links, stated on the resume the same way an email or phone
    # number is. Optional for exactly the same reason every field above is:
    # a resume that never prints a LinkedIn URL must yield `None`, never a
    # URL guessed from the candidate's name. `CandidateProfileRecord.contact`
    # is a JSON column (profiles/models.py), so these three need no database
    # migration — an existing row simply deserializes with all three unset.
    linkedin_url: str | None = None
    github_url: str | None = None
    portfolio_url: str | None = None
    # Whether an extraction pass has actually *looked* for the three link
    # fields above, as opposed to never having been asked for them.
    #
    # The three URL fields shipped after `full_name`/`email`/`phone`/
    # `location` did, so a profile parsed in between carries a populated
    # contact whose links are `None` because nothing ever searched for them
    # — indistinguishable, from the values alone, from a resume that
    # genuinely prints no links. This flag makes the two distinguishable:
    # `profiles.parsing.extract_profile_fields` sets it `True` on every
    # successful extraction, links found or not, and
    # `profiles.parsing.profile_needs_enrichment` re-parses exactly the
    # contacts that still say `False`. A resume with no links is therefore
    # checked once and never again.
    #
    # Additive with no migration, for the same reason the three URL fields
    # were: `CandidateProfileRecord.contact` is a JSON column
    # (profiles/models.py), so an existing row simply deserializes with this
    # unset — i.e. `False`, "never looked", which is exactly right for a row
    # written before this field existed.
    links_checked: bool = False


class WorkExperienceEntry(BaseModel):
    """One employment entry from a resume, kept profession-independent:
    `company`/`title` are free text, dates are free-text strings (resumes
    write them as "Jan 2020", "2020-01", "Spring 2020", ...) rather than
    parsed dates, and `bullets` are the entry's own achievement/
    responsibility lines verbatim.

    `end_date is None` means the role is current ("Present" on the resume).
    """

    company: str
    title: str
    start_date: str | None = None
    end_date: str | None = None  # None => current role ("Present")
    bullets: list[str] = []


class ProfileMatchScore(BaseModel):
    """One profile's score against one job.

    `reasoning`, `hard_blocker` and `insufficient_detail` are additive,
    default-bearing explanation fields (shared-types.md's additive-only
    versioning rule). They are persisted inside the `job_matches.
    profile_scores` JSON column, so rows written before they existed simply
    lack the keys and the defaults below apply on read — no migration.
    None of the three participates in `score`, `matched_skills` or
    `missing_skills`; they only explain a score that was computed exactly
    as it was before.
    """

    profile_id: ProfileId
    resume_id: ResumeId
    score: float  # 0.0-1.0
    matched_skills: list[str] = []
    missing_skills: list[str] = []
    # Why this score — the LLM already produced it; it is no longer dropped.
    reasoning: str = ""
    # A requirement the posting itself states as non-negotiable and that this
    # candidate clearly fails. None whenever the posting states no such
    # requirement, or states one the candidate meets. Never inferred.
    hard_blocker: str | None = None
    # True only when the posting carries too little real content to judge fit
    # with confidence — not merely when some structured fields are empty.
    insufficient_detail: bool = False


class WorkflowError(BaseModel):
    node: str
    error_code: ErrorCode
    message: str
    occurred_at: datetime


# ---------------------------------------------------------------------------
# Canonical exchange types
# ---------------------------------------------------------------------------


class ResumeProfile(BaseModel):
    """The read/composition type combining a Resume and its derived
    CandidateProfile. Used whenever another component needs "a user's
    profile" — most notably as an input to Job Matching.

    Produced by: Resume/Profile Service (GET /profiles, GET /profiles/{id}).
    Consumed by: Job Matching Service (as JobMatchingState.profiles).
    Not persisted directly — assembled on read from resumes +
    candidate_profiles.
    """

    profile_id: ProfileId
    resume_id: ResumeId
    user_id: UserId
    title: str
    summary: str | None = None
    contact: ContactInfo | None = None
    skills: list[str] = []
    experience_years: float | None = None
    seniority: str | None = None
    education: list[EducationEntry] = []
    work_history: list[WorkExperienceEntry] = []
    certifications: list[str] = []
    projects: list[str] = []
    industries: list[str] = []
    target_roles: list[str] = []
    status: ProfileStatus


class NormalizedJob(BaseModel):
    """The canonical job representation used everywhere after ingestion —
    this is what travels on Kafka and what Matching/Contact
    Discovery/Outreach operate on, instead of the raw Job DB record.

    Produced by: Job Ingestion Service (manual URL path) and Job Discovery
    Service (automatic path) — both emit this exact shape.
    Consumed by: Job Matching Service; embedded by value in
    JobMatchingState. Payload of JobDiscoveredEvent.
    """

    job_id: JobId
    user_id: UserId
    company: str
    title: str
    location: str | None = None
    description: str
    extracted_skills: list[str] = []
    experience_required: str | None = None
    source_type: JobSourceType
    source_url: str | None = None
    discovered_at: datetime


class JobMatchResult(BaseModel):
    """The output of Job Matching — the wire/event form of JobMatch.

    Produced by: Job Matching Service.
    Consumed by: Tracking Service (always); this exact type is the payload
    of both JobMatchedEvent and JobShortlistedEvent.
    Drops JobMatch.profile_scores relative to the DB record.
    """

    job_match_id: JobMatchId
    job_id: JobId
    user_id: UserId
    selected_profile_id: ProfileId
    selected_resume_id: ResumeId
    match_score: float
    matched_skills: list[str] = []
    missing_skills: list[str] = []
    recommendation: MatchRecommendation
    matched_at: datetime


class ContactCandidate(BaseModel):
    """A single discovered contact, before ranking is finalized. Used
    inside the Contact Discovery LangGraph state between the search and
    rank nodes.

    Produced by: Contact Discovery Service's search_contacts node.
    Consumed by: the same workflow's rank_contacts node. Never crosses a
    component boundary — it is upgraded to Contact + ContactScore
    (persisted) before being published.
    """

    full_name: str
    headline: str | None = None
    company: str
    company_confirmed: bool = True
    """False when `company` was filled in from the target job's company as
    a display fallback (the search provider's own hit carried no parsed
    company — see `hits_to_candidates`), not because this candidate is
    actually confirmed to work there. `rank_contacts` must not treat such a
    candidate as same-company just because the text happens to match.
    Defaults `True` so directly-constructed candidates (tests, and any
    future producer that already verified the company) keep the old
    same-company-eligible behavior.
    """
    contact_type: ContactType
    profile_url: str | None = None
    email: str | None = None
    source: str  # which tool/API found this candidate


class RankedContact(BaseModel):
    contact_id: ContactId
    full_name: str
    headline: str | None = None
    contact_type: ContactType
    profile_url: str | None = None
    relevance_score: float  # 0.0-10.0


class ContactRankingResult(BaseModel):
    """The output of contact discovery+ranking — the wire/event form
    covering both Contact and ContactScore for every ranked contact found
    for a job.

    Produced by: Contact Discovery Service.
    Consumed by: Outreach Service, Tracking Service — payload of
    ContactsFoundEvent.
    """

    job_id: JobId
    user_id: UserId
    contacts: list[RankedContact]  # ordered, highest relevance_score first
    ranked_at: datetime


class OutreachDraft(BaseModel):
    """The generated-but-unapproved message — the wire/event form of
    Outreach at creation time.

    Produced by: Outreach Service.
    Consumed by: Tracking Service — payload of OutreachGeneratedEvent. Also
    read directly (via API, not Kafka) by whatever UI surfaces the human
    approval step.
    """

    outreach_id: OutreachId
    job_id: JobId
    contact_id: ContactId
    user_id: UserId
    channel: OutreachChannel
    draft_message: str
    generated_at: datetime


class ApplicationStatusUpdate(BaseModel):
    """The single event payload type for any Application.status change,
    regardless of which upstream event or user action caused it.

    Produced by: Tracking Service only.
    Consumed by: reserved for future Analytics; currently optional.
    """

    application_id: ApplicationId
    job_id: JobId
    user_id: UserId
    previous_status: ApplicationStatus | None = None  # null for the first status
    new_status: ApplicationStatus
    changed_at: datetime
    triggered_by: str  # component name or "user"


__all__ = [
    "ContactInfo",
    "EducationEntry",
    "WorkExperienceEntry",
    "ProfileMatchScore",
    "WorkflowError",
    "ResumeProfile",
    "NormalizedJob",
    "JobMatchResult",
    "ContactCandidate",
    "RankedContact",
    "ContactRankingResult",
    "OutreachDraft",
    "ApplicationStatusUpdate",
]
