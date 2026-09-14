"""User Service API request/response contracts.
See docs/architecture/api-contracts.md#user-service.
"""

from datetime import datetime

from pydantic import BaseModel, field_validator

from shared.types.domain.skill_prompt import SkillKey
from shared.types.domain.user_llm_config import LlmProviderName
from shared.types.enums import RemoteWorkPreference
from shared.types.ids import UserId, UserPreferencesId

MAX_SKILL_INSTRUCTIONS_LENGTH = 2000
"""Per-skill ceiling on a user's custom instructions, in characters.

Generous enough for the paragraph or two of real preference
docs/prompt-customization/PRODUCT.md describes ("always lead with my Python
experience", "de-emphasize my QA background unless the job asks for it"),
and small enough to bound both the token cost added to every call of that
skill and the amount of text an injection attempt has to work with. It is
not a safety mechanism on its own — the append point's ordering and framing
are (see docs/prompt-customization/TECH.md) — it is a cost and blast-radius
bound.
"""


class CreateUserRequest(BaseModel):
    email: str
    display_name: str
    password: str
    timezone: str | None = None


class UserResponse(BaseModel):
    id: UserId
    email: str
    display_name: str
    created_at: datetime


class UserPreferencesResponse(BaseModel):
    id: UserPreferencesId
    user_id: UserId
    target_roles: list[str] = []
    target_locations: list[str] = []
    remote_preference: RemoteWorkPreference | None = None
    excluded_companies: list[str] = []
    min_salary: int | None = None
    salary_currency: str | None = None
    updated_at: datetime


class UpdateUserPreferencesRequest(BaseModel):
    target_roles: list[str] = []
    target_locations: list[str] = []
    remote_preference: RemoteWorkPreference | None = None
    excluded_companies: list[str] = []
    min_salary: int | None = None
    salary_currency: str | None = None


class UserLlmConfigResponse(BaseModel):
    """The user's saved AI provider configuration, as the frontend sees it.

    Deliberately narrower than the `UserLlmConfig` domain type and *far*
    narrower than the stored row: it carries `key_hint` (a masked tail such
    as `...ab12`) and never the API key itself. The repository that builds
    the domain object cannot hand a plaintext key to this layer at all (see
    `users/repository.py::UserLlmConfigRepository.get`), so there is no code
    path by which a usable key could reach a response body.
    """

    provider: LlmProviderName
    model: str | None = None
    key_hint: str | None = None
    last_validated_at: datetime | None = None


class UpdateUserLlmConfigRequest(BaseModel):
    """Full-replace payload for `PUT /users/me/llm-config`, matching
    `UpdateUserPreferencesRequest`'s full-replace semantics.

    `api_key` is the one place a plaintext key travels in this system: it is
    accepted here, validated against the live provider, encrypted, and
    stored. It is never echoed back — the response is a
    `UserLlmConfigResponse`.
    """

    provider: LlmProviderName
    model: str | None = None
    api_key: str


class SkillPromptsResponse(BaseModel):
    """The caller's own per-skill custom instructions, verbatim.

    Returned in full rather than masked or summarized — the opposite of
    `UserLlmConfigResponse`, and for the opposite reason: this is text the
    user typed about their own resume, and the Settings form has to be able
    to seed its text areas with exactly what is stored so a save does not
    silently rewrite what they wrote.

    A user who has saved nothing gets `{}` with a 200, not a 404. An empty
    map is a complete and truthful answer here ("every skill uses its
    default prompt"), unlike an absent `llm-config`, where "unset" gates the
    whole AI feature set and the UI must say so.
    """

    instructions: dict[str, str] = {}


class UpdateSkillPromptsRequest(BaseModel):
    """Full-replace payload for `PUT /users/me/skill-prompts`, matching
    `UpdateUserPreferencesRequest`'s and `UpdateUserLlmConfigRequest`'s
    full-replace semantics.

    The frontend always submits the complete current map, so a skill absent
    from `instructions` is cleared rather than left alone — there is no
    partial-update ambiguity between "field omitted" and "field cleared",
    and therefore no separate delete endpoint. Submitting an empty string
    for a skill is the documented way to turn its customization off
    (docs/prompt-customization/PRODUCT.md: "Saving an empty box after
    previously having text clears the customization").

    Both validators below fail the request at the schema boundary, so an
    over-long or misspelled entry comes back as a field-level 422 rather
    than being stored inert or surfacing as a 500 later.
    """

    instructions: dict[str, str]

    @field_validator("instructions")
    @classmethod
    def _reject_unknown_skills(cls, value: dict[str, str]) -> dict[str, str]:
        """Only current `SkillKey` values are accepted.

        A client typo (`"resume_tailor"`) would otherwise be stored happily
        and then never read by anything, leaving the user looking at saved
        instructions that silently do nothing. Failing loudly here is the
        only way that mistake is ever visible.
        """
        known = {skill.value for skill in SkillKey}
        unknown = sorted(set(value) - known)
        if unknown:
            raise ValueError(
                f"unknown skill key(s): {', '.join(unknown)} — "
                f"expected one of: {', '.join(sorted(known))}"
            )
        return value

    @field_validator("instructions")
    @classmethod
    def _reject_over_long_instructions(cls, value: dict[str, str]) -> dict[str, str]:
        too_long = sorted(
            key
            for key, text in value.items()
            if len(text) > MAX_SKILL_INSTRUCTIONS_LENGTH
        )
        if too_long:
            raise ValueError(
                f"instructions for {', '.join(too_long)} exceed the "
                f"{MAX_SKILL_INSTRUCTIONS_LENGTH}-character limit"
            )
        return value


__all__ = [
    "MAX_SKILL_INSTRUCTIONS_LENGTH",
    "CreateUserRequest",
    "SkillPromptsResponse",
    "UpdateSkillPromptsRequest",
    "UpdateUserLlmConfigRequest",
    "UpdateUserPreferencesRequest",
    "UserLlmConfigResponse",
    "UserPreferencesResponse",
    "UserResponse",
]
