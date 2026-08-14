"""Tests for infrastructure.external.message_send.MessageSendClient.

Mocks at the MessageSendProvider protocol boundary. Verifies successful
send, correct provider routing by OutreachChannel, send-failure
normalization to EXTERNAL_SEND_FAILED, and that this layer performs zero
content-generation or approval-gate logic — it only transports whatever
OutboundMessage it is given.
"""

import pytest

from infrastructure.external.config import ExternalClientConfig, RetryPolicy
from infrastructure.external.errors import MessageSendError
from infrastructure.external.message_send import (
    MessageSendClient,
    MessageSendReceipt,
    OutboundMessage,
    RecordingMessageSendProvider,
)
from shared.types.enums import OutreachChannel

FAST_CONFIG = ExternalClientConfig(
    retry=RetryPolicy(max_attempts=2, initial_backoff_seconds=0.001, backoff_multiplier=1.0)
)


def _message(**overrides) -> OutboundMessage:
    defaults = {
        "channel": OutreachChannel.EMAIL,
        "recipient": "jamie@example.com",
        "body": "Hi Jamie, ...",
        "subject": "Referral opportunity",
    }
    defaults.update(overrides)
    return OutboundMessage(**defaults)


@pytest.mark.asyncio
async def test_send_returns_receipt_on_success():
    provider = RecordingMessageSendProvider(name="smtp")
    client = MessageSendClient({OutreachChannel.EMAIL: provider}, FAST_CONFIG)

    receipt = await client.send(_message())

    assert isinstance(receipt, MessageSendReceipt)
    assert receipt.channel == OutreachChannel.EMAIL
    assert receipt.provider == "smtp"
    assert receipt.external_message_id == "smtp-1"
    assert receipt.sent_at is not None
    assert provider.sent == [_message()]


@pytest.mark.asyncio
async def test_send_routes_to_the_provider_registered_for_the_channel():
    email_provider = RecordingMessageSendProvider(name="smtp")
    linkedin_provider = RecordingMessageSendProvider(name="linkedin")
    client = MessageSendClient(
        {
            OutreachChannel.EMAIL: email_provider,
            OutreachChannel.LINKEDIN_MESSAGE: linkedin_provider,
        },
        FAST_CONFIG,
    )

    await client.send(_message(channel=OutreachChannel.LINKEDIN_MESSAGE, subject=None))

    assert len(linkedin_provider.sent) == 1
    assert email_provider.sent == []


@pytest.mark.asyncio
async def test_send_raises_non_retryable_error_when_no_provider_registered_for_channel():
    client = MessageSendClient({OutreachChannel.EMAIL: RecordingMessageSendProvider()}, FAST_CONFIG)

    with pytest.raises(MessageSendError) as exc_info:
        await client.send(_message(channel=OutreachChannel.LINKEDIN_CONNECTION_REQUEST, subject=None))

    assert exc_info.value.retryable is False


@pytest.mark.asyncio
async def test_send_normalizes_provider_failure_to_external_send_failed():
    class _FailingProvider:
        name = "broken-smtp"

        async def send(self, message: OutboundMessage, *, timeout_seconds: float) -> str:
            raise ConnectionError("smtp connection refused")

    client = MessageSendClient({OutreachChannel.EMAIL: _FailingProvider()}, FAST_CONFIG)

    with pytest.raises(MessageSendError) as exc_info:
        await client.send(_message())

    assert exc_info.value.provider == "broken-smtp"
    assert exc_info.value.retryable is True


@pytest.mark.asyncio
async def test_send_retries_then_succeeds():
    call_count = {"n": 0}

    class _FlakyProvider:
        name = "flaky-smtp"

        async def send(self, message: OutboundMessage, *, timeout_seconds: float) -> str:
            call_count["n"] += 1
            if call_count["n"] < 2:
                raise TimeoutError("slow provider")
            return "flaky-external-id"

    client = MessageSendClient({OutreachChannel.EMAIL: _FlakyProvider()}, FAST_CONFIG)

    receipt = await client.send(_message())

    assert call_count["n"] == 2
    assert receipt.external_message_id == "flaky-external-id"


@pytest.mark.asyncio
async def test_send_performs_no_content_generation_message_passed_through_unchanged():
    # The body/subject the caller supplies must reach the provider verbatim
    # — this layer never composes or edits outreach content.
    provider = RecordingMessageSendProvider()
    client = MessageSendClient({OutreachChannel.EMAIL: provider}, FAST_CONFIG)
    message = _message(body="Exact approved wording.", subject="Exact subject")

    await client.send(message)

    assert provider.sent[0].body == "Exact approved wording."
    assert provider.sent[0].subject == "Exact subject"


@pytest.mark.asyncio
async def test_recording_provider_increments_ids_per_send():
    provider = RecordingMessageSendProvider(name="rec")

    first = await provider.send(_message(), timeout_seconds=1.0)
    second = await provider.send(_message(), timeout_seconds=1.0)

    assert first == "rec-1"
    assert second == "rec-2"
    assert len(provider.sent) == 2
