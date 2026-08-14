"""Email/LinkedIn send provider client wrapper.

Consumed by Outreach Service's send worker. This layer is a transport only:
it dispatches an already-composed message on an already-chosen channel to
the provider registered for that channel.

It does not choose the channel, compose the message, pick the recipient, or
decide whether sending is allowed. The human-approval gate
(`OUTREACH_GENERATED -> OUTREACH_APPROVED`, see
docs/architecture/state-machines.md#outreach-lifecycle) is enforced by
Outreach Service *before* this client is ever called; nothing here weakens
it, and nothing here may be used to bypass it.
"""

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Protocol

from pydantic import BaseModel

from infrastructure.external.config import ExternalClientConfig
from infrastructure.external.errors import MessageSendError
from infrastructure.external.resilience import RateLimiter, call_with_resilience
from shared.types.enums import OutreachChannel


class OutboundMessage(BaseModel):
    """An approved message, ready for transport."""

    channel: OutreachChannel
    recipient: str
    body: str
    subject: str | None = None


class MessageSendReceipt(BaseModel):
    """Confirmation of a successful send. `external_message_id` is what
    Outreach Service persists on its `Outreach` record.
    """

    external_message_id: str
    channel: OutreachChannel
    provider: str
    sent_at: datetime


class MessageSendProvider(Protocol):
    """A concrete send backend (SMTP, a LinkedIn client, ...)."""

    name: str

    async def send(
        self, message: OutboundMessage, *, timeout_seconds: float
    ) -> str: ...


class RecordingMessageSendProvider:
    """Provider that records messages instead of transmitting them.

    The safe local-development and test default: outreach can be exercised
    end to end without anything reaching a real recipient.
    """

    def __init__(self, *, name: str = "recording") -> None:
        self.name = name
        self.sent: list[OutboundMessage] = []

    async def send(self, message: OutboundMessage, *, timeout_seconds: float) -> str:
        self.sent.append(message)
        return f"{self.name}-{len(self.sent)}"


class MessageSendClient:
    """Dispatches an `OutboundMessage` to the provider registered for its
    channel, under the shared timeout, retry, and rate-limit policy.
    Failures surface as `MessageSendError` (`EXTERNAL_SEND_FAILED`).
    """

    def __init__(
        self,
        providers: Mapping[OutreachChannel, MessageSendProvider],
        config: ExternalClientConfig | None = None,
    ) -> None:
        self._providers = dict(providers)
        self._config = config or ExternalClientConfig()
        self._rate_limiter = RateLimiter(self._config.min_interval_seconds)

    async def send(self, message: OutboundMessage) -> MessageSendReceipt:
        provider = self._providers.get(message.channel)
        if provider is None:
            raise MessageSendError(
                f"no send provider registered for channel {message.channel.value}",
                retryable=False,
            )

        async def operation() -> str:
            return await provider.send(
                message, timeout_seconds=self._config.timeout_seconds
            )

        external_message_id = await call_with_resilience(
            operation,
            config=self._config,
            error_type=MessageSendError,
            provider=provider.name,
            description=f"send on {message.channel.value}",
            rate_limiter=self._rate_limiter,
        )
        return MessageSendReceipt(
            external_message_id=external_message_id,
            channel=message.channel,
            provider=provider.name,
            sent_at=datetime.now(UTC),
        )


__all__ = [
    "MessageSendClient",
    "MessageSendProvider",
    "MessageSendReceipt",
    "OutboundMessage",
    "RecordingMessageSendProvider",
]
