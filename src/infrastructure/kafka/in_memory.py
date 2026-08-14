"""In-memory fake broker satisfying ``ProducerClient``/``ConsumerClient``.

Exists so ``EventProducer`` and ``EventConsumer`` can be tested end to end
(publish -> serialize -> consume -> deserialize -> retry -> DLQ) without a
running Kafka broker, per client.py's docstring ("the in-memory broker in
in_memory.py"). Every business component's own tests may reuse this instead
of hand-rolling a fake broker per component.

One partition per topic is enough to fake correctness of the properties this
layer actually guarantees: FIFO delivery within a topic (per-partition
ordering — kafka-topics.md) and independent offsets per consumer group
(multiple consumer groups on one topic — kafka-topics.md). It does not model
multi-partition rebalancing; components don't need that to unit test their
handler logic.
"""

from dataclasses import dataclass, field

from infrastructure.kafka.client import Headers


@dataclass
class FakeMessage:
    """The subset of ``confluent_kafka.Message`` the consumer wrapper reads."""

    _topic: str
    _value: bytes | None
    _key: bytes | None
    _offset: int
    _headers: Headers | None = None
    _partition: int = 0
    _error: object | None = None

    def error(self) -> object | None:
        return self._error

    def value(self) -> bytes | None:
        return self._value

    def key(self) -> bytes | None:
        return self._key

    def topic(self) -> str | None:
        return self._topic

    def partition(self) -> int | None:
        return self._partition

    def offset(self) -> int | None:
        return self._offset

    def headers(self) -> Headers | None:
        return self._headers


@dataclass
class InMemoryBroker:
    """Shared state behind a set of fake producer/consumer clients.

    One ``InMemoryBroker`` instance stands in for a whole cluster within a
    test: construct it once, hand ``InMemoryProducerClient``/
    ``InMemoryConsumerClient`` instances backed by it to whichever
    ``EventProducer``/``EventConsumer`` the test wires up.
    """

    _logs: dict[str, list[FakeMessage]] = field(default_factory=dict)
    _group_offsets: dict[tuple[str, str], int] = field(default_factory=dict)

    def append(
        self,
        topic: str,
        value: bytes | None,
        key: bytes | None,
        headers: Headers | None,
    ) -> None:
        log = self._logs.setdefault(topic, [])
        log.append(FakeMessage(topic, value, key, len(log), headers))

    def poll(self, topic: str, group_id: str) -> FakeMessage | None:
        log = self._logs.get(topic, [])
        position = self._group_offsets.get((topic, group_id), 0)
        if position >= len(log):
            return None
        return log[position]

    def commit(self, topic: str, group_id: str, next_offset: int) -> None:
        self._group_offsets[(topic, group_id)] = next_offset

    def log(self, topic: str) -> list[FakeMessage]:
        """All messages ever produced to ``topic``, for test assertions."""
        return list(self._logs.get(topic, []))


class InMemoryProducerClient:
    """``ProducerClient`` backed by an ``InMemoryBroker``."""

    def __init__(self, broker: InMemoryBroker) -> None:
        self._broker = broker

    def produce(
        self,
        topic: str,
        value: bytes | None = None,
        key: bytes | None = None,
        headers: Headers | None = None,
    ) -> None:
        self._broker.append(topic, value, key, headers)

    def poll(self, timeout: float) -> int:
        return 0

    def flush(self, timeout: float) -> int:
        return 0


class InMemoryConsumerClient:
    """``ConsumerClient`` backed by an ``InMemoryBroker``.

    Each instance has its own ``group_id``, matching the "one consumer group
    per topic per consuming component" convention (kafka-topics.md) — two
    ``InMemoryConsumerClient``s on the same broker with different
    ``group_id``s read the same log independently, exactly like two real
    consumer groups.
    """

    def __init__(self, broker: InMemoryBroker, group_id: str) -> None:
        self._broker = broker
        self._group_id = group_id
        self._topics: list[str] = []

    def subscribe(self, topics: list[str]) -> None:
        self._topics = list(topics)

    def poll(self, timeout: float) -> FakeMessage | None:
        for topic in self._topics:
            message = self._broker.poll(topic, self._group_id)
            if message is not None:
                return message
        return None

    def commit(self, message: FakeMessage | None = None, asynchronous: bool = True) -> None:
        if message is None or message.topic() is None:
            return
        self._broker.commit(message.topic(), self._group_id, message.offset() + 1)

    def close(self) -> None:
        pass


__all__ = [
    "FakeMessage",
    "InMemoryBroker",
    "InMemoryConsumerClient",
    "InMemoryProducerClient",
]
