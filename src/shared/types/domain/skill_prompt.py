"""SkillPromptCustomization — the optional, per-user, per-skill extra
instructions a user appends to an AI skill's prompt. One active row per
user, mirroring `user_preferences` and `user_llm_config`. See
docs/prompt-customization/TECH.md.

Ownership: User Service. Modifiable by User Service only.

**This is additive text, never a replacement prompt.** The stored value is
appended, quoted and explicitly subordinated, after the *end* of each
skill's existing hardened prompt — see
`infrastructure/llm/user_prompts.py` and the single append point in each
prompt builder. Nothing here can remove, reorder, or precede an
anti-fabrication rule, and this type deliberately carries no field that
could express "replace the prompt": it is a map of free text, and the only
thing the system does with that text is quote it inside a fixed frame.

Unlike `user_llm_config.encrypted_api_key`, this is plain text. A user's
stated preference about how their own resume is phrased is not a secret:
it never needs decryption on read, and it is safe to return verbatim in an
API response to the user who wrote it.
"""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel

from shared.types.ids import UserId


class SkillKey(str, Enum):
    """The AI "skills" a user may append their own instructions to.

    "Skill" is the in-app term (docs/prompt-customization/PRODUCT.md) for
    one LLM-driven capability with its own engineered prompt. Values are
    the stable storage keys of the `instructions` map below, so renaming a
    member's *value* would orphan every saved row — add new members, never
    re-spell existing ones.

    Only capabilities whose prompt builder actually has an append point
    belong here. Contact discovery and outreach generation deliberately do
    not (PRODUCT.md's "Non-Goals"); adding one later is a new member plus
    an append point, and no schema change — which is the whole reason the
    stored shape is a JSON map rather than a column per skill.
    """

    RESUME_TAILORING = "resume_tailoring"
    JOB_MATCHING = "job_matching"


class SkillPromptCustomization(BaseModel):
    """A user's saved per-skill instructions, safe to return anywhere.

    `instructions` is keyed by `SkillKey` *value* (`"resume_tailoring"`,
    `"job_matching"`), not by the enum member, because it is persisted as
    JSON and read back as plain strings — keeping the stored and in-memory
    key type the same avoids a translation step that could silently drop a
    key it does not recognize. A skill absent from the map, or present with
    an empty/whitespace value, means "use that skill's default prompt
    unchanged" — the common case, and the only case for every user who has
    not opted in.

    No `id` field, unlike `UserLlmConfig`: nothing in the system addresses
    one of these rows by its own identity — every read and write is by
    `user_id` — so surfacing the surrogate key would only invite a caller
    to depend on it.
    """

    user_id: UserId  # one active row per user
    instructions: dict[str, str] = {}
    updated_at: datetime


__all__ = ["SkillKey", "SkillPromptCustomization"]
