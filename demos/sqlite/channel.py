"""Bounded in-process live tail, independent readers and explicit cursor gaps.

A disconnected reader never changes producer ownership. The host closes the
channel after the producer settles; a missing terminal is recovered via SQLite.
"""

import threading
from collections import deque
from dataclasses import dataclass, replace

from demos.sqlite.constants import CHANNEL_CAPACITY, EventKind, Scenario
from demos.sqlite.events import DemoEvent
from ui_stream.exceptions import LiveUnavailable


@dataclass(frozen=True, slots=True)
class ChannelBatch:
    events: tuple[DemoEvent, ...]
    gap: bool = False
    closed: bool = False


class MemoryChannel:
    """Publish immutable events into one bounded execution tail under a condition lock."""

    def __init__(self, capacity: int = CHANNEL_CAPACITY):
        self._events: deque[DemoEvent] = deque(maxlen=capacity)
        self._condition = threading.Condition()
        self._cursor = 0
        self._closed = False

    def publish(self, event: DemoEvent) -> DemoEvent:
        with self._condition:
            if self._closed:
                raise LiveUnavailable("Demo live channel is closed")
            self._cursor += 1
            published = replace(event, cursor=self._cursor)
            self._events.append(published)
            self._condition.notify_all()
            return published

    def read(self, after: int, *, timeout: float = 1) -> ChannelBatch:
        with self._condition:
            self._condition.wait_for(lambda: self._closed or self._cursor > after, timeout=timeout)
            if after > self._cursor or (self._events and after < self._events[0].cursor - 1):
                return ChannelBatch((), gap=True, closed=self._closed)
            return ChannelBatch(tuple(item for item in self._events if item.cursor > after), closed=self._closed)

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._condition.notify_all()


class DemoPublisher:
    """Inject a declared live-outage scenario at the demo adapter boundary."""

    def __init__(self, channel: MemoryChannel, scenario: Scenario):
        self._channel = channel
        self._scenario = scenario

    def publish(self, event: DemoEvent) -> DemoEvent:
        if self._scenario == Scenario.LIVE_FAILURE and event.kind == EventKind.PROGRESS and 4 <= event.step <= 7:
            raise LiveUnavailable("Synthetic live outage at steps 4 through 7")
        return self._channel.publish(event)
