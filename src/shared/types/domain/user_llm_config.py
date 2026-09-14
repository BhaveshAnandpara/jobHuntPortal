"""UserLlmConfig — the LLM provider each user brings their own credentials
for. One active row per user, mirroring `user_preferences`. See
docs/llm-provider-config/TECH.md.

Ownership: User Service. Modifiable by User Service only.

**This type never carries a usable API key.** The same reasoning as
`password_hash` being kept off `User` (see `users/repository.py`'s
`get_by_email_with_hash` docstring): a general-purpose domain object that
flows up into services, API responses, logs, and event payloads must not be
able to leak the secret. Only `has_key` (is one stored at all?) and
`key_hint` (a masked tail, for "is this the key I think it is?" in the UI)
travel with it. The decrypted value is reachable only through
`UserLlmConfigRepository.get_decrypted_api_key`.
"""

from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel

from shared.types.ids import UserId


class LlmProviderName(str, Enum):
    """The four user-selectable providers.

    Values match the `provider` strings `infrastructure.llm.config.LLMConfig`
    already understands (`groq`, `gemini`), so a stored row maps onto an
    `LLMConfig` without a translation table.

    Ollama is deliberately absent — it needs a locally-running model server,
    which is meaningless for a user of a hosted deployment (see
    docs/llm-provider-config/PRODUCT.md).

    Named `LlmProviderName`, not `LLMProvider`, to stay distinct from the
    `LLMProvider` *protocol* in `infrastructure/llm/provider.py`.
    """

    CLAUDE = "claude"
    OPENAI = "openai"
    GROQ = "groq"
    GEMINI = "gemini"


class UserLlmConfig(BaseModel):
    """A user's saved LLM provider configuration, safe to return anywhere.

    `id` is a plain `UUID` rather than a `NewType` from `shared.types.ids`:
    that module is a locked contract listing the system's canonical
    entities, and this row is a per-user settings record (like
    `user_preferences`' own row) rather than a new first-class entity.
    """

    id: UUID
    user_id: UserId  # one active row per user
    provider: LlmProviderName
    model: str | None = None  # optional per-provider model override
    has_key: bool = False
    """Whether an encrypted key is stored. False only if the row predates a
    key or the stored ciphertext is empty."""

    key_hint: str | None = None
    """Masked tail of the stored key (e.g. `...ab12`) — enough to recognize
    which key is saved, never enough to use it. None when no key is stored
    or the stored ciphertext can no longer be decrypted."""

    last_validated_at: datetime | None = None
    """When the key last succeeded against a live provider call. None means
    "never validated" — treated as not-configured by the gating checks."""

    updated_at: datetime


__all__ = ["LlmProviderName", "UserLlmConfig"]
