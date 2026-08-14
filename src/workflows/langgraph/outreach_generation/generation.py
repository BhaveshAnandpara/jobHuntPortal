"""LLM-backed personalized outreach draft generation for the Outreach
Generation LangGraph workflow's `generate_message` node.

Internal to this workflow — the Pydantic schema below never crosses a
component boundary; only the resulting `str` (the eventual
`Outreach.draft_message`) does. Same "internal LLM-call contract, not a
shared type" convention as `workflows/langgraph/job_matching/scoring.py`
and `workflows/langgraph/contact_discovery/discovery.py`.

Profession-independence (CLAUDE.md rules 3-4; about_project.md's "Outreach
Generation" section — "The goal is to avoid generic mass outreach";
service-boundaries.md#outreach-service): no function here branches on the
candidate's or the job's profession/title text. The identical prompt and
schema run for a software engineer, a mechanical engineer, an HR
professional, a finance analyst, or any other profession named in
about_project.md — all personalization comes from feeding the LLM this
run's own `ResumeProfile`/job-context/`RankedContact` data, never a
per-profession template or code branch (mirrors
`workflows/langgraph/contact_discovery/discovery.py`'s established
profession-independence pattern).

Channel affects formatting/content constraints only (a LinkedIn connection
request's real-world ~300 character limit; email's optional subject line)
— never a separate prompt template or duplicated business logic. One
prompt-building function, channel passed as context, per this component's
task brief ("Channel affects formatting/content constraints only ... One
generate_message node, channel passed as context").
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from infrastructure.llm import LLMClient
from shared.types.dto import RankedContact, ResumeProfile
from shared.types.enums import OutreachChannel

LINKEDIN_CONNECTION_REQUEST_CHAR_LIMIT = 300
"""LinkedIn's real-world connection-request note character limit. The
prompt asks the model to respect it; `nodes._format_draft` also enforces it
defensively (truncates) in case the model doesn't."""


_SYSTEM_PROMPT = (
    "You are an expert, profession-agnostic professional-outreach writer. "
    "Given ONE candidate (any profession — software engineering, "
    "mechanical/civil engineering, HR, product management, data "
    "analytics, finance, design, skilled trades, students/freshers, and "
    "more), ONE target job opportunity, and ONE contact at the target "
    "company, write a short, individually-written outreach message. Never "
    "assume the profession from the job title alone, and never reuse a "
    "generic template — ground the message in the candidate's own actual "
    "skills/experience relevant to this specific role, and the contact's "
    "own actual headline/role. Never write generic filler like 'I saw you "
    "work at Company X, please refer me' — a real recipient should read "
    "this as written specifically for them about this specific "
    "opportunity. Be concise, professional, and warm; do not overstate the "
    "candidate's fit or fabricate shared history with the contact."
)


class OutreachDraftContent(BaseModel):
    """Structured LLM output for one generated draft. `subject` is only
    meaningful for `OutreachChannel.EMAIL` — see `_channel_instructions`
    and `nodes._format_draft`.
    """

    subject: str | None = None
    body: str = Field(min_length=1)


def _channel_instructions(channel: OutreachChannel) -> str:
    if channel == OutreachChannel.LINKEDIN_CONNECTION_REQUEST:
        return (
            "Channel: LinkedIn connection request note. This channel has a "
            f"real character limit — keep the body to at most "
            f"{LINKEDIN_CONNECTION_REQUEST_CHAR_LIMIT} characters, no "
            "subject line, no greeting/sign-off boilerplate, just the note "
            "itself."
        )
    if channel == OutreachChannel.LINKEDIN_MESSAGE:
        return (
            "Channel: LinkedIn direct message. No subject line. Keep it "
            "short (a few sentences) — this is a message, not an email."
        )
    if channel == OutreachChannel.EMAIL:
        return (
            "Channel: email. Include a concise, specific subject line "
            "(not 'Hello' or 'Referral request') and a short, professional "
            "email body with a greeting and a sign-off."
        )
    return "Channel: unspecified. Keep the message short and professional."


def build_prompt(
    *,
    channel: OutreachChannel,
    contact: RankedContact,
    candidate_profile: ResumeProfile,
    company: str,
    title: str,
) -> str:
    skills = ", ".join(candidate_profile.skills) or "(not specified)"
    experience = (
        f"{candidate_profile.experience_years:g} years"
        if candidate_profile.experience_years is not None
        else "not specified"
    )
    seniority = candidate_profile.seniority or "not specified"
    summary = candidate_profile.summary or "(no summary provided)"

    return (
        f"{_channel_instructions(channel)}\n\n"
        f"Target job: {title} at {company}.\n\n"
        "Candidate profile:\n"
        f"- Title: {candidate_profile.title}\n"
        f"- Summary: {summary}\n"
        f"- Relevant skills: {skills}\n"
        f"- Experience: {experience}\n"
        f"- Seniority: {seniority}\n\n"
        "Contact being reached out to:\n"
        f"- Name: {contact.full_name}\n"
        f"- Headline: {contact.headline or '(no headline)'}\n"
        f"- Functional tier: {contact.contact_type.value}\n\n"
        "Write ONE personalized outreach message for this exact "
        "candidate, job, and contact, following the channel instructions "
        "above."
    )


def generate_draft_content(
    llm_client: LLMClient,
    *,
    channel: OutreachChannel,
    contact: RankedContact,
    candidate_profile: ResumeProfile,
    company: str,
    title: str,
) -> OutreachDraftContent:
    prompt = build_prompt(
        channel=channel,
        contact=contact,
        candidate_profile=candidate_profile,
        company=company,
        title=title,
    )
    return llm_client.complete_structured(prompt, OutreachDraftContent, system=_SYSTEM_PROMPT)


__all__ = [
    "LINKEDIN_CONNECTION_REQUEST_CHAR_LIMIT",
    "OutreachDraftContent",
    "build_prompt",
    "generate_draft_content",
]
