"""LLM-backed contact search planning, hit classification, and ranking
signal extraction for the Contact Discovery LangGraph workflow.

Internal to this workflow — none of the Pydantic schemas below cross a
component boundary; only `shared.types.dto.ContactCandidate`/`RankedContact`
(via nodes.py) do. Same "internal LLM-call contract, not a shared type"
convention as `workflows/langgraph/job_matching/scoring.py`.

Profession-independence (CLAUDE.md rules 3-4; about_project.md's "Contact
Discovery"/"Contact Ranking" sections; service-boundaries.md#contact-
discovery-service: "no hard-coded 'Software Engineer'/'Recruiter' logic
branches"): no function here branches on the job's title/company text.
Which search keywords to use and which functional tier (`ContactType`) a
hit belongs to are both LLM judgements over the job's own free-text
title/company and each hit's own free-text headline — the identical prompt
and schema run for a Mechanical Engineer job, a Software Engineer job, and
an HR job alike (about_project.md's own three worked examples for this
exact service — Software Engineers/Engineering Managers/Recruiters vs.
Mechanical Engineers/Design Leads/Manufacturing Leads vs. HR Managers/
Talent Acquisition Leads/HR Business Partners — all flow through this same
code, never a per-profession branch).
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from infrastructure.external.people_search import PersonSearchHit
from infrastructure.llm import LLMClient
from shared.types.dto import ContactCandidate
from shared.types.enums import ContactType

# ---------------------------------------------------------------------------
# search_contacts: query construction
# ---------------------------------------------------------------------------

_SEARCH_PLAN_SYSTEM_PROMPT = (
    "You are an expert, profession-agnostic professional-networking "
    "analyst. Given ONE job opportunity (any profession — software "
    "engineering, mechanical/civil engineering, HR, product management, "
    "data analytics, finance, design, skilled trades, students/freshers, "
    "and more), propose search terms for finding useful referral/"
    "networking contacts at the target company for THIS specific "
    "opportunity. Never assume the profession from the job title alone, "
    "and never reuse a fixed list of roles from one profession for a "
    "different one — read the job's own title and reason about who would "
    "actually be relevant: people doing the work itself, their team "
    "leads/managers, the likely hiring manager, recruiters/talent "
    "acquisition, and senior/department leadership, all phrased using "
    "this job's own domain vocabulary."
)


class ContactSearchPlan(BaseModel):
    role_keywords: list[str] = Field(min_length=1, max_length=8)
    reasoning: str = ""


def build_search_plan_prompt(company: str, title: str, location: str | None) -> str:
    return (
        "Propose 3-6 short search role/title keywords (free text, using "
        "this job's own domain vocabulary) that would help find useful "
        "referral or networking contacts at the target company for this "
        "opportunity. Include a mix where relevant: people who do this "
        "work day to day, their team leads/managers, the likely hiring "
        "manager, recruiters/talent acquisition, and senior/department "
        "leadership. Do not invent a profession — infer it entirely from "
        "the job title given.\n\n"
        f"Job title: {title}\n"
        f"Company: {company}\n"
        f"Location: {location or 'not specified'}\n"
    )


def build_search_plan(
    llm_client: LLMClient, company: str, title: str, location: str | None
) -> ContactSearchPlan:
    return llm_client.complete_structured(
        build_search_plan_prompt(company, title, location),
        ContactSearchPlan,
        system=_SEARCH_PLAN_SYSTEM_PROMPT,
    )


# ---------------------------------------------------------------------------
# search_contacts: hit classification
# ---------------------------------------------------------------------------

_CLASSIFICATION_SYSTEM_PROMPT = (
    "You are an expert, profession-agnostic professional-networking "
    "analyst. Classify each person below into exactly one functional "
    "tier, based purely on their own headline/title text, never on the "
    "target job's profession: PRACTITIONER (does the hands-on work "
    "themselves), TEAM_LEAD (leads a small team, or is a senior "
    "individual contributor doing lead-level work), HIRING_MANAGER "
    "(manages the team that would own this opening), RECRUITER "
    "(recruiting/talent-acquisition function), EXECUTIVE (VP-level or "
    "above), DEPARTMENT_LEADER (director/head of a department, below "
    "executive), or OTHER if none clearly fits."
)


class HitClassification(BaseModel):
    index: int
    contact_type: ContactType


class ContactClassificationBatch(BaseModel):
    classifications: list[HitClassification]


def build_classification_prompt(
    company: str, title: str, hits: list[PersonSearchHit]
) -> str:
    lines = "\n".join(
        f"{i}. {hit.full_name} — {hit.headline or '(no headline)'}"
        for i, hit in enumerate(hits)
    )
    return (
        f"Target opportunity: {title} at {company}.\n\n"
        "Classify each of the following people found at the target "
        "company into exactly one functional tier. Return one "
        "classification per index listed below, using the SAME index "
        "numbers.\n\n"
        f"{lines}\n"
    )


def classify_hits(
    llm_client: LLMClient, company: str, title: str, hits: list[PersonSearchHit]
) -> list[ContactType]:
    """Returns one `ContactType` per hit, aligned by position. Falls back
    to `ContactType.OTHER` for any index the model didn't return a
    classification for — defensive against a response that validates
    against `ContactClassificationBatch`'s schema but doesn't actually
    cover every index.
    """
    batch = llm_client.complete_structured(
        build_classification_prompt(company, title, hits),
        ContactClassificationBatch,
        system=_CLASSIFICATION_SYSTEM_PROMPT,
    )
    by_index = {item.index: item.contact_type for item in batch.classifications}
    return [by_index.get(i, ContactType.OTHER) for i in range(len(hits))]


def _company_mentioned_in_headline(headline: str | None, company: str) -> bool:
    """True when the target company's own name appears in the candidate's
    own headline text as a distinct word/phrase (word-boundary match, not
    an arbitrary substring — so "Via" doesn't match "Reliable") — e.g.
    "Software Engineer @Viamedia" or "Creative Specialist at Viamedia".
    This is confirmation, not a guess: unlike defaulting an unparsed
    company field, the company's own name is literally present in text the
    candidate wrote about themselves, even when `_parse_search_item`
    couldn't split it into its own dedicated title segment.
    """
    company = company.strip()
    if not headline or not company:
        return False
    pattern = r"(?<!\w)" + re.escape(company) + r"(?!\w)"
    return re.search(pattern, headline, re.IGNORECASE) is not None


def hits_to_candidates(
    hits: list[PersonSearchHit],
    contact_types: list[ContactType],
    *,
    default_company: str,
) -> list[ContactCandidate]:
    """A candidate's company is only ever `company_confirmed=True` when
    there is real evidence for it: either `_parse_search_item` split a
    distinct company segment out of the hit's title, or the target
    company's own name appears in the candidate's headline text
    (`_company_mentioned_in_headline`). Everything else falls back to
    `default_company` as a *display* value only, `company_confirmed=False`
    — the candidate's name/profile merely matched the site-restricted
    search query for that company, which unrelated profiles that only
    happen to mention it elsewhere on their page can also do. Downstream,
    `rank_contacts` uses `company_confirmed` both to gate the same-company
    scoring signal and to keep unconfirmed candidates from outranking
    confirmed ones.
    """
    candidates: list[ContactCandidate] = []
    for hit, contact_type in zip(hits, contact_types, strict=True):
        if hit.company is not None:
            company, confirmed = hit.company, True
        elif _company_mentioned_in_headline(hit.headline, default_company):
            company, confirmed = default_company, True
        else:
            company, confirmed = default_company, False
        candidates.append(
            ContactCandidate(
                full_name=hit.full_name,
                headline=hit.headline,
                company=company,
                company_confirmed=confirmed,
                contact_type=contact_type,
                profile_url=hit.profile_url,
                email=hit.email,
                source=hit.provider,
            )
        )
    return candidates


def is_confirmed_same_company(candidate: ContactCandidate, company: str) -> bool:
    """True only when `candidate` carries real, confirmed evidence
    (`company_confirmed`) that its own company text actually matches the
    target `company` — never merely because the candidate's profile
    surfaced on a search scoped to that company. Used by `search_contacts`
    to drop candidates with no real evidence of working at the target
    company, rather than passing them through to ranking/persistence where
    they could otherwise still surface as a "referral contact" for a
    company they may have no connection to at all.
    """
    return candidate.company_confirmed and same_company(candidate.company, company)


# ---------------------------------------------------------------------------
# rank_contacts: relevance signals
# ---------------------------------------------------------------------------

_RANKING_SYSTEM_PROMPT = (
    "You are an expert, profession-agnostic professional-networking "
    "analyst. Score how useful ONE contact would be for referral/"
    "networking outreach about ONE job opportunity, for any profession. "
    "Never assume the profession from the job title alone — read the "
    "contact's own headline and judge fit on its merits."
)


class RelevanceSignals(BaseModel):
    role_similarity: float = Field(ge=0.0, le=1.0)
    department_relevance: float = Field(ge=0.0, le=1.0)
    seniority_fit: float = Field(ge=0.0, le=1.0)
    reasoning: str = ""


class RankedRelevanceSignals(BaseModel):
    index: int
    role_similarity: float = Field(ge=0.0, le=1.0)
    department_relevance: float = Field(ge=0.0, le=1.0)
    seniority_fit: float = Field(ge=0.0, le=1.0)
    reasoning: str = ""


class RelevanceSignalsBatch(BaseModel):
    signals: list[RankedRelevanceSignals]


def build_ranking_batch_prompt(
    company: str, title: str, candidates: list[ContactCandidate]
) -> str:
    lines = "\n".join(
        f"{i}. {c.full_name} — {c.headline or '(no headline)'} ({c.contact_type.value})"
        for i, c in enumerate(candidates)
    )
    return (
        "Score EVERY contact listed below for referral/networking outreach "
        "relevance about the job opportunity given. Score each factor from "
        "0.0 (no fit) to 1.0 (excellent fit). Return one scored entry per "
        "index listed below, using the SAME index numbers:\n"
        "- role_similarity: how closely the contact's own work (per their "
        "headline) resembles the job's actual work\n"
        "- department_relevance: how likely the contact sits in the same "
        "team/department this opening belongs to\n"
        "- seniority_fit: whether the contact's seniority (per their "
        "headline/functional tier) makes them a plausible, reachable "
        "referral source for this opening (very junior or very senior "
        "relative to the role may be a weaker fit than a close peer or "
        "direct manager)\n\n"
        f"Job title: {title}\n"
        f"Company: {company}\n\n"
        f"{lines}\n"
    )


def score_candidates_with_llm(
    llm_client: LLMClient, company: str, title: str, candidates: list[ContactCandidate]
) -> list[RelevanceSignals | None]:
    """Scores every candidate in ONE LLM call instead of one call per
    candidate — the same index-aligned batching pattern `classify_hits`
    already uses for hit classification, applied here to cut
    `rank_contacts`' LLM call count from O(candidates) to O(1) per job.

    Returns one `RelevanceSignals` per candidate, aligned by position.
    `None` at an index means the model didn't return a scored entry for
    it — defensive against a response that validates against
    `RelevanceSignalsBatch`'s schema but doesn't actually cover every
    index — and the caller falls back to rule-based signals for just that
    candidate, not the whole batch.
    """
    batch = llm_client.complete_structured(
        build_ranking_batch_prompt(company, title, candidates),
        RelevanceSignalsBatch,
        system=_RANKING_SYSTEM_PROMPT,
    )
    by_index = {item.index: item for item in batch.signals}
    results: list[RelevanceSignals | None] = []
    for i in range(len(candidates)):
        item = by_index.get(i)
        results.append(
            None
            if item is None
            else RelevanceSignals(
                role_similarity=item.role_similarity,
                department_relevance=item.department_relevance,
                seniority_fit=item.seniority_fit,
                reasoning=item.reasoning,
            )
        )
    return results


# ---------------------------------------------------------------------------
# rank_contacts: rule-based fallback (LLM_PROVIDER_ERROR path) + scoring
# ---------------------------------------------------------------------------

_STOPWORDS = frozenset(
    {"a", "an", "the", "and", "or", "of", "at", "in", "for", "to", "with", "on", "&"}
)


def _keywords(text: str) -> set[str]:
    return {word for word in text.lower().replace("/", " ").split() if word not in _STOPWORDS}


def rule_based_role_similarity(candidate: ContactCandidate, title: str) -> float:
    """Fallback used only when the LLM Provider Layer is unavailable
    (`rank_contacts`' documented `LLM_PROVIDER_ERROR` fallback: "rule-based
    scoring signals only — same_company, role_similarity" —
    langgraph-state.md#contactdiscoverystate). Pure word-overlap between
    the job title and the candidate's headline — no profession-specific
    vocabulary is consulted, so it stays generic across every profession
    by construction (it never looks at *which* words overlap, only
    *whether* they do).
    """
    headline_words = _keywords(candidate.headline or "")
    title_words = _keywords(title)
    if not headline_words or not title_words:
        return 0.5  # neutral — insufficient text to compare
    overlap = headline_words & title_words
    return min(1.0, len(overlap) / max(1, len(title_words)))


def same_company(candidate_company: str, job_company: str) -> bool:
    return candidate_company.strip().casefold() == job_company.strip().casefold()


_SAME_COMPANY_WEIGHT = 0.30
_ROLE_SIMILARITY_WEIGHT = 0.35
_DEPARTMENT_RELEVANCE_WEIGHT = 0.20
_SENIORITY_FIT_WEIGHT = 0.15
_NEUTRAL_SIGNAL = 0.5
"""Weights sum to 1.0, so `compute_relevance_score`'s weighted sum stays in
[0, 1] before the *10 scale-up. `role_similarity` carries the single
highest weight but is capped at 0.35 of the total specifically so no one
signal can dominate the score: `department_relevance` + `seniority_fit`
together contribute another 0.35, and `same_company` a further 0.30.
Missing optional signals (`department_relevance`/`seniority_fit` possibly
`None` — always the case on the rule-based fallback path, and legitimately
possible on `ContactScore` per domain-model.md) default to a neutral 0.5
rather than 0.0, so an unscored factor pulls the result toward the middle
instead of being punished as if it were a confirmed bad fit — this is what
lets ranking still succeed, without crashing or unfairly penalizing a
contact, when optional signals are missing.
"""


def compute_relevance_score(
    *,
    same_company_flag: bool,
    role_similarity: float,
    department_relevance: float | None,
    seniority_fit: float | None,
) -> float:
    """Combines the four signals into a single 0.0-10.0 `relevance_score`
    (domain-model.md#contactscore's documented range)."""
    weighted = (
        _SAME_COMPANY_WEIGHT * (1.0 if same_company_flag else 0.0)
        + _ROLE_SIMILARITY_WEIGHT * role_similarity
        + _DEPARTMENT_RELEVANCE_WEIGHT
        * (department_relevance if department_relevance is not None else _NEUTRAL_SIGNAL)
        + _SENIORITY_FIT_WEIGHT
        * (seniority_fit if seniority_fit is not None else _NEUTRAL_SIGNAL)
    )
    return round(max(0.0, min(1.0, weighted)) * 10.0, 4)


__all__ = [
    "ContactClassificationBatch",
    "ContactSearchPlan",
    "HitClassification",
    "RankedRelevanceSignals",
    "RelevanceSignals",
    "RelevanceSignalsBatch",
    "build_classification_prompt",
    "build_ranking_batch_prompt",
    "build_search_plan",
    "build_search_plan_prompt",
    "classify_hits",
    "compute_relevance_score",
    "hits_to_candidates",
    "is_confirmed_same_company",
    "rule_based_role_similarity",
    "same_company",
    "score_candidates_with_llm",
]
