"""Business logic boundary for User Service. Called only by users/api/;
never imported by another component (see
docs/architecture/dependency-graph.md#1-compileimport-dependencies).

Preferences stay profession-independent: target roles and locations are
free text, and the only closed vocabulary is the shared
`RemoteWorkPreference` enum. No field here encodes an assumption about the
user's profession (docs/architecture/overview.md rule 5).
"""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

from pydantic import BaseModel

from infrastructure.auth import hash_password, verify_password
from infrastructure.llm.errors import LLMProviderError
from infrastructure.llm.structured import LLMClient
from infrastructure.llm.user_client import build_user_llm_config
from shared.errors.codes import ErrorCode
from shared.types.api.auth import LoginRequest
from shared.types.api.users import (
    CreateUserRequest,
    UpdateSkillPromptsRequest,
    UpdateUserLlmConfigRequest,
    UpdateUserPreferencesRequest,
)
from shared.types.domain.user import User
from shared.types.domain.user_llm_config import LlmProviderName, UserLlmConfig
from shared.types.domain.user_preferences import UserPreferences
from shared.types.ids import UserId, UserPreferencesId

if TYPE_CHECKING:
    from users.repository import (
        SkillPromptRepository,
        UserLlmConfigRepository,
        UserPreferencesRepository,
        UserRepository,
    )

_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s.]+(\.[^@\s.]+)+$")
_CURRENCY_PATTERN = re.compile(r"^[A-Z]{3}$")

_PROVIDER_LABELS: dict[LlmProviderName, str] = {
    LlmProviderName.CLAUDE: "Claude",
    LlmProviderName.OPENAI: "OpenAI",
    LlmProviderName.GROQ: "Groq",
    LlmProviderName.GEMINI: "Gemini",
}
"""Human-readable provider names for the rejection message a user reads."""

_VALIDATION_TIMEOUT_SECONDS = 30.0
"""Ceiling on the save-time probe call. Well under the layer-wide 120s
default: a user is waiting on this request, and a provider that hasn't
answered a one-word prompt in 30 seconds isn't going to make this save
succeed."""


class _KeyValidationProbe(BaseModel):
    """The smallest schema `LLMClient.complete_structured` can be asked for.

    `complete_structured` is the LLM Provider Layer's only public call, so
    the save-time validation call goes through it like every other caller
    rather than reaching past it to a provider. One short string field keeps
    the response — and therefore the cost of validating a key — as close to
    nothing as this layer allows.
    """

    ok: str


_KEY_VALIDATION_PROMPT = (
    'Reply with the single word "yes" as the value of the "ok" field.'
)


class UserError(Exception):
    """Typed User Service failure carrying a shared `ErrorCode`, which the
    API layer maps to an HTTP status (see
    docs/architecture/api-contracts.md#user-service).
    """

    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class UserService:
    """Orchestrates UserRepository / UserPreferencesRepository on behalf of
    users/api/.
    """

    def __init__(
        self,
        users: UserRepository,
        preferences: UserPreferencesRepository,
        llm_configs: UserLlmConfigRepository | None = None,
        skill_prompts: SkillPromptRepository | None = None,
    ) -> None:
        self._users = users
        self._preferences = preferences
        self._llm_configs = llm_configs
        self._skill_prompts = skill_prompts

    async def create_user(self, request: CreateUserRequest) -> User:
        email = request.email.strip().lower()
        if not _EMAIL_PATTERN.match(email):
            raise UserError(ErrorCode.VALIDATION_ERROR, "email is not a valid address")

        display_name = request.display_name.strip()
        if not display_name:
            raise UserError(ErrorCode.VALIDATION_ERROR, "display_name must not be empty")

        if await self._users.get_by_email(email) is not None:
            raise UserError(ErrorCode.VALIDATION_ERROR, "email is already registered")

        if len(request.password) < 8:
            raise UserError(ErrorCode.VALIDATION_ERROR, "password must be at least 8 characters")

        tz = request.timezone.strip() if request.timezone else None

        return await self._users.add(
            User(
                id=UserId(uuid4()),
                email=email,
                display_name=display_name,
                created_at=datetime.now(UTC),
                timezone=tz or None,
            ),
            hash_password(request.password),
        )

    async def login(self, request: LoginRequest) -> User:
        """Verify email+password, returning the matching `User`.

        Raises `UserError(UNAUTHORIZED, ...)` with the SAME message for a
        nonexistent email and a wrong password — never reveal which one
        failed (standard practice, and matches
        `infrastructure.auth.errors.AuthError`'s reasoning for the same
        failure class).
        """
        email = request.email.strip().lower()
        found = await self._users.get_by_email_with_hash(email)
        if found is None:
            raise UserError(ErrorCode.UNAUTHORIZED, "invalid email or password")

        user, password_hash = found
        if not verify_password(request.password, password_hash):
            raise UserError(ErrorCode.UNAUTHORIZED, "invalid email or password")

        return user

    async def get_preferences(self, user_id: UserId) -> UserPreferences:
        preferences = await self._preferences.get(user_id)
        if preferences is None:
            raise UserError(ErrorCode.NOT_FOUND, "no preferences exist for this user")
        return preferences

    async def replace_preferences(
        self, user_id: UserId, request: UpdateUserPreferencesRequest
    ) -> UserPreferences:
        if await self._users.get(user_id) is None:
            raise UserError(ErrorCode.NOT_FOUND, "user does not exist")

        if request.min_salary is not None and request.min_salary < 0:
            raise UserError(ErrorCode.VALIDATION_ERROR, "min_salary must not be negative")

        currency = request.salary_currency.strip().upper() if request.salary_currency else None
        if currency is not None and not _CURRENCY_PATTERN.match(currency):
            raise UserError(
                ErrorCode.VALIDATION_ERROR,
                "salary_currency must be a 3-letter ISO 4217 code",
            )

        existing = await self._preferences.get(user_id)
        return await self._preferences.upsert(
            UserPreferences(
                id=existing.id if existing else UserPreferencesId(uuid4()),
                user_id=user_id,
                target_roles=_clean(request.target_roles),
                target_locations=_clean(request.target_locations),
                remote_preference=request.remote_preference,
                excluded_companies=_clean(request.excluded_companies),
                min_salary=request.min_salary,
                salary_currency=currency,
                updated_at=datetime.now(UTC),
            )
        )

    # ------------------------------------------------------------------
    # LLM provider configuration (docs/llm-provider-config/TECH.md Task 6)
    # ------------------------------------------------------------------

    async def get_llm_config(self, user_id: UserId) -> UserLlmConfig:
        """Return the user's saved provider configuration, key masked.

        "Unset" is a `NOT_FOUND`, exactly like `get_preferences` — the
        frontend already treats a 404 from a `/users/me/*` read as "nothing
        configured yet" rather than as an error.
        """
        config = await self._require_llm_configs().get(user_id)
        if config is None:
            raise UserError(
                ErrorCode.NOT_FOUND, "no LLM provider configuration exists for this user"
            )
        return config

    async def replace_llm_config(
        self, user_id: UserId, request: UpdateUserLlmConfigRequest
    ) -> UserLlmConfig:
        """Validate the submitted key against the live provider, then store it.

        Full-replace semantics, matching `replace_preferences`: provider,
        model, and key are all written from the request, so this is never a
        partial patch.

        The ordering is the point of this method — one real provider call
        happens *before* anything is persisted, so a typo'd or expired key is
        caught at save time (docs/llm-provider-config/PRODUCT.md, "Key
        Validation") instead of surfacing minutes later inside a pipeline.
        A rejected key leaves any previously-stored configuration untouched.
        """
        repository = self._require_llm_configs()

        if await self._users.get(user_id) is None:
            raise UserError(ErrorCode.NOT_FOUND, "user does not exist")

        api_key = request.api_key.strip()
        if not api_key:
            raise UserError(ErrorCode.VALIDATION_ERROR, "api_key must not be empty")

        # An empty/whitespace override means "use this provider's default
        # model", which `build_user_llm_config` expresses as None.
        model = request.model.strip() if request.model else None
        model = model or None

        await self._validate_llm_key(request.provider, model, api_key)

        now = datetime.now(UTC)
        return await repository.upsert(
            user_id,
            request.provider,
            model,
            api_key,
            last_validated_at=now,
            updated_at=now,
        )

    async def _validate_llm_key(
        self, provider: LlmProviderName, model: str | None, api_key: str
    ) -> None:
        """Make one minimal live call with the submitted key.

        The config is built by `build_user_llm_config` — the same function
        `get_user_llm_client` uses — so what gets validated here is exactly
        the provider/model resolution that later real calls will use, not a
        lookalike.

        Two deliberate narrowings on top of it: `max_attempts=1`, because a
        rejected key is not going to be accepted on a retry and the user is
        waiting; and a 30s timeout. The structured-output repair budget is
        left at its default, so a provider that answers but formats its one
        field badly gets the same second chance it would get in production
        rather than costing the user a false rejection.

        `complete_structured` is synchronous (it sleeps between transport
        retries), so it runs via `asyncio.to_thread` — the same way
        `profiles.parsing.extract_profile_fields` is called from async code.
        """
        config = build_user_llm_config(provider, model, api_key).model_copy(
            update={
                "max_attempts": 1,
                "timeout_seconds": _VALIDATION_TIMEOUT_SECONDS,
            }
        )
        client = LLMClient(config=config)

        try:
            await asyncio.to_thread(
                client.complete_structured, _KEY_VALIDATION_PROMPT, _KeyValidationProbe
            )
        except LLMProviderError as error:
            label = _PROVIDER_LABELS.get(provider, provider.value)
            # `error.message` names the provider/model and the failure
            # reason; it never contains the submitted key (no provider in
            # this layer puts one in an exception), so the reason is safe to
            # surface and is what makes a wrong-provider paste diagnosable.
            raise UserError(
                ErrorCode.VALIDATION_ERROR,
                f"This key was rejected by {label} ({error.reason.value}) — "
                "check it and try again",
            ) from error
        except ValueError as error:
            # A provider constructor rejecting the key shape before any
            # request goes out (e.g. `AnthropicProvider` with an empty key).
            label = _PROVIDER_LABELS.get(provider, provider.value)
            raise UserError(
                ErrorCode.VALIDATION_ERROR,
                f"This key was rejected by {label} — check it and try again",
            ) from error

    # ------------------------------------------------------------------
    # Per-skill prompt customization (docs/prompt-customization/TECH.md
    # Task 2)
    # ------------------------------------------------------------------

    async def get_skill_prompts(self, user_id: UserId) -> dict[str, str]:
        """Return the user's saved per-skill instructions, or `{}`.

        Deliberately not a `NOT_FOUND` when unset, unlike `get_llm_config`:
        "this user customized nothing" and "this user customized nothing
        *yet*" are the same state to every consumer, and `{}` says it
        exactly. The Settings form renders empty text areas from it with no
        special case, and the prompt builders treat an absent entry the
        same way they treat an empty one.
        """
        saved = await self._require_skill_prompts().get(user_id)
        return dict(saved.instructions) if saved is not None else {}

    async def replace_skill_prompts(
        self, user_id: UserId, request: UpdateSkillPromptsRequest
    ) -> dict[str, str]:
        """Replace the user's whole instructions map.

        Two normalizations before storing, both of which make "cleared" and
        "never set" indistinguishable downstream — which is what
        docs/prompt-customization/PRODUCT.md promises ("Saving an empty box
        after previously having text clears the customization ... there is
        no separate disable toggle needed"):

        - each value is stripped, so trailing newlines a textarea collects
          never reach a prompt;
        - an entry that is empty after stripping is dropped entirely rather
          than stored as `""`, so a cleared skill leaves no row content at
          all and `get_skill_customization` has one shape to recognize
          rather than two.

        The request schema has already rejected unknown keys and over-long
        values, so nothing here needs to police content — per PRODUCT.md,
        *what* a user asks for is deliberately unrestricted; the guarantee
        is that asking for something unsafe simply does not work, which the
        prompt append point enforces, not this method.
        """
        repository = self._require_skill_prompts()

        if await self._users.get(user_id) is None:
            raise UserError(ErrorCode.NOT_FOUND, "user does not exist")

        instructions = {
            key: stripped
            for key, value in request.instructions.items()
            if (stripped := value.strip())
        }
        saved = await repository.upsert(user_id, instructions, datetime.now(UTC))
        return dict(saved.instructions)

    def _require_skill_prompts(self) -> SkillPromptRepository:
        """Same optional-collaborator reasoning as `_require_llm_configs`:
        the repository is optional on `__init__` so existing two- and
        three-repository constructions keep working, and any route that
        needs it is always wired with one by
        `users.api.dependencies.get_user_service`.
        """
        if self._skill_prompts is None:
            raise RuntimeError(
                "UserService was constructed without a SkillPromptRepository"
            )
        return self._skill_prompts

    def _require_llm_configs(self) -> UserLlmConfigRepository:
        """`llm_configs` is optional on `__init__` so the many existing
        two-repository constructions of this service keep working; a route
        that actually needs it is always wired with one by
        `users.api.dependencies.get_user_service`. Reaching here means a
        miswiring, not a user error.
        """
        if self._llm_configs is None:
            raise RuntimeError(
                "UserService was constructed without a UserLlmConfigRepository"
            )
        return self._llm_configs


def _clean(values: list[str]) -> list[str]:
    """Free-text list fields — trim and drop blanks, never interpret. Role
    and location vocabularies vary by profession and are deliberately not
    validated against any fixed set.
    """
    return [stripped for value in values if (stripped := value.strip())]


__all__ = ["UserError", "UserService"]
