"""Structural protocols for the underlying broker clients.

``confluent_kafka.Producer`` / ``confluent_kafka.Consumer`` satisfy these
structurally, and so does the in-memory broker in ``in_memory.py``. Because
``EventProducer`` and ``EventConsumer`` depend only on these protocols, the
same wrapper code — including retry and dead-letter handling — runs in tests
and in production.
"""

from collections.abc import Sequence
from typing import Any, Protocol, runtime_checkable

Headers = Sequence[tuple[str, bytes]]


@runtime_checkable
class Message(Protocol):
    """The subset of ``confluent_kafka.Message`` the consumer wrapper uses."""

    def error(self) -> Any | None: ...
    def value(self) -> bytes | None: ...
    def key(self) -> bytes | None: ...
    def topic(self) -> str | None: ...
    def partition(self) -> int | None: ...
    def offset(self) -> int | None: ...


@runtime_checkable
class ProducerClient(Protocol):
    def produce(
        self,
        topic: str,
        value: bytes | None = None,
        key: bytes | None = None,
        headers: Headers | None = None,
    ) -> None: ...

    def poll(self, timeout: float) -> int: ...

    def flush(self, timeout: float) -> int: ...


@runtime_checkable
class ConsumerClient(Protocol):
    def subscribe(self, topics: list[str]) -> None: ...

    def poll(self, timeout: float) -> Message | None: ...

    def commit(self, message: Message | None = None, asynchronous: bool = True) -> Any: ...

    def close(self) -> None: ...


__all__ = ["ConsumerClient", "Headers", "Message", "ProducerClient"]
