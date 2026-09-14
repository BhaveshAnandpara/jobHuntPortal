"""Composition root for Resume Tailoring Service.

`resume_tailoring.service` is written against `Protocol`s so it has no
compile-time dependency on any other component. This module is where the
real collaborators are bound to them, and it is therefore the one place
this component's cross-component reads are declared and justified:

    matching.repository.JobMatchRepository     -- read the persisted
        `JobMatch` for a job (`match_score`, `matched_skills`,
        `missing_skills`, `selected_profile_id`, and `user_id` for the
        ownership check)
    profiles.repository.CandidateProfileRepository -- read the winning
        `CandidateProfile`
    profiles.service.ProfileService            -- Task 2's transparent
        `contact`/`work_history` backfill
    jobs.repository.JobRepository              -- read the job's own text
    users.repository.UserPreferencesRepository -- read the same
        `UserPreferences` normal matching scores against

Every one of these is **read-only** from this component's side, with one
deliberate exception: `ProfileService.ensure_enriched_profile` updates the
`candidate_profiles` row it was given. That write is Resume/Profile
Service's own, invoked through Resume/Profile Service's own service class —
this component never touches the row itself, and that method exists
precisely so "the first feature that actually needs the fields" can call it
(see its docstring). This is the same shape of narrow, documented exception
to docs/architecture/dependency-graph.md's no-cross-component-import rule
that `infrastructure.llm.user_client` already carries for reading User
Service's config row, and it is taken for the same reason: the alternative
contracts do not carry what this feature needs. In particular
`GET /jobs/{job_id}/matches` returns a `JobMatchResponse`, which
deliberately omits `user_id` — so the HTTP route cannot answer "does this
match belong to the caller?", which is exactly the check that keeps one
user's `JobMatch` out of another user's tailoring request.

The imports are lazy (inside the functions) so importing this module never
requires a configured database, Kafka broker, or storage directory.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.llm import LLMClient
from resume_tailoring.service import ResumeTailoringService


def build_profile_service(session: AsyncSession):
    """Resume/Profile Service's own service object, wired to `session`.

    Only `ensure_enriched_profile` is ever called on it. The `producer` and
    `storage` collaborators are constructed because the constructor
    requires them, not because this path uses them: enrichment publishes no
    event, and it reads a file only in the rare case where a resume row has
    no stored `raw_text` (see `ProfileService.ensure_enriched_profile`).
    Neither construction opens a connection.
    """
    from infrastructure.kafka.producer import EventProducer
    from profiles.events import PRODUCER_NAME
    from profiles.repository import CandidateProfileRepository, ResumeRepository
    from profiles.service import ProfileService
    from profiles.storage import LocalResumeStorage

    return ProfileService(
        ResumeRepository(session),
        CandidateProfileRepository(session),
        producer=EventProducer(PRODUCER_NAME),
        storage=LocalResumeStorage(),
    )


def build_service(session: AsyncSession, llm_client: LLMClient) -> ResumeTailoringService:
    """Assemble a `ResumeTailoringService` bound to one session."""
    from jobs.repository import JobRepository
    from matching.repository import JobMatchRepository
    from profiles.repository import CandidateProfileRepository
    from users.repository import UserPreferencesRepository

    return ResumeTailoringService(
        job_matches=JobMatchRepository(session),
        profiles=CandidateProfileRepository(session),
        jobs=JobRepository(session),
        preferences=UserPreferencesRepository(session),
        profile_service=build_profile_service(session),
        llm_client=llm_client,
    )


__all__ = ["build_profile_service", "build_service"]
