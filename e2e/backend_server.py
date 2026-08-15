"""E2E-support infrastructure — NOT part of the backend, NOT used by pytest.

What this is
-------------
A standalone script that boots a **real** `uvicorn` HTTP server, on
`127.0.0.1:8000` (matching the frontend's default `VITE_API_BASE_URL`, see
`frontend/.env.example`), serving the real `api.main.app` FastAPI
application, wired to a fully in-process/fake backend exactly the way
`tests/integration/conftest.py`'s `harness` fixture wires it for pytest —
one shared in-memory SQLite database, one shared `InMemoryBroker`, every
component's DB/producer/LLM/external-client DI seam pointed at fakes — but
with **no pytest test body driving it**. Instead, a background asyncio task
continuously drains the in-memory Kafka broker on a timer
(`drain_chain`, imported unmodified from `tests/integration/conftest.py`),
so the asynchronous jobs.discovered -> ... -> outreach.sent pipeline
actually advances on its own, the same way real Kafka consumers would in a
real deployment — just fully deterministic and fake underneath.

This lets Playwright drive the **real browser** against the **real running
FastAPI app** over real HTTP, and see the backend's async pipeline actually
progress in response to real `POST`/`GET` calls the browser makes, without
requiring real Ollama/Kafka/Postgres/LinkedIn/email infrastructure.

Who owns this file
-------------------
`frontend-integration-ui-agent` (Playwright/E2E owner). Lives outside both
`src/` and `tests/` on purpose, so it's unambiguous this is a new file, not
a modification to backend source or backend tests — see this agent's Must
Not list ("Do NOT modify backend files under any circumstance"). It is
never imported by `pytest` and `pytest` never imports it.

It *does* import reusable fakes/helpers from `tests.integration.conftest`,
`tests.contacts.conftest`, `tests.jobs.conftest` — importing existing test
doubles is not modifying them, per the same brief that created this file.

How it's invoked
------------------
`frontend/playwright.config.ts`'s `webServer` array runs
`python ../e2e/backend_server.py` (cwd `frontend/`) alongside the existing
Vite dev server entry, health-checked against `/openapi.json` (a stock
FastAPI endpoint, so no new backend route was added for this).

Fixture data
-------------
Every LLM/external-client fake below is scripted **up front**, keyed by a
stable substring of the rendered prompt (mirroring the exact technique
`tests/matching/conftest.py`'s/`tests/contacts/conftest.py`'s/
`tests/outreach/conftest.py`'s own `FakeLLMClient` classes already use —
"a substring that must appear in the rendered prompt"), for a fixed,
known-in-advance set of three resume profiles (software / mechanical / HR)
and three job postings (one aligned to each profession) — see
`FIXTURES` below and `frontend/tests/e2e/fixtures.ts`, which mirrors these
same constants for the TypeScript specs. Nothing here is scored, ranked, or
selected by this script — every score/ranking/selection is still produced
by the real (fake-LLM-backed) LangGraph workflows, exactly as in
production; this script only supplies deterministic LLM *answers*.

Two categories of DI seam are wired below, mirroring
`tests/integration/conftest.py`'s own two categories:
  - Kafka *consumer*-side DB/producer/LLM/external-client access -> each
    component's own `set_*` test seams (`matching.db.set_session_factory`,
    `workflows.langgraph.job_matching.nodes.set_llm_client`, etc.).
  - API-layer DB access -> FastAPI `app.dependency_overrides[...]`.

One deliberate difference from the pytest harness: `tests/integration/
conftest.py`'s `sync_matching_profiles`/`sync_outreach_clients` build a
*snapshot* fake from a real API response, at exactly the right test-body
moment. There is no test body here driving individual steps — the
background drain loop just keeps calling `drain_chain` on a timer for the
server's whole lifetime, so those synchronous snapshots aren't available.
Instead, this script uses small **"Live"** client classes (`LiveProfile
ServiceClient`, `LiveUserPreferencesClient`, `LiveJobMatchClient`, `Live
JobIngestionClient`) that call the real HTTP API in-process (via
`httpx.ASGITransport`, no real socket) on every invocation, so they always
see current DB state regardless of when a consumer handler fires. This is
arguably closer to how a real deployment's `ProfileServiceClient` etc.
would behave (a real HTTP call to another service, made fresh every time)
than a static snapshot would be.

Known non-invasive gap fix: `src/api/main.py` does not add CORS middleware
(there is no cross-origin browser client in the backend's own test suite,
so this was never exercised). A real browser on `http://localhost:5173`
calling `http://localhost:8000` needs CORS response headers or the fetch
is blocked entirely by the browser. Rather than modify backend source
(forbidden — see this file's header), this script calls
`fastapi_app.add_middleware(CORSMiddleware, ...)` on the *imported* `app`
object from this script, before starting uvicorn — the same "configure the
shared app object from outside its own module" technique
`tests/integration/conftest.py` already uses for `app.dependency_overrides`.
This affects only this script's own process; `src/api/main.py` on disk is
untouched. Flagged in the final report as a real gap worth a small, real
backend fix (e.g. env-gated CORS) if this app is ever deployed with the
frontend on a different origin than the backend.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import sys
import tempfile
from pathlib import Path
from typing import Annotated

import httpx
import uvicorn
from fastapi import Depends
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Make the backend's `src/` importable exactly like pytest's `pyproject.toml`
# `[tool.pytest.ini_options] pythonpath`/rootdir configuration does, since
# this script is run directly with `python`, not via pytest.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC = _REPO_ROOT / "src"
for _path in (str(_REPO_ROOT), str(_SRC)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

# Deterministic, e2e-only JWT secret — this process never shares state with
# a real deployment, so a fixed value is fine (never the same value used
# anywhere real). Set before any `infrastructure.auth` call reads it (that
# module resolves the env var lazily, per-call, but setting it once up front
# here avoids ever depending on import order). The real frontend, driven by
# Playwright, authenticates exactly like a real browser would (register/
# login through the actual UI) — this only covers the "Live" clients below,
# which make privileged server-to-server calls with no browser session of
# their own to inherit a token from.
os.environ.setdefault("JWT_SECRET_KEY", "e2e-test-only-secret-do-not-use-in-prod")

import contacts.consumers as contacts_consumers
import contacts.db as contacts_db
import contacts.events as contacts_events
import contacts.models  # noqa: F401 - registers tables on Base.metadata
import jobs.models  # noqa: F401
import matching.consumers as matching_consumers
import matching.db as matching_db
import matching.events as matching_events
import matching.models  # noqa: F401
import outreach.consumers as outreach_consumers
import outreach.db as outreach_db
import outreach.events as outreach_events
import outreach.models  # noqa: F401
import profiles.models  # noqa: F401
import tracking.consumers as tracking_consumers  # noqa: F401 - imported for parity/clarity
import tracking.db as tracking_db
import tracking.events as tracking_events
import tracking.models  # noqa: F401
import users.models  # noqa: F401
import workflows.langgraph.contact_discovery.nodes as contact_nodes
import workflows.langgraph.job_matching.nodes as matching_nodes
import workflows.langgraph.outreach_generation.nodes as outreach_nodes
from api.main import app as fastapi_app
from contacts.api.dependencies import get_session as contacts_get_session
from infrastructure.auth import create_access_token
from infrastructure.database import Base
from infrastructure.external.errors import PeopleSearchRequestError
from infrastructure.external.people_search import PersonSearchHit
from infrastructure.kafka.in_memory import InMemoryBroker, InMemoryProducerClient
from infrastructure.kafka.producer import EventProducer
from infrastructure.llm.errors import LLMFailureReason, LLMProviderError
from jobs.ingestion.dependencies import get_db_session as jobs_get_db_session
from jobs.ingestion.dependencies import get_event_producer as jobs_get_event_producer
from jobs.ingestion.dependencies import get_page_fetcher as jobs_get_page_fetcher
from jobs.ingestion.dependencies import (
    get_structured_extractor as jobs_get_structured_extractor,
)
from matching.api.dependencies import get_session as matching_get_session
from outreach.api.dependencies import get_session as outreach_get_session
from profiles.api.dependencies import (
    get_profile_service as profiles_get_profile_service,
)
from profiles.api.dependencies import get_session as profiles_get_session
from profiles.repository import CandidateProfileRepository, ResumeRepository
from profiles.service import ProfileService
from profiles.storage import LocalResumeStorage
from shared.types.api.jobs import JobResponse
from shared.types.api.matching import JobMatchResponse
from shared.types.domain.user_preferences import UserPreferences
from shared.types.dto import ResumeProfile
from shared.types.enums import ContactType

# Reused fakes/helpers from the backend's own pytest suite (importing, not
# modifying — see this file's header).
from tests.contacts.conftest import FakeLLMClient as ContactsFakeLLMClient
from tests.integration.conftest import (
    CHAIN_ORDER,
    drain_chain,
)
from tests.jobs.conftest import FakeExtractor, FakePageFetcher, make_extracted_fields
from tests.outreach.conftest import FakeLLMClient as OutreachFakeLLMClient
from tracking.api.dependencies import get_session as tracking_get_session
from users.api.dependencies import get_session as users_get_session
from workflows.langgraph.contact_discovery.discovery import (
    ContactClassificationBatch,
    ContactSearchPlan,
    HitClassification,
    RelevanceSignals,
)
from workflows.langgraph.job_matching.scoring import ProfileScoringOutput
from workflows.langgraph.outreach_generation.generation import OutreachDraftContent

logger = logging.getLogger("e2e.backend_server")
logging.basicConfig(level=logging.INFO, format="[e2e-backend] %(message)s")

HOST = "127.0.0.1"
PORT = 8000
DRAIN_INTERVAL_SECONDS = 0.25

# ---------------------------------------------------------------------------
# Fixture data — kept in sync BY HAND with frontend/tests/e2e/fixtures.ts.
# Three professions (about_project.md's own worked examples): software,
# mechanical engineering, HR. See this file's header for why every value
# below is decided up front rather than scripted per-request.
# ---------------------------------------------------------------------------

PROFILE_A_TITLE = "Senior Backend Engineer (Python/FastAPI)"
PROFILE_B_TITLE = "Senior Mechanical Design Engineer (CAD/SolidWorks)"
PROFILE_C_TITLE = "HR Business Partner (Talent Acquisition)"

RESUME_A_NAME = "Alex Morgan"
RESUME_B_NAME = "Jordan Rivera"
RESUME_C_NAME = "Taylor Chen"

RESUME_A_TEXT = (
    f"{RESUME_A_NAME}. {PROFILE_A_TITLE}. 8 years building distributed "
    "backend systems in Python. Skills: Python, FastAPI, Kafka, "
    "PostgreSQL, Docker, REST APIs. Built an event-driven order "
    "processing platform handling millions of events per day."
)
RESUME_B_TEXT = (
    f"{RESUME_B_NAME}. {PROFILE_B_TITLE}. 7 years designing mechanical "
    "subsystems for industrial robots. Skills: CAD, SolidWorks, GD&T, "
    "DFM, tolerance stack-up analysis. Led design reviews and prototype "
    "validation for three product launches."
)
RESUME_C_TEXT = (
    f"{RESUME_C_NAME}. {PROFILE_C_TITLE}. 6 years in talent acquisition "
    "and HR business partnering. Skills: Talent Acquisition, Employee "
    "Relations, HRIS, Onboarding, Performance Management. Redesigned a "
    "company-wide onboarding program adopted across five departments."
)
RESUME_FAIL_TRIGGER = "TRIGGER_PARSE_FAILURE"
RESUME_FAIL_TEXT = (
    f"{RESUME_FAIL_TRIGGER}. This resume is deliberately unparseable for "
    "E2E replace-failure-safety testing."
)

_EXTRACTED_A = {
    "title": PROFILE_A_TITLE,
    "summary": "Backend engineer with 8 years building distributed systems in Python.",
    "skills": ["Python", "FastAPI", "Kafka", "PostgreSQL", "Docker", "REST APIs"],
    "experience_years": 8.0,
    "seniority": "Senior",
    "education": [],
    "certifications": [],
    "projects": ["Event-driven order processing platform"],
    "industries": ["Software"],
    "target_roles": ["Backend Engineer", "Software Engineer"],
}
_EXTRACTED_B = {
    "title": PROFILE_B_TITLE,
    "summary": "Mechanical engineer with 7 years designing robotic subsystems.",
    "skills": ["CAD", "SolidWorks", "GD&T", "DFM", "Tolerance Stack-up"],
    "experience_years": 7.0,
    "seniority": "Senior",
    "education": [],
    "certifications": [],
    "projects": ["Industrial robot arm redesign"],
    "industries": ["Robotics"],
    "target_roles": ["Mechanical Design Engineer"],
}
_EXTRACTED_C = {
    "title": PROFILE_C_TITLE,
    "summary": "HR professional with 6 years in talent acquisition and business partnering.",
    "skills": ["Talent Acquisition", "Employee Relations", "HRIS", "Onboarding"],
    "experience_years": 6.0,
    "seniority": "Senior",
    "education": [],
    "certifications": [],
    "projects": ["Company-wide onboarding program redesign"],
    "industries": ["Human Resources"],
    "target_roles": ["HR Business Partner", "Talent Acquisition Lead"],
}

# Job postings — one aligned to each profession. Titles deliberately worded
# differently from the profile titles above so the AND-pair matching-LLM
# markers (job title + profile title, both required) never collide with an
# unintended (job, profile) pair.
JOB1_URL = "https://boards.example.com/jobs/e2e-mech-design-1"
JOB1_COMPANY = "Acme Robotics"
JOB1_TITLE = "Mechanical Design Engineer II - Robotics Platform"
JOB1_PAGE_TEXT = (
    f"{JOB1_TITLE} at {JOB1_COMPANY}. Remote. Own CAD models from concept "
    "through DFM/DFA review, run tolerance stack-ups, and validate designs "
    "with prototype testing. Requires SolidWorks, GD&T, DFM."
)

JOB2_URL = "https://boards.example.com/jobs/e2e-backend-swe-1"
JOB2_COMPANY = "Initech Systems"
JOB2_TITLE = "Backend Software Engineer - Core Services"
JOB2_PAGE_TEXT = (
    f"{JOB2_TITLE} at {JOB2_COMPANY}. Remote. Build and operate "
    "event-driven backend services in Python with Kafka and PostgreSQL. "
    "Requires Python, FastAPI, Kafka, REST APIs."
)

JOB3_URL = "https://boards.example.com/jobs/e2e-hr-bp-1"
JOB3_COMPANY = "Globex People Ops"
JOB3_TITLE = "HR Business Partner - Global People Team"
JOB3_PAGE_TEXT = (
    f"{JOB3_TITLE} at {JOB3_COMPANY}. Hybrid. Partner with department "
    "leaders on talent acquisition, employee relations, and onboarding "
    "programs. Requires Talent Acquisition, Employee Relations, HRIS."
)

# A URL registered to always fail the page-fetch step (JOB_FETCH_FAILED) —
# for error-recovery.spec.ts's "ingestion failure" scenario. Any *other*
# well-formed http(s) URL not registered below cleanly 400s as
# INVALID_JOB_URL (FakePageFetcher returns "" for an unknown URL, and
# jobs.ingestion.extraction.extract_job_from_url raises INVALID_JOB_URL for
# empty page content) — no special-casing needed for "unknown URL".
JOB_FETCH_FAIL_URL = "https://boards.example.com/jobs/e2e-fetch-fail"

CONTACT_MECH_NAME = "Sam Lee"
CONTACT_SWE_NAME = "Riley Chen"
CONTACT_HR_NAME = "Morgan Blake"

# A company with zero people-search hits — contacts.spec.ts's empty-state
# scenario. A distinct company that raises PeopleSearchRequestError — the
# contact-search-failure scenario. Both jobs below reuse profile B's
# alignment (mechanical) purely so they clear the SHORTLIST gate and reach
# contact search at all — the profession isn't the point of these two
# fixtures, the contacts-panel behavior is.
JOB4_URL = "https://boards.example.com/jobs/e2e-empty-contacts"
JOB4_COMPANY = "Quiet Startup Co"
JOB4_TITLE = "Mechanical Design Engineer - Quiet Startup"
JOB4_PAGE_TEXT = (
    f"{JOB4_TITLE} at {JOB4_COMPANY}. Remote. Own CAD models from concept "
    "through DFM/DFA review. Requires SolidWorks, GD&T, DFM."
)

JOB5_URL = "https://boards.example.com/jobs/e2e-contact-search-fail"
JOB5_COMPANY = "Search Fail Inc"
JOB5_TITLE = "Mechanical Design Engineer - Search Fail"
JOB5_PAGE_TEXT = (
    f"{JOB5_TITLE} at {JOB5_COMPANY}. Remote. Own CAD models from concept "
    "through DFM/DFA review. Requires SolidWorks, GD&T, DFM."
)

EMPTY_CONTACTS_COMPANY = JOB4_COMPANY
CONTACT_SEARCH_FAIL_COMPANY = JOB5_COMPANY


def _extracted_job(company: str, title: str, page_text: str, skills: list[str], experience: str):
    return make_extracted_fields(
        company=company,
        title=title,
        location="Remote",
        description=page_text,
        extracted_skills=skills,
        experience_required=experience,
    )


JOB_PAGES = {
    JOB1_URL: JOB1_PAGE_TEXT,
    JOB2_URL: JOB2_PAGE_TEXT,
    JOB3_URL: JOB3_PAGE_TEXT,
    JOB4_URL: JOB4_PAGE_TEXT,
    JOB5_URL: JOB5_PAGE_TEXT,
}
JOB_EXTRACTOR_BY_CONTENT = {
    JOB1_PAGE_TEXT: _extracted_job(
        JOB1_COMPANY, JOB1_TITLE, JOB1_PAGE_TEXT, ["CAD", "SolidWorks", "GD&T", "DFM"], "5+ years"
    ),
    JOB2_PAGE_TEXT: _extracted_job(
        JOB2_COMPANY,
        JOB2_TITLE,
        JOB2_PAGE_TEXT,
        ["Python", "FastAPI", "Kafka", "PostgreSQL"],
        "5+ years",
    ),
    JOB3_PAGE_TEXT: _extracted_job(
        JOB3_COMPANY,
        JOB3_TITLE,
        JOB3_PAGE_TEXT,
        ["Talent Acquisition", "Employee Relations", "HRIS"],
        "4+ years",
    ),
    JOB4_PAGE_TEXT: _extracted_job(
        JOB4_COMPANY, JOB4_TITLE, JOB4_PAGE_TEXT, ["CAD", "SolidWorks", "GD&T", "DFM"], "5+ years"
    ),
    JOB5_PAGE_TEXT: _extracted_job(
        JOB5_COMPANY, JOB5_TITLE, JOB5_PAGE_TEXT, ["CAD", "SolidWorks", "GD&T", "DFM"], "5+ years"
    ),
}
JOB_FETCH_ERRORS = {
    JOB_FETCH_FAIL_URL: ConnectionError("simulated E2E page-fetch failure"),
}


# ---------------------------------------------------------------------------
# Resume-parsing LLM fake (profiles.service.ProfileService.llm_client) —
# duck-typed `.complete_structured(prompt, schema, *, system=None,
# options=None)`, matched by substring exactly like the reused
# matching/contacts/outreach FakeLLMClient classes. Deliberately NOT the
# pytest suite's `ScriptedLLMProvider` (tests/profiles/conftest.py), which
# pops an ordered list per call — unsuitable here since this server may
# process multiple concurrent Playwright specs' resume uploads in any
# order. Raises a real `LLMProviderError` on the failure trigger so
# `profiles.parsing.extract_profile_fields` maps it to `ResumeParsingError`
# and the resume correctly reaches `PARSE_FAILED` (not an unhandled
# exception in a FastAPI BackgroundTask, which would leave it stuck in
# PARSING forever).
# ---------------------------------------------------------------------------


class ResumeExtractionLLM:
    """Keyed by a substring of the rendered prompt — the plain decoded
    resume text, matching what `profiles.parsing._build_extraction_prompt`
    actually embeds in the LLM prompt (`ProfileService.upload_resume` now
    correctly `base64.b64decode`s `CreateResumeRequest.file_content` before
    it ever reaches parsing — see the note below this class for the
    now-fixed history)."""

    def __init__(self, by_marker: dict[str, dict], fail_trigger: str) -> None:
        self._by_marker = by_marker
        self._fail_trigger = fail_trigger

    def complete_structured(self, prompt, schema, *, system=None, options=None):
        if self._fail_trigger in prompt:
            raise LLMProviderError(
                LLMFailureReason.PROVIDER_ERROR,
                "simulated E2E resume-parsing failure",
                provider="e2e-fake",
                model="e2e-fake",
            )
        for marker, fields in self._by_marker.items():
            if marker in prompt:
                return schema(**fields)
        raise AssertionError(
            f"ResumeExtractionLLM has no scripted result for prompt: {prompt[:400]!r}"
        )


# ---------------------------------------------------------------------------
# FIXED UPSTREAM IN STEP 12 (was: "KNOWN, REPORTED, UNFIXED BACKEND
# DEFECT"):
#
# `CreateResumeRequest.file_content` was previously typed `bytes`, and
# pydantic's default `bytes`-from-JSON-string coercion never base64-decoded
# it — it just UTF-8-encoded the JSON string's own characters. That meant
# every resume's stored `raw_text` was literally the base64 string itself,
# not the decoded original text, so this harness's scripted LLM had to key
# on the base64-encoded form of each fixture resume to match what the
# prompt actually contained.
#
# profile-agent fixed this at the source in Step 12:
# `CreateResumeRequest.file_content` is now `str`, and
# `ProfileService.upload_resume` (src/profiles/service.py) explicitly does
# `base64.b64decode(request.file_content, validate=True)` before storing
# the file, with malformed base64 raising a normalized VALIDATION_ERROR.
# `profiles.parsing._build_extraction_prompt` therefore now embeds the real
# decoded resume text in the LLM prompt, matching real production behavior
# (and matching the wire contract, which never changed — only server-side
# decoding was broken and is now fixed). This harness now keys on the plain
# decoded text below, matching that real behavior.
# ---------------------------------------------------------------------------

RESUME_LLM = ResumeExtractionLLM(
    by_marker={
        RESUME_A_TEXT: _EXTRACTED_A,
        RESUME_B_TEXT: _EXTRACTED_B,
        RESUME_C_TEXT: _EXTRACTED_C,
    },
    fail_trigger=RESUME_FAIL_TEXT,
)


# ---------------------------------------------------------------------------
# Matching LLM fake — AND-pair keyed (job title marker AND profile title
# marker both present in the rendered prompt), since a flat single-substring
# key (as the reused MatchingFakeLLMClient supports) cannot distinguish
# "profile A scored against its aligned job" from "profile A scored against
# a misaligned job" — see this file's header for the full reasoning. Falls
# back to a low, non-shortlisting default for every other (job, profile)
# combination.
# ---------------------------------------------------------------------------


class PairScoringLLM:
    def __init__(self, rules: list[tuple[str, str, ProfileScoringOutput]], default: ProfileScoringOutput):
        self._rules = rules
        self._default = default

    def complete_structured(self, prompt, schema, *, system=None, options=None):
        for job_marker, profile_marker, output in self._rules:
            if job_marker in prompt and profile_marker in prompt:
                return output
        return self._default


def _high_score(matched: list[str], missing: list[str]) -> ProfileScoringOutput:
    return ProfileScoringOutput(
        role_relevance=0.95,
        skills_fit=0.93,
        experience_fit=0.9,
        domain_fit=0.9,
        seniority_fit=0.9,
        location_preference_fit=0.5,
        overall_score=0.93,
        matched_skills=matched,
        missing_skills=missing,
        reasoning="Strong fit for this specific opportunity.",
    )


MATCHING_LLM = PairScoringLLM(
    rules=[
        (JOB1_TITLE, PROFILE_B_TITLE, _high_score(["CAD", "SolidWorks", "GD&T", "DFM"], [])),
        (JOB2_TITLE, PROFILE_A_TITLE, _high_score(["Python", "FastAPI", "Kafka"], [])),
        (JOB3_TITLE, PROFILE_C_TITLE, _high_score(["Talent Acquisition", "Employee Relations"], [])),
        (JOB4_TITLE, PROFILE_B_TITLE, _high_score(["CAD", "SolidWorks", "GD&T", "DFM"], [])),
        (JOB5_TITLE, PROFILE_B_TITLE, _high_score(["CAD", "SolidWorks", "GD&T", "DFM"], [])),
    ],
    default=ProfileScoringOutput(
        role_relevance=0.3,
        skills_fit=0.25,
        experience_fit=0.4,
        domain_fit=0.2,
        seniority_fit=0.5,
        location_preference_fit=0.5,
        overall_score=0.3,
        matched_skills=[],
        missing_skills=[],
        reasoning="Limited overlap between this profile and this opportunity.",
    ),
)


# ---------------------------------------------------------------------------
# Contact discovery fakes.
# ---------------------------------------------------------------------------


class CompanyScriptedPeopleSearchClient:
    """`PeopleSearchClient`-shaped fake (duck-typed `.search(query)`), keyed
    by `query.company` exactly (not substring) — real company names, no
    ambiguity risk.
    """

    def __init__(
        self,
        hits_by_company: dict[str, list[PersonSearchHit]],
        errors_by_company: dict[str, Exception] | None = None,
    ) -> None:
        self._hits_by_company = hits_by_company
        self._errors_by_company = errors_by_company or {}
        self.queries: list[object] = []

    async def search(self, query):
        self.queries.append(query)
        if query.company in self._errors_by_company:
            raise self._errors_by_company[query.company]
        return list(self._hits_by_company.get(query.company, []))


CONTACTS_PEOPLE_SEARCH = CompanyScriptedPeopleSearchClient(
    hits_by_company={
        JOB1_COMPANY: [
            PersonSearchHit(
                full_name=CONTACT_MECH_NAME,
                provider="e2e-static",
                headline=f"Engineering Manager, Mechanical at {JOB1_COMPANY}",
                company=JOB1_COMPANY,
                profile_url="https://example.com/in/sam-lee-e2e",
            )
        ],
        JOB2_COMPANY: [
            PersonSearchHit(
                full_name=CONTACT_SWE_NAME,
                provider="e2e-static",
                headline=f"Engineering Manager, Backend at {JOB2_COMPANY}",
                company=JOB2_COMPANY,
                profile_url="https://example.com/in/riley-chen-e2e",
            )
        ],
        JOB3_COMPANY: [
            PersonSearchHit(
                full_name=CONTACT_HR_NAME,
                provider="e2e-static",
                headline=f"Director of Talent Acquisition at {JOB3_COMPANY}",
                company=JOB3_COMPANY,
                profile_url="https://example.com/in/morgan-blake-e2e",
            )
        ],
        EMPTY_CONTACTS_COMPANY: [],
    },
    errors_by_company={
        CONTACT_SEARCH_FAIL_COMPANY: PeopleSearchRequestError(
            "simulated E2E people-search failure", provider="e2e-static"
        ),
    },
)

CONTACTS_LLM = ContactsFakeLLMClient(
    results={
        "Propose 3-6 short search": ContactSearchPlan(
            role_keywords=["Team Lead", "Hiring Manager", "Recruiter"]
        ),
        "Classify each of the following": ContactClassificationBatch(
            classifications=[HitClassification(index=0, contact_type=ContactType.HIRING_MANAGER)]
        ),
        CONTACT_MECH_NAME: RelevanceSignals(
            role_similarity=0.9, department_relevance=0.9, seniority_fit=0.8
        ),
        CONTACT_SWE_NAME: RelevanceSignals(
            role_similarity=0.92, department_relevance=0.88, seniority_fit=0.8
        ),
        CONTACT_HR_NAME: RelevanceSignals(
            role_similarity=0.9, department_relevance=0.85, seniority_fit=0.75
        ),
    }
)


# ---------------------------------------------------------------------------
# Outreach generation LLM fake.
# ---------------------------------------------------------------------------

OUTREACH_LLM = OutreachFakeLLMClient(
    results={
        CONTACT_MECH_NAME: OutreachDraftContent(
            body=(
                f"Hi {CONTACT_MECH_NAME}, I saw the {JOB1_TITLE} opening at "
                f"{JOB1_COMPANY}. I've spent 7 years designing robotic "
                "subsystems with SolidWorks and GD&T-driven DFM reviews, "
                "and would love to connect about the role."
            )
        ),
        CONTACT_SWE_NAME: OutreachDraftContent(
            body=(
                f"Hi {CONTACT_SWE_NAME}, I saw the {JOB2_TITLE} opening at "
                f"{JOB2_COMPANY}. I've spent 8 years building event-driven "
                "backend systems in Python with Kafka and FastAPI, and "
                "would love to connect about the role."
            )
        ),
        CONTACT_HR_NAME: OutreachDraftContent(
            body=(
                f"Hi {CONTACT_HR_NAME}, I saw the {JOB3_TITLE} opening at "
                f"{JOB3_COMPANY}. I've spent 6 years in talent acquisition "
                "and HR business partnering, and would love to connect "
                "about the role."
            )
        ),
    }
)


# ---------------------------------------------------------------------------
# "Live" clients — call the real HTTP API in-process (ASGI transport, no
# real socket) on every invocation, so they always reflect current DB state
# regardless of when a consumer handler fires. See this file's header for
# why these are dynamic rather than pre-scripted snapshots.
# ---------------------------------------------------------------------------


def _auth_headers(user_id) -> dict[str, str]:
    """`/profiles` and `/users/me/preferences` now derive identity from a
    bearer token rather than a client-supplied `user_id` (see
    `infrastructure.auth`). These "Live" clients are privileged
    server-to-server code (a Kafka consumer, not a real browser request),
    so they mint their own token for whichever `user_id` they're acting on
    behalf of, the same way a trusted backend-to-backend caller would.
    """
    return {"Authorization": f"Bearer {create_access_token(user_id)}"}


class LiveProfileServiceClient:
    """Matching's `ProfileServiceClient` shape: `list_profiles(user_id)`."""

    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    async def list_profiles(self, user_id) -> list[ResumeProfile]:
        response = await self._http.get("/profiles", headers=_auth_headers(user_id))
        response.raise_for_status()
        return [ResumeProfile(**item) for item in response.json()]


class LiveUserPreferencesClient:
    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    async def get_preferences(self, user_id) -> UserPreferences | None:
        response = await self._http.get("/users/me/preferences", headers=_auth_headers(user_id))
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return UserPreferences(**response.json())


class LiveJobMatchClient:
    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    async def get_match(self, job_id) -> JobMatchResponse | None:
        response = await self._http.get(f"/jobs/{job_id}/matches")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return JobMatchResponse(**response.json())


class LiveOutreachProfileServiceClient:
    """Outreach's `ProfileServiceClient` shape: `get_profile(profile_id)`."""

    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    async def get_profile(self, profile_id) -> ResumeProfile | None:
        response = await self._http.get(f"/profiles/{profile_id}")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return ResumeProfile(**response.json())


class LiveJobIngestionClient:
    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    async def get_job(self, job_id) -> JobResponse | None:
        response = await self._http.get(f"/jobs/{job_id}")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return JobResponse(**response.json())


# ---------------------------------------------------------------------------
# Session-dependency override factory — identical shape to
# tests/integration/conftest.py's `_session_dependency_factory`.
# ---------------------------------------------------------------------------


def _session_dependency_factory(session_factory: async_sessionmaker[AsyncSession]):
    async def _dependency():
        session = session_factory()
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

    return _dependency


async def _drain_loop(broker: InMemoryBroker) -> None:
    """Continuously dispatch every not-yet-delivered message on every
    `CHAIN_ORDER` topic, forever, for the server's whole lifetime — the
    real-Kafka-consumer stand-in this whole script exists to provide. Errors
    from one bad envelope are logged, not fatal, so the loop keeps serving
    every other in-flight opportunity.
    """
    offsets: dict = dict.fromkeys(CHAIN_ORDER, 0)
    while True:
        try:
            offsets = await drain_chain(broker, offsets=offsets)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - never let one bad message kill the loop
            logger.exception("drain_chain iteration failed; will retry next tick")
        await asyncio.sleep(DRAIN_INTERVAL_SECONDS)


async def _amain() -> None:
    # KNOWN, FIXED-HERE concurrency defect (this script's own file, not
    # backend source — safe to fix directly). Confirmed by direct
    # reproduction, no browser involved: 3 concurrent `POST /resumes`
    # uploads for one user, one of the three permanently stuck at
    # `PARSING` forever. `create_async_engine("sqlite+aiosqlite:///:memory:")`
    # with no poolclass override (the exact call `tests/integration/
    # conftest.py`'s own `harness` fixture also uses) never surfaces this in
    # pytest, since `TestClient` runs one request at a time — but this
    # script's real concurrent browser + background drain loop can issue
    # genuinely overlapping requests, and each request's DB session is held
    # open across an `asyncio.to_thread(...)` LLM call (parsing.py), so two
    # overlapping requests' sessions can contend for the same underlying
    # SQLite connection for a non-trivial window. `StaticPool` (pinning
    # every checkout to one connection) was tried first and made it WORSE
    # (2 of 3 stuck) — a single shared DBAPI connection cannot itself
    # safely serve two overlapping transactions. The reliable fix is what
    # SQLite itself is actually designed for under concurrent access: a
    # real file-backed database (temp file, not `:memory:`) so every
    # session gets its own connection to the same on-disk database, using
    # SQLite's own file-locking, with WAL mode + a busy timeout so writers
    # queue instead of raising `database is locked`. Confirmed reliable
    # under repeated concurrent-upload reproduction after this change (see
    # this agent's final report).
    db_path = Path(tempfile.mkdtemp(prefix="e2e-db-")) / "e2e.sqlite3"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_path}",
        connect_args={"timeout": 30},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _connection_record) -> None:  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    broker = InMemoryBroker()

    matching_producer = EventProducer(
        matching_events.PRODUCER_NAME, client=InMemoryProducerClient(broker)
    )
    contacts_producer = EventProducer(
        contacts_events.PRODUCER_NAME, client=InMemoryProducerClient(broker)
    )
    outreach_producer = EventProducer(
        outreach_events.PRODUCER_NAME, client=InMemoryProducerClient(broker)
    )
    tracking_producer = EventProducer(
        tracking_events.PRODUCER_NAME, client=InMemoryProducerClient(broker)
    )
    jobs_producer = EventProducer("job-ingestion-service", client=InMemoryProducerClient(broker))
    profiles_producer = EventProducer(
        "resume-profile-service", client=InMemoryProducerClient(broker)
    )

    matching_events.set_event_producer(matching_producer)
    contacts_events.set_event_producer(contacts_producer)
    outreach_events.set_event_producer(outreach_producer)
    tracking_events.set_event_producer(tracking_producer)

    matching_db.set_session_factory(session_factory)
    contacts_db.set_session_factory(session_factory)
    outreach_db.set_session_factory(session_factory)
    tracking_db.set_session_factory(session_factory)

    app = fastapi_app
    app.dependency_overrides = {}

    app.dependency_overrides[users_get_session] = _session_dependency_factory(session_factory)
    app.dependency_overrides[jobs_get_db_session] = _session_dependency_factory(session_factory)
    app.dependency_overrides[matching_get_session] = _session_dependency_factory(session_factory)
    app.dependency_overrides[contacts_get_session] = _session_dependency_factory(session_factory)
    app.dependency_overrides[outreach_get_session] = _session_dependency_factory(session_factory)
    app.dependency_overrides[tracking_get_session] = _session_dependency_factory(session_factory)
    app.dependency_overrides[profiles_get_session] = _session_dependency_factory(session_factory)
    app.dependency_overrides[jobs_get_event_producer] = lambda: jobs_producer

    resume_dir = Path(tempfile.mkdtemp(prefix="e2e-resumes-"))
    resume_storage = LocalResumeStorage(base_dir=resume_dir)

    def _profile_service_override(
        session: Annotated[AsyncSession, Depends(profiles_get_session)],
    ) -> ProfileService:
        return ProfileService(
            ResumeRepository(session),
            CandidateProfileRepository(session),
            producer=profiles_producer,
            storage=resume_storage,
            llm_client=RESUME_LLM,
        )

    app.dependency_overrides[profiles_get_profile_service] = _profile_service_override

    fetcher = FakePageFetcher(pages=JOB_PAGES, errors=JOB_FETCH_ERRORS)
    extractor = FakeExtractor(by_content=JOB_EXTRACTOR_BY_CONTENT)
    app.dependency_overrides[jobs_get_page_fetcher] = lambda: fetcher
    app.dependency_overrides[jobs_get_structured_extractor] = lambda: extractor

    # CORS — see this file's header ("Known non-invasive gap fix").
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    internal_transport = httpx.ASGITransport(app=app)
    internal_http = httpx.AsyncClient(transport=internal_transport, base_url="http://e2e-internal")

    matching_nodes.set_profile_service_client(LiveProfileServiceClient(internal_http))
    matching_consumers.set_user_preferences_client(LiveUserPreferencesClient(internal_http))
    matching_nodes.set_llm_client(MATCHING_LLM)

    contact_nodes.set_people_search_client(CONTACTS_PEOPLE_SEARCH)
    contact_nodes.set_llm_client(CONTACTS_LLM)

    outreach_nodes.set_llm_client(OUTREACH_LLM)
    outreach_consumers.set_job_match_client(LiveJobMatchClient(internal_http))
    outreach_consumers.set_profile_service_client(LiveOutreachProfileServiceClient(internal_http))
    outreach_consumers.set_job_ingestion_client(LiveJobIngestionClient(internal_http))
    # outreach_consumers' message-send client is left at its lazily-built
    # default (RecordingMessageSendProvider) — safe by construction, no real
    # recipient is ever reached; see outreach/consumers.py's
    # `_get_message_send_client` docstring.

    logger.info("fixtures wired: 3 profiles, 3 jobs, contacts + outreach scripted")

    drain_task = asyncio.create_task(_drain_loop(broker))

    config = uvicorn.Config(app, host=HOST, port=PORT, log_level="warning", lifespan="on")
    server = uvicorn.Server(config)
    logger.info("starting E2E backend on http://%s:%s", HOST, PORT)
    try:
        await server.serve()
    finally:
        drain_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await drain_task
        await internal_http.aclose()

        matching_nodes.set_profile_service_client(None)
        matching_nodes.set_llm_client(None)
        matching_consumers.set_user_preferences_client(None)
        matching_db.set_session_factory(None)
        matching_events.set_event_producer(None)

        contact_nodes.set_people_search_client(None)
        contact_nodes.set_llm_client(None)
        contacts_db.set_session_factory(None)
        contacts_events.set_event_producer(None)

        outreach_nodes.set_llm_client(None)
        outreach_consumers.set_job_match_client(None)
        outreach_consumers.set_profile_service_client(None)
        outreach_consumers.set_job_ingestion_client(None)
        outreach_db.set_session_factory(None)
        outreach_events.set_event_producer(None)

        tracking_db.set_session_factory(None)
        tracking_events.set_event_producer(None)

        await engine.dispose()


def main() -> None:
    asyncio.run(_amain())


if __name__ == "__main__":
    main()
