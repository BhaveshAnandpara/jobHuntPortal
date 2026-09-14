"""Transparent re-parse for profiles that predate `contact`/`work_history`
(docs/tailored-resume/TECH.md Task 2).

Runs against a real SQLAlchemy session (SQLite in-memory, `StaticPool` so
two sessions share one database) and the real `CandidateProfileRepository`,
because the point of this feature is that the *row* is updated in place —
an in-memory return value proves nothing. Every assertion about persistence
therefore reloads the profile through a second session opened after a
commit.

The LLM is a `ScriptedLLMProvider` behind a real `LLMClient` (see
conftest.py), so the extraction path exercised here is the production one
minus the network.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from infrastructure.database import Base
from infrastructure.kafka.producer import EventProducer
from infrastructure.llm import LLMClient
from profiles import parsing
from profiles.models import (  # noqa: F401 — registers the owned tables
    CandidateProfileRecord,
    ResumeRecord,
)
from profiles.parsing import ExtractedResumeProfile, profile_needs_enrichment
from profiles.repository import CandidateProfileRepository, ResumeRepository
from profiles.service import ProfileError, ProfileService
from profiles.storage import LocalResumeStorage
from shared.errors.codes import ErrorCode
from shared.types.domain.candidate_profile import CandidateProfile
from shared.types.domain.resume import Resume
from shared.types.dto import ContactInfo, EducationEntry, WorkExperienceEntry
from shared.types.enums import ProfileStatus, ResumeStatus
from shared.types.ids import ProfileId, ResumeId, UserId
from tests.profiles.conftest import FakeProducerClient, llm_response, make_llm_client
from users.models import UserRecord  # noqa: F401 — registers the users FK target

RESUME_TEXT = (
    "Priya Raman\npriya.raman@example.com | +91 98765 43210 | Pune, India\n\n"
    "Shift Supervisor, Meridian Foods, Mar 2019 - Present\n"
    "- Ran a 40-person packing line across two shifts\n"
    "- Cut changeover time by a quarter\n"
)

# The shape the extraction prompt asks for; only `contact`/`work_history`
# are read back by the enrichment path, the rest is deliberately different
# from the stored profile so the test can prove they are NOT written.
EXTRACTED_FIELDS: dict[str, Any] = {
    "title": "Production Supervisor",
    "summary": "A summary the backfill must not write.",
    "contact": {
        "full_name": "Priya Raman",
        "email": "priya.raman@example.com",
        "phone": "+91 98765 43210",
        "location": "Pune, India",
    },
    "skills": ["something the backfill must not write"],
    "experience_years": 99.0,
    "seniority": "Principal",
    "education": [{"institution": "Not this one"}],
    "work_history": [
        {
            "company": "Meridian Foods",
            "title": "Shift Supervisor",
            "start_date": "Mar 2019",
            "end_date": None,
            "bullets": [
                "Ran a 40-person packing line across two shifts",
                "Cut changeover time by a quarter",
            ],
        }
    ],
    "certifications": [],
    "projects": [],
    "industries": [],
    "target_roles": [],
}


# ---------------------------------------------------------------------------
# Database fixtures — one engine, many sessions, so "was the row really
# written" can be answered from a session that never saw the in-memory
# object.
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    db = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with db.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield db
    await db.dispose()


@pytest_asyncio.fixture
async def sessions(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


def _make_service(
    session: AsyncSession,
    *,
    storage: LocalResumeStorage,
    llm_client: LLMClient | None = None,
) -> ProfileService:
    return ProfileService(
        ResumeRepository(session),
        CandidateProfileRepository(session),
        producer=EventProducer("resume-profile-service", client=FakeProducerClient()),
        storage=storage,
        llm_client=llm_client,
    )


async def _seed(
    sessions: async_sessionmaker[AsyncSession],
    *,
    raw_text: str | None = RESUME_TEXT,
    storage_uri: str = "/tmp/does-not-matter.txt",
    contact: ContactInfo | None = None,
    work_history: list[WorkExperienceEntry] | None = None,
) -> CandidateProfile:
    """Insert a resume + its profile and commit, then return the profile.

    Defaults reproduce a pre-feature row exactly: `contact=None`,
    `work_history=[]`, every other field populated the way the original
    parse left it.
    """
    user_id = UserId(uuid4())
    resume = Resume(
        id=ResumeId(uuid4()),
        user_id=user_id,
        file_name="resume.txt",
        storage_uri=storage_uri,
        raw_text=raw_text,
        status=ResumeStatus.PARSED,
        uploaded_at=datetime.now(UTC),
        parsed_at=datetime.now(UTC),
    )
    profile = CandidateProfile(
        id=ProfileId(uuid4()),
        user_id=user_id,
        resume_id=resume.id,
        title="Shift Supervisor",
        summary="Runs a packing line.",
        contact=contact,
        skills=["line management", "scheduling"],
        experience_years=6.0,
        seniority="Mid-level",
        education=[EducationEntry(institution="Pune University")],
        work_history=work_history or [],
        certifications=["Food Safety Level 3"],
        projects=["Changeover reduction"],
        industries=["Food manufacturing"],
        target_roles=["Production Supervisor"],
        status=ProfileStatus.ACTIVE,
        generated_at=datetime.now(UTC),
    )
    async with sessions() as session:
        await ResumeRepository(session).add(resume)
        await CandidateProfileRepository(session).add(profile)
        await session.commit()
    return profile


async def _reload(
    sessions: async_sessionmaker[AsyncSession], profile_id: ProfileId
) -> CandidateProfile:
    async with sessions() as session:
        reloaded = await CandidateProfileRepository(session).get(profile_id)
    assert reloaded is not None
    return reloaded


class _CountingExtractor:
    """Stands in for `parsing.extract_profile_fields`, counting calls.

    The acceptance criterion is about how many times the *extraction* runs,
    not how many rows exist, so it is asserted at the function the feature
    is supposed to avoid re-entering.
    """

    def __init__(self, fields: ExtractedResumeProfile) -> None:
        self.fields = fields
        self.calls = 0
        self.texts: list[str] = []

    def __call__(self, raw_text: str, *, client: LLMClient) -> ExtractedResumeProfile:
        self.calls += 1
        self.texts.append(raw_text)
        return self.fields


@pytest.fixture
def counting_extractor(monkeypatch: pytest.MonkeyPatch) -> _CountingExtractor:
    extractor = _CountingExtractor(ExtractedResumeProfile(**EXTRACTED_FIELDS))
    monkeypatch.setattr(parsing, "extract_profile_fields", extractor)
    return extractor


# ---------------------------------------------------------------------------
# profile_needs_enrichment — the "has this ever been asked for?" predicate
# ---------------------------------------------------------------------------


def _profile(**overrides: Any) -> CandidateProfile:
    defaults: dict[str, Any] = {
        "id": ProfileId(uuid4()),
        "user_id": UserId(uuid4()),
        "resume_id": ResumeId(uuid4()),
        "title": "Nurse Practitioner",
        "status": ProfileStatus.ACTIVE,
        "generated_at": datetime.now(UTC),
    }
    defaults.update(overrides)
    return CandidateProfile(**defaults)


def test_needs_enrichment_when_both_fields_are_empty() -> None:
    assert profile_needs_enrichment(_profile(contact=None, work_history=[])) is True


def test_contact_alone_counts_as_already_enriched() -> None:
    """A one-page bio: contact details, no itemized employment history — and
    an extraction that already looked for the profile links.
    """
    profile = _profile(
        contact=ContactInfo(email="a@example.com", links_checked=True),
        work_history=[],
    )
    assert profile_needs_enrichment(profile) is False


def test_contact_populated_before_links_existed_needs_enrichment() -> None:
    """The real bug this flag exists for.

    A profile parsed after `contact` shipped but before the three link
    fields did has a fully populated contact whose links are `None` purely
    because nothing ever looked. The old `contact is None and not
    work_history` check read that row as finished, so the user's LinkedIn
    and GitHub could never appear on a tailored resume no matter how many
    times they generated one.
    """
    profile = _profile(
        contact=ContactInfo(
            full_name="Priya Raman",
            email="priya.raman@example.com",
            phone="+91 98765 43210",
            location="Pune, India",
        ),
        work_history=[
            WorkExperienceEntry(company="Meridian Foods", title="Supervisor")
        ],
    )

    assert profile.contact is not None
    assert profile.contact.links_checked is False, "the pre-links default"
    assert profile_needs_enrichment(profile) is True


def test_a_checked_contact_with_no_links_at_all_never_re_triggers() -> None:
    """The loop guard. Most resumes print none of the three links, so the
    condition must test the flag, not the values — otherwise every such
    candidate is re-parsed on every single request for the life of the
    profile.
    """
    profile = _profile(
        contact=ContactInfo(
            full_name="Priya Raman",
            email="priya.raman@example.com",
            linkedin_url=None,
            github_url=None,
            portfolio_url=None,
            links_checked=True,
        ),
        work_history=[],
    )

    assert profile_needs_enrichment(profile) is False


def test_an_existing_row_deserializes_as_never_checked() -> None:
    """`contact` is a JSON blob, so no migration adds the flag to rows
    already in the database — they must simply read as `False`, which is the
    honest answer for a row written before anything looked for links.
    """
    stored = {
        "full_name": "Priya Raman",
        "email": "priya.raman@example.com",
        "phone": "+91 98765 43210",
        "location": "Pune, India",
    }

    contact = ContactInfo(**stored)

    assert contact.links_checked is False
    assert profile_needs_enrichment(_profile(contact=contact)) is True


def test_work_history_alone_counts_as_already_enriched() -> None:
    """A CV listing jobs but stating no contact details anywhere."""
    profile = _profile(
        contact=None,
        work_history=[WorkExperienceEntry(company="Meridian Foods", title="Supervisor")],
    )
    assert profile_needs_enrichment(profile) is False


# ---------------------------------------------------------------------------
# ensure_enriched_profile
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pre_feature_profile_is_backfilled_and_persisted(
    sessions: async_sessionmaker[AsyncSession], resume_storage: LocalResumeStorage
) -> None:
    profile = await _seed(sessions)
    client, provider = make_llm_client([llm_response(EXTRACTED_FIELDS)])

    async with sessions() as session:
        service = _make_service(session, storage=resume_storage)
        returned = await service.ensure_enriched_profile(profile, client=client)
        await session.commit()

    assert returned.contact is not None
    assert returned.contact.email == "priya.raman@example.com"
    assert [entry.company for entry in returned.work_history] == ["Meridian Foods"]

    # The row itself — read from a session that never saw the object above.
    reloaded = await _reload(sessions, profile.id)
    assert reloaded.contact is not None
    assert reloaded.contact.full_name == "Priya Raman"
    assert reloaded.contact.phone == "+91 98765 43210"
    assert reloaded.contact.location == "Pune, India"
    assert len(reloaded.work_history) == 1
    entry = reloaded.work_history[0]
    assert entry.company == "Meridian Foods"
    assert entry.title == "Shift Supervisor"
    assert entry.start_date == "Mar 2019"
    assert entry.end_date is None  # current role
    assert entry.bullets == [
        "Ran a 40-person packing line across two shifts",
        "Cut changeover time by a quarter",
    ]

    # The prompt was built from the resume's stored text, not re-extracted
    # from the original file.
    assert RESUME_TEXT.strip()[:20] in provider.requests[0].prompt


@pytest.mark.asyncio
async def test_a_real_run_records_that_links_were_checked_even_with_none_found(
    sessions: async_sessionmaker[AsyncSession], resume_storage: LocalResumeStorage
) -> None:
    """The round trip that closes the loop.

    `EXTRACTED_FIELDS`'s contact prints no LinkedIn, GitHub or portfolio
    link — the common case. The extraction still ran and still looked, so
    the persisted row must say so, and the profile it produces must not ask
    to be enriched a second time.
    """
    profile = await _seed(sessions)
    client, _ = make_llm_client([llm_response(EXTRACTED_FIELDS)])

    async with sessions() as session:
        service = _make_service(session, storage=resume_storage)
        returned = await service.ensure_enriched_profile(profile, client=client)
        await session.commit()

    assert returned.contact is not None
    assert returned.contact.linkedin_url is None
    assert returned.contact.github_url is None
    assert returned.contact.portfolio_url is None
    assert returned.contact.links_checked is True
    assert profile_needs_enrichment(returned) is False

    reloaded = await _reload(sessions, profile.id)
    assert reloaded.contact is not None
    assert reloaded.contact.links_checked is True
    assert profile_needs_enrichment(reloaded) is False


@pytest.mark.asyncio
async def test_a_real_run_records_the_links_it_did_find(
    sessions: async_sessionmaker[AsyncSession], resume_storage: LocalResumeStorage
) -> None:
    """The other half: when the resume does print links, they are persisted
    alongside the same flag — the flag is about whether anything looked, not
    about what was found.
    """
    profile = await _seed(sessions)
    with_links = {
        **EXTRACTED_FIELDS,
        "contact": {
            **EXTRACTED_FIELDS["contact"],
            "linkedin_url": "linkedin.com/in/priya-raman",
            "github_url": "github.com/priyaraman",
        },
    }
    client, _ = make_llm_client([llm_response(with_links)])

    async with sessions() as session:
        service = _make_service(session, storage=resume_storage)
        await service.ensure_enriched_profile(profile, client=client)
        await session.commit()

    reloaded = await _reload(sessions, profile.id)
    assert reloaded.contact is not None
    assert reloaded.contact.linkedin_url == "linkedin.com/in/priya-raman"
    assert reloaded.contact.github_url == "github.com/priyaraman"
    assert reloaded.contact.links_checked is True


@pytest.mark.asyncio
async def test_backfill_touches_no_other_field_and_creates_no_second_row(
    sessions: async_sessionmaker[AsyncSession], resume_storage: LocalResumeStorage
) -> None:
    """Only `contact`/`work_history` move. The scripted extraction returns a
    different title/summary/skills/education on purpose — if the backfill
    wrote the whole profile, these assertions would fail.
    """
    profile = await _seed(sessions)
    client, _ = make_llm_client([llm_response(EXTRACTED_FIELDS)])

    async with sessions() as session:
        service = _make_service(session, storage=resume_storage)
        await service.ensure_enriched_profile(profile, client=client)
        await session.commit()

    reloaded = await _reload(sessions, profile.id)
    assert reloaded.id == profile.id
    assert reloaded.title == "Shift Supervisor"
    assert reloaded.summary == "Runs a packing line."
    assert reloaded.skills == ["line management", "scheduling"]
    assert reloaded.experience_years == 6.0
    assert reloaded.seniority == "Mid-level"
    assert [e.institution for e in reloaded.education] == ["Pune University"]
    assert reloaded.certifications == ["Food Safety Level 3"]
    assert reloaded.projects == ["Changeover reduction"]
    assert reloaded.industries == ["Food manufacturing"]
    assert reloaded.target_roles == ["Production Supervisor"]
    assert reloaded.status is ProfileStatus.ACTIVE
    # SQLite drops the tzinfo SQLAlchemy's `DateTime(timezone=True)` carries
    # on PostgreSQL, so compare the instants, not the tzinfo — the point is
    # that the backfill left the timestamp alone.
    assert reloaded.generated_at.replace(tzinfo=None) == profile.generated_at.replace(
        tzinfo=None
    )

    async with sessions() as session:
        rows = await CandidateProfileRepository(session).list_for_user(profile.user_id)
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_re_parse_happens_at_most_once_per_resume(
    sessions: async_sessionmaker[AsyncSession],
    resume_storage: LocalResumeStorage,
    counting_extractor: _CountingExtractor,
) -> None:
    """The persisted enrichment is the memo: a second call against the now-
    enriched profile returns the same data without a second extraction.
    """
    profile = await _seed(sessions)
    client, _ = make_llm_client([])  # would raise if a request were sent

    async with sessions() as session:
        service = _make_service(session, storage=resume_storage)
        first = await service.ensure_enriched_profile(profile, client=client)
        await session.commit()

    assert counting_extractor.calls == 1

    async with sessions() as session:
        service = _make_service(session, storage=resume_storage)
        second = await service.ensure_enriched_profile(first, client=client)
        await session.commit()

    assert counting_extractor.calls == 1
    assert second.contact == first.contact
    assert second.work_history == first.work_history
    assert second.id == first.id


@pytest.mark.asyncio
async def test_already_enriched_profile_is_not_re_parsed(
    sessions: async_sessionmaker[AsyncSession],
    resume_storage: LocalResumeStorage,
    counting_extractor: _CountingExtractor,
) -> None:
    """A genuinely contactless resume — work history, `contact=None` — was
    already parsed correctly and must not be re-run on every request.
    """
    existing = [
        WorkExperienceEntry(
            company="Northwind Clinic",
            title="Staff Nurse",
            start_date="2017",
            end_date="2021",
            bullets=["Triage for a 20-bed ward"],
        )
    ]
    profile = await _seed(sessions, contact=None, work_history=existing)
    client, _ = make_llm_client([])

    async with sessions() as session:
        service = _make_service(session, storage=resume_storage)
        returned = await service.ensure_enriched_profile(profile, client=client)

    assert counting_extractor.calls == 0
    assert returned is profile
    assert returned.contact is None
    assert returned.work_history == existing


@pytest.mark.asyncio
async def test_profile_with_contact_but_no_work_history_is_not_re_parsed(
    sessions: async_sessionmaker[AsyncSession],
    resume_storage: LocalResumeStorage,
    counting_extractor: _CountingExtractor,
) -> None:
    profile = await _seed(
        sessions,
        contact=ContactInfo(full_name="Priya Raman", links_checked=True),
        work_history=[],
    )
    client, _ = make_llm_client([])

    async with sessions() as session:
        service = _make_service(session, storage=resume_storage)
        returned = await service.ensure_enriched_profile(profile, client=client)

    assert counting_extractor.calls == 0
    assert returned is profile


@pytest.mark.asyncio
async def test_falls_back_to_the_original_file_when_raw_text_is_missing(
    sessions: async_sessionmaker[AsyncSession],
    resume_storage: LocalResumeStorage,
    counting_extractor: _CountingExtractor,
) -> None:
    storage_uri = resume_storage.save(
        uuid4(), uuid4(), "resume.txt", RESUME_TEXT.encode("utf-8")
    )
    profile = await _seed(sessions, raw_text=None, storage_uri=storage_uri)
    client, _ = make_llm_client([])

    async with sessions() as session:
        service = _make_service(session, storage=resume_storage)
        await service.ensure_enriched_profile(profile, client=client)
        await session.commit()

    assert counting_extractor.calls == 1
    assert "Priya Raman" in counting_extractor.texts[0]
    reloaded = await _reload(sessions, profile.id)
    assert reloaded.contact is not None


@pytest.mark.asyncio
async def test_uses_the_services_own_client_when_none_is_supplied(
    sessions: async_sessionmaker[AsyncSession],
    resume_storage: LocalResumeStorage,
    counting_extractor: _CountingExtractor,
) -> None:
    profile = await _seed(sessions)
    client, _ = make_llm_client([])

    async with sessions() as session:
        service = _make_service(session, storage=resume_storage, llm_client=client)
        await service.ensure_enriched_profile(profile)
        await session.commit()

    assert counting_extractor.calls == 1
    assert await _reload(sessions, profile.id) is not None


@pytest.mark.asyncio
async def test_no_client_anywhere_raises_llm_provider_error(
    sessions: async_sessionmaker[AsyncSession], resume_storage: LocalResumeStorage
) -> None:
    profile = await _seed(sessions)

    async with sessions() as session:
        service = _make_service(session, storage=resume_storage)
        with pytest.raises(ProfileError) as exc_info:
            await service.ensure_enriched_profile(profile)

    assert exc_info.value.code is ErrorCode.LLM_PROVIDER_ERROR


@pytest.mark.asyncio
async def test_missing_resume_raises_not_found(
    sessions: async_sessionmaker[AsyncSession], resume_storage: LocalResumeStorage
) -> None:
    orphan = _profile(contact=None, work_history=[])
    client, _ = make_llm_client([])

    async with sessions() as session:
        service = _make_service(session, storage=resume_storage)
        with pytest.raises(ProfileError) as exc_info:
            await service.ensure_enriched_profile(orphan, client=client)

    assert exc_info.value.code is ErrorCode.NOT_FOUND


@pytest.mark.asyncio
async def test_extraction_failure_surfaces_as_a_profile_error(
    sessions: async_sessionmaker[AsyncSession],
    resume_storage: LocalResumeStorage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The caller must learn the backfill failed rather than proceed with
    silently un-enriched data.
    """
    profile = await _seed(sessions)

    def _boom(raw_text: str, *, client: LLMClient) -> ExtractedResumeProfile:
        raise parsing.ResumeParsingError(ErrorCode.LLM_PROVIDER_ERROR, "provider down")

    monkeypatch.setattr(parsing, "extract_profile_fields", _boom)
    client, _ = make_llm_client([])

    async with sessions() as session:
        service = _make_service(session, storage=resume_storage)
        with pytest.raises(ProfileError) as exc_info:
            await service.ensure_enriched_profile(profile, client=client)

    assert exc_info.value.code is ErrorCode.LLM_PROVIDER_ERROR
    reloaded = await _reload(sessions, profile.id)
    assert reloaded.contact is None
    assert reloaded.work_history == []
