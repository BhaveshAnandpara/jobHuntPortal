"""Node functions for the Contact Discovery LangGraph workflow.
See docs/architecture/langgraph-state.md#contactdiscoverystate for the
graph shape and per-node input/output/error contract:

    search_contacts -> rank_contacts -> persist_and_publish

Owned by Contact Discovery Service.

Dependency injection follows `workflows/langgraph/job_matching/nodes.py`'s
established idiom exactly: each node reaches a lazily-constructed,
module-level default (a real `PeopleSearchClient`, a real `LLMClient`, the
shared DB session scope from `contacts.db`, and `contacts.events`'
producer). Tests override via the `set_*` functions exported below rather
than patching internals directly.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from contacts.db import contacts_session_scope
from contacts.errors import ContactDiscoveryError
from contacts.events import publish_contacts_found
from contacts.repository import ContactRepository, ContactScoreRepository
from infrastructure.external.errors import PeopleSearchRequestError
from infrastructure.external.people_search import (
    PeopleSearchClient,
    PeopleSearchQuery,
    StaticPeopleSearchProvider,
)
from infrastructure.llm import LLMClient, LLMProviderError
from shared.errors.codes import ErrorCode
from shared.types.domain.contact import Contact
from shared.types.domain.contact_score import ContactScore
from shared.types.dto import ContactRankingResult, RankedContact, WorkflowError
from shared.types.enums import ContactStatus, ContactType
from shared.types.ids import ContactId, ContactScoreId
from workflows.langgraph.contact_discovery.context import (
    ContactPersistenceDetail,
    get_contact_details,
    get_correlation_id,
)
from workflows.langgraph.contact_discovery.discovery import (
    build_search_plan,
    classify_hits,
    compute_relevance_score,
    hits_to_candidates,
    rule_based_role_similarity,
    score_candidate_with_llm,
)
from workflows.langgraph.contact_discovery.discovery import (
    same_company as _same_company,
)
from workflows.langgraph.contact_discovery.state import ContactDiscoveryState

# ---------------------------------------------------------------------------
# Dependency injection seams
# ---------------------------------------------------------------------------

_people_search_client: PeopleSearchClient | None = None
_llm_client: LLMClient | None = None


def _get_people_search_client() -> PeopleSearchClient:
    global _people_search_client
    if _people_search_client is None:
        # Local-runnable default (no third-party credentials required) —
        # returns zero hits until a real provider is injected in
        # production wiring via set_people_search_client.
        _people_search_client = PeopleSearchClient(StaticPeopleSearchProvider([]))
    return _people_search_client


def set_people_search_client(client: PeopleSearchClient | None) -> None:
    """Test seam. Pass `None` to restore the lazily-constructed default."""
    global _people_search_client
    _people_search_client = client


def _get_llm_client() -> LLMClient:
    global _llm_client
    if _llm_client is None:
        _llm_client = LLMClient()
    return _llm_client


def set_llm_client(client: LLMClient | None) -> None:
    """Test seam. Pass `None` to restore the lazily-constructed default."""
    global _llm_client
    _llm_client = client


def _now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------


async def search_contacts(state: ContactDiscoveryState) -> ContactDiscoveryState:
    """External tools: LLM Provider Layer (search-keyword planning + hit
    classification), people-search client. Always routes to rank_contacts,
    even on failure or an empty result (langgraph-state.md#contactdiscoverystate).

    On CONTACT_SEARCH_FAILED (the people-search call itself failing):
    candidates left empty, error recorded, still routes forward.
    """
    company, title, location = state["company"], state["title"], state["location"]
    llm_client = _get_llm_client()
    errors: list[WorkflowError] = list(state["errors"])

    try:
        plan = build_search_plan(llm_client, company, title, location)
        role_keywords = plan.role_keywords
    except LLMProviderError:
        # Query-construction fallback: use the job's own free-text title as
        # the sole search keyword rather than aborting the node. Still
        # profession-independent — it is this job's own title text, not a
        # hard-coded role list.
        role_keywords = [title]

    people_search_client = _get_people_search_client()
    try:
        hits = await people_search_client.search(
            PeopleSearchQuery(company=company, role_keywords=role_keywords, location=location)
        )
    except PeopleSearchRequestError as exc:
        errors.append(
            WorkflowError(
                node="search_contacts",
                error_code=ErrorCode.CONTACT_SEARCH_FAILED,
                message=f"people search failed for company {company!r}: {exc}",
                occurred_at=_now(),
            )
        )
        return {**state, "candidates": [], "errors": errors}

    if not hits:
        return {**state, "candidates": [], "errors": errors}

    try:
        contact_types = classify_hits(llm_client, company, title, list(hits))
    except LLMProviderError as exc:
        # Classification fallback: every hit becomes ContactType.OTHER
        # rather than aborting the node — rank_contacts can still score
        # these candidates on same_company/role_similarity.
        contact_types = [ContactType.OTHER for _ in hits]
        errors.append(
            WorkflowError(
                node="search_contacts",
                error_code=ErrorCode.LLM_PROVIDER_ERROR,
                message=f"contact classification failed for company {company!r}: {exc}",
                occurred_at=_now(),
            )
        )

    candidates = hits_to_candidates(list(hits), contact_types, default_company=company)
    return {**state, "candidates": candidates, "errors": errors}


async def rank_contacts(state: ContactDiscoveryState) -> ContactDiscoveryState:
    """External tools: LLM Provider Layer (relevance signals), scoring
    logic. Always routes to persist_and_publish (langgraph-state.md).

    On LLM_PROVIDER_ERROR: falls back to rule-based same_company/
    role_similarity signals only, leaving department_relevance/
    seniority_fit None for that candidate, rather than aborting the run.
    """
    candidates = state["candidates"]
    if not candidates:
        return {**state, "ranked_contacts": []}

    company, title = state["company"], state["title"]
    llm_client = _get_llm_client()
    errors: list[WorkflowError] = list(state["errors"])
    details = get_contact_details()

    ranked: list[RankedContact] = []
    llm_error_recorded = False
    for candidate in candidates:
        same_company_flag = _same_company(candidate.company, company)
        try:
            signals = score_candidate_with_llm(llm_client, company, title, candidate)
            role_similarity = signals.role_similarity
            department_relevance: float | None = signals.department_relevance
            seniority_fit: float | None = signals.seniority_fit
        except LLMProviderError as exc:
            role_similarity = rule_based_role_similarity(candidate, title)
            department_relevance = None
            seniority_fit = None
            if not llm_error_recorded:
                errors.append(
                    WorkflowError(
                        node="rank_contacts",
                        error_code=ErrorCode.LLM_PROVIDER_ERROR,
                        message=(
                            f"relevance scoring failed for {candidate.full_name}: {exc}; "
                            "falling back to rule-based signals for this run"
                        ),
                        occurred_at=_now(),
                    )
                )
                llm_error_recorded = True

        relevance_score = compute_relevance_score(
            same_company_flag=same_company_flag,
            role_similarity=role_similarity,
            department_relevance=department_relevance,
            seniority_fit=seniority_fit,
        )

        contact_id = ContactId(uuid4())
        ranked.append(
            RankedContact(
                contact_id=contact_id,
                full_name=candidate.full_name,
                headline=candidate.headline,
                contact_type=candidate.contact_type,
                profile_url=candidate.profile_url,
                relevance_score=relevance_score,
            )
        )
        details[contact_id] = ContactPersistenceDetail(
            company=candidate.company,
            email=candidate.email,
            same_company=same_company_flag,
            role_similarity=role_similarity,
            department_relevance=department_relevance,
            seniority_fit=seniority_fit,
        )

    ranked.sort(key=lambda r: r.relevance_score, reverse=True)
    return {**state, "ranked_contacts": ranked, "errors": errors}


async def persist_and_publish(state: ContactDiscoveryState) -> ContactDiscoveryState:
    """External tools: contacts/contact_rankings repositories (DB write),
    Kafka producer. Terminal node — publishes ContactsFoundEvent even when
    ranked_contacts is empty (the documented "no contacts found" signal,
    not an error — NO_CONTACTS_FOUND). Raises ContactDiscoveryError on
    DB/publish failure so the Kafka consumer's retry/DLQ mechanism can act
    on it; no partial event is published.

    Contact rows are inserted directly at status=RANKED — DISCOVERED is the
    conceptual pre-ranking state (a transient in-memory ContactCandidate,
    never persisted on its own within this single-pass workflow), the same
    precedent state-machines.md#outreach-lifecycle documents for Outreach's
    DRAFT never being written to the outreach table.
    """
    job_id, user_id = state["job_id"], state["user_id"]
    company = state["company"]
    ranked_contacts = state["ranked_contacts"]
    details = get_contact_details()
    timestamp = _now()

    contacts: list[Contact] = []
    scores: list[ContactScore] = []
    for ranked in ranked_contacts:
        detail = details.get(ranked.contact_id)
        contacts.append(
            Contact(
                id=ranked.contact_id,
                job_id=job_id,
                user_id=user_id,
                full_name=ranked.full_name,
                headline=ranked.headline,
                company=detail.company if detail is not None else company,
                contact_type=ranked.contact_type,
                profile_url=ranked.profile_url,
                email=detail.email if detail is not None else None,
                status=ContactStatus.RANKED,
                discovered_at=timestamp,
            )
        )
        scores.append(
            ContactScore(
                id=ContactScoreId(uuid4()),
                contact_id=ranked.contact_id,
                job_id=job_id,
                relevance_score=ranked.relevance_score,
                same_company=detail.same_company if detail is not None else False,
                department_relevance=detail.department_relevance if detail is not None else None,
                role_similarity=detail.role_similarity if detail is not None else None,
                seniority_fit=detail.seniority_fit if detail is not None else None,
                ranked_at=timestamp,
            )
        )

    try:
        async with contacts_session_scope() as session:
            contact_repository = ContactRepository(session)
            score_repository = ContactScoreRepository(session)
            for contact in contacts:
                await contact_repository.add(contact)
            for score in scores:
                await score_repository.add(score)
    except Exception as exc:
        raise ContactDiscoveryError(
            f"failed to persist contacts for job {job_id}: {exc}"
        ) from exc

    result = ContactRankingResult(
        job_id=job_id, user_id=user_id, contacts=ranked_contacts, ranked_at=timestamp
    )
    try:
        await publish_contacts_found(result, correlation_id=get_correlation_id())
    except Exception as exc:
        raise ContactDiscoveryError(
            f"failed to publish contacts.found for job {job_id}: {exc}"
        ) from exc

    return state


__all__ = [
    "persist_and_publish",
    "rank_contacts",
    "search_contacts",
    "set_llm_client",
    "set_people_search_client",
]
